#!/usr/bin/env python3
"""音声 -> 文字（faster-whisper）。将来の VLM の入力を作る。

    PC-C/PC-B ── OME ──> ここ（受信も文字起こしも 1 プロセス）──> 書き起こし

**VLM はこのファイルの `Transcriber` を import して使う想定。**
`text(seconds)` が「直近 N 秒の書き起こし」を返すので、それをプロンプトに
入れる。実装が済むまでの間は、このファイル単体を動かして
（`run.sh` がそうする）経路が生きていることを確認できる。

以前は「GStreamer が要る受信側」と「GPU が要る文字起こし側」を別プロセスに
分け、間を TCP で繋いでいた ── focal の `python3-gi` が 3.8 用しか無く、
faster-whisper が 3.8 に入らなかったため。pyenv でビルドした素の
Python 3.10 に PyGObject を入れることで、**同じ Python で両方が動くように
なった**ので 1 本にまとめてある。gi(webrtcbin) と CUDA を同一プロセスで
同時に働かせても落ちないことは実測で確かめた（受信 9,076 buf と推論
1,018 回を 3 分間同時に回して無事）。

## 窓の取り方（2 つあり、別物として設定する）

1. **切り出しの窓**（`ASR_*` の silence / min_speech / max_segment）
   音声をどこで区切って whisper に渡すか。**固定長で切らない。** 固定長だと
   語の途中で切れて、境目で欠落や重複が出る。無音で区切り、長すぎる発話だけ
   `ASR_MAX_SEGMENT_SEC` で強制的に切る。
2. **文脈の窓**（`ASR_CONTEXT_SEC`、既定 15 秒）
   VLM に見せる書き起こしの長さ。頭を誰に向けるかの判断材料なので、
   **直近の 2〜4 ターンぶんあれば足りる**（会話の 1 ターンは 2〜8 秒）。
   長くすると、既に終わった話題の話者に引きずられるうえ、トークンだけ増える。

この 2 つを混ぜないこと。1 は音の切れ目、2 は判断に要る文脈の長さで、
決める理由がまったく別。VLM を繋いだら 2 だけを動かして調整する。

**発話は音源ごとに分けて持つ**（`mic` = 現場、`operator` = 操作者）。
「操作者が今なにか聞いた」のか「来場者が喋った」のかで向く先が変わるので、
混ぜると判断に使えない。
"""

import json
import os
import sys
import threading
import time
from collections import deque

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from recv_ome import OmeInputs  # noqa: E402

# whisper の入力仕様。**設定ではないので config.env には置かない**
# （変えるとモデルが正しく動かない）。gst にこの形へ変換させて受ける。
RATE = 16000
CHANNELS = 1
CAPS = f"audio/x-raw,format=S16LE,rate={RATE},channels={CHANNELS}"

FRAME_MS = 20                         # VAD をかける粒度（内部の刻み。設定ではない）


def env(k, d=None):
    if d is None:
        try:
            return os.environ[k]
        except KeyError:
            raise RuntimeError(
                f"{k} が未設定。env.sh を読まずに起動している"
                f"（common/config.env か pc-d-server/config.env が設定する）"
            ) from None
    return os.environ.get(k, d)


def log(level, text):
    print(f"[{level}] [asr]: {text}", flush=True)


class Utterance:
    __slots__ = ("source", "text", "t_start", "t_end")

    def __init__(self, source, text, t_start, t_end):
        self.source = source
        self.text = text
        self.t_start = t_start
        self.t_end = t_end

    def as_dict(self):
        return {"source": self.source, "text": self.text,
                "t_start": round(self.t_start, 3), "t_end": round(self.t_end, 3)}

    def __repr__(self):
        return f"<{self.source} {self.t_end - self.t_start:.1f}s {self.text!r}>"


class _SourceBuffer:
    """1 音源ぶんの溜め込みと、無音による発話の切り出し。

    エネルギー（RMS）で喋っているかを見る。**閾値は固定しない** ── 現場の
    暗騒音はマイクと会場で桁が変わるので、静かな側の分位点を暗騒音として
    追い、その何倍かで喋りと判定する。絶対値で決め打ちすると、静かな部屋では
    何も拾わず、うるさい会場では切れ目が見つからなくなる。
    """

    def __init__(self, silence_sec, min_speech_sec, max_segment_sec, floor_k=3.0):
        self.silence_sec = silence_sec
        self.min_speech_sec = min_speech_sec
        self.max_segment_sec = max_segment_sec
        self.floor_k = floor_k

        self.pcm = bytearray()
        self.t0 = None                      # pcm の先頭サンプルの unix 時刻
        self.speech_sec = 0.0
        self.silence_run = 0.0
        self._rms_hist = deque(maxlen=250)  # 直近 5 秒ぶんの 20 ms フレーム
        self._pending = bytearray()         # フレーム長に満たない端数

    @staticmethod
    def _rms(buf):
        # int16 のまま。numpy を使わないのは、こちら側の依存を
        # faster-whisper だけに保つため（音声 2 本 × 50 fps なら十分速い）。
        n = len(buf) // 2
        if n == 0:
            return 0.0
        total = 0
        for i in range(0, n * 2, 2):
            v = buf[i] | (buf[i + 1] << 8)
            if v >= 32768:
                v -= 65536
            total += v * v
        return (total / n) ** 0.5

    def add(self, pcm, unix_ns):
        """PCM を足し、発話が切れたら (bytes, t_start, t_end) を返す。まだなら None。"""
        if self.t0 is None:
            self.t0 = unix_ns / 1e9
        self.pcm += pcm
        self._pending += pcm

        frame_bytes = RATE * FRAME_MS // 1000 * 2
        while len(self._pending) >= frame_bytes:
            frame = self._pending[:frame_bytes]
            del self._pending[:frame_bytes]
            rms = self._rms(frame)
            self._rms_hist.append(rms)

            floor = self._noise_floor()
            if rms > max(floor * self.floor_k, 50.0):
                self.speech_sec += FRAME_MS / 1000
                self.silence_run = 0.0
            else:
                self.silence_run += FRAME_MS / 1000

        dur = len(self.pcm) / 2 / RATE
        ended = (self.speech_sec >= self.min_speech_sec
                 and self.silence_run >= self.silence_sec)
        if ended or dur >= self.max_segment_sec:
            if self.speech_sec < self.min_speech_sec:
                self._reset()               # 無音だけ溜まった。捨てる
                return None
            return self._cut()
        return None

    def _noise_floor(self):
        if len(self._rms_hist) < 25:
            return 0.0
        s = sorted(self._rms_hist)
        return s[len(s) // 10]              # 10 パーセンタイル

    def _cut(self):
        seg = bytes(self.pcm)
        t_start = self.t0
        t_end = t_start + len(seg) / 2 / RATE
        self._reset()
        return seg, t_start, t_end

    def _reset(self):
        self.pcm = bytearray()
        self.t0 = None
        self.speech_sec = 0.0
        self.silence_run = 0.0


class Transcriber:
    """音声を受けて、直近の書き起こしを持つ。**VLM はこれを import して使う。**

        tr = Transcriber()
        ...                                   # feed() は受信スレッドが呼ぶ
        prompt_part = tr.text()               # 直近 ASR_CONTEXT_SEC 秒
    """

    def __init__(self, model_size=None, device=None, compute_type=None,
                 language=None, context_sec=None, on_utterance=None):
        """on_utterance:
        発話が 1 つ確定するたびに `fn(Utterance)` で呼ばれる。**受信側では
        なくワーカースレッドから呼ばれる** ── 文字起こしは非同期なので、
        feed() が返った時点ではまだ結果が無い。
        """
        from faster_whisper import WhisperModel      # 起動時間が長いので遅延 import

        self.model_size = model_size or env("ASR_MODEL")
        self.device = device or env("ASR_DEVICE")
        self.compute_type = compute_type or env("ASR_COMPUTE_TYPE")
        self.language = language or env("ASR_LANGUAGE")
        self.context_sec = float(context_sec or env("ASR_CONTEXT_SEC"))
        self.on_utterance = on_utterance

        self._seg_args = dict(
            silence_sec=float(env("ASR_SILENCE_SEC")),
            min_speech_sec=float(env("ASR_MIN_SPEECH_SEC")),
            max_segment_sec=float(env("ASR_MAX_SEGMENT_SEC")),
        )
        self._bufs = {}
        self._utts = deque(maxlen=200)
        self._lock = threading.Lock()
        self._queue = deque()
        self._wake = threading.Event()
        self.n_seg = 0
        self.busy_sec = 0.0

        log("INFO", f"モデルを読む: {self.model_size} / {self.device} / {self.compute_type}")
        t0 = time.time()
        self.model = WhisperModel(self.model_size, device=self.device,
                                  compute_type=self.compute_type)
        log("INFO", f"読み込み完了 {time.time() - t0:.1f}s（言語 {self.language}、"
                    f"文脈窓 {self.context_sec:.0f}s）")

        threading.Thread(target=self._worker, daemon=True).start()

    # ---- 入口 ----

    def feed(self, source, pcm, unix_ns):
        """受信スレッドから呼ばれる。**ここでは推論しない**（受信が詰まるため）。"""
        buf = self._bufs.get(source)
        if buf is None:
            buf = self._bufs[source] = _SourceBuffer(**self._seg_args)
        cut = buf.add(pcm, unix_ns)
        if cut is not None:
            seg, t_start, t_end = cut
            self._queue.append((source, seg, t_start, t_end))
            self._wake.set()

    # ---- 出口（VLM 側が使う）----

    def recent(self, seconds=None):
        """直近 N 秒の Utterance。既定は ASR_CONTEXT_SEC。"""
        sec = self.context_sec if seconds is None else seconds
        cutoff = time.time() - sec
        with self._lock:
            return [u for u in self._utts if u.t_end >= cutoff]

    def text(self, seconds=None, with_time=False):
        """直近 N 秒を 1 つの文字列に。そのままプロンプトに入れられる形。"""
        lines = []
        for u in self.recent(seconds):
            if with_time:
                lines.append(f"[{time.strftime('%H:%M:%S', time.localtime(u.t_start))}]"
                             f" {u.source}: {u.text}")
            else:
                lines.append(f"{u.source}: {u.text}")
        return "\n".join(lines)

    # ---- 中身 ----

    def _worker(self):
        while True:
            self._wake.wait(0.2)
            self._wake.clear()
            while self._queue:
                source, seg, t_start, t_end = self._queue.popleft()
                try:
                    self._transcribe(source, seg, t_start, t_end)
                except Exception as e:
                    log("ERROR", f"文字起こしに失敗 ({source}): {e}")

    def _transcribe(self, source, seg, t_start, t_end):
        import numpy as np

        audio = np.frombuffer(seg, dtype=np.int16).astype(np.float32) / 32768.0
        t0 = time.time()
        segments, _info = self.model.transcribe(
            audio, language=self.language, beam_size=1,
            # 切り出し側でも無音は落としているが、発話の中に挟まる間は
            # ここで落とす。whisper は無音に対して幻聴を出しやすい。
            vad_filter=True,
            condition_on_previous_text=False,   # 前の発話に引きずられて繰り返すのを防ぐ
        )
        text = " ".join(s.text.strip() for s in segments).strip()
        took = time.time() - t0
        self.n_seg += 1
        self.busy_sec += took

        if not text:
            return
        u = Utterance(source, text, t_start, t_end)
        with self._lock:
            self._utts.append(u)
        dur = t_end - t_start
        log("INFO", f"{source} {dur:4.1f}s -> {took:4.2f}s ({dur / took:4.1f}x) : {text}")
        if self.on_utterance is not None:
            try:
                self.on_utterance(u)
            except Exception as e:
                log("WARN", f"on_utterance で例外: {e}")
        return u


def main():
    import signal

    here = os.path.dirname(os.path.abspath(__file__))
    jsonl = os.path.join(here, "log", "transcript.jsonl")
    os.makedirs(os.path.dirname(jsonl), exist_ok=True)

    # 確定した発話を 1 行 1 件で残す。**この系で唯一 PC-D に残る記録**
    # （映像や音声そのものは PC-B の bag にしか無い）。VLM を繋いだあと
    # 「そのとき何が聞こえていたか」を後から突き合わせるのに要る。
    def _append(u):
        with open(jsonl, "a") as f:
            f.write(json.dumps(u.as_dict(), ensure_ascii=False) + "\n")

    tr = Transcriber(on_utterance=_append)

    # 音声 2 本だけ受ける。映像はいま使わないので繋がない（無駄に復号しない）。
    # **16 kHz 単声道への変換は gst にやらせる**（CAPS）。whisper の入力が
    # その形なので、ここで揃えれば Python 側の resample が丸ごと要らない。
    inp = OmeInputs(only=["mic", "operator"], audio_caps=CAPS,
                    logger=lambda lv, m: log(lv.upper(), m))

    # 届いた形を音源ごとに 1 回だけ確かめる。レートが違っても例外は出ず、
    # **whisper が別の速さの音として読んだ「それらしい文字」が出る**という
    # 形で外れるので、気付けない。
    ok_by_key = {}

    def on_audio(key, a):
        ok = ok_by_key.get(key)
        if ok is None:
            ok = (a.rate == RATE and a.channels == CHANNELS)
            ok_by_key[key] = ok
            if ok:
                log("INFO", f"{key}: {a.rate} Hz {a.channels}ch を確認")
            else:
                log("ERROR", f"{key}: {a.rate} Hz {a.channels}ch で届いている。"
                             f"{RATE} Hz {CHANNELS}ch のはず ── audio_caps が"
                             f"効いていない。**この音源は使わない**")
        if ok:
            tr.feed(key, a.data, a.unix_ns)

    inp.add_audio_sink(on_audio)

    def _bye(signum, _frame):
        log("INFO", f"signal {signum} を受けた。終了する")
        os._exit(0)

    signal.signal(signal.SIGTERM, _bye)
    signal.signal(signal.SIGINT, _bye)

    inp.start()
    log("INFO", "音声 2 本を OME から受けて文字にする")

    while True:
        time.sleep(30.0)
        st = inp.stats()
        got = " ".join(
            f"{k}={st[k]['audio']}" for k in ("mic", "operator") if k in st)
        log("INFO", f"受信 {got} / 直近 {tr.context_sec:.0f}s の発話 "
                    f"{len(tr.recent())} 件 / 累計 {tr.n_seg} 区間・"
                    f"GPU {tr.busy_sec:.1f}s")


if __name__ == "__main__":
    main()
