#!/usr/bin/env python3
"""两路音频 → 文字（faster-whisper）。给 VLM 造输入。

    robot-pc ─┐                      ┌─ onboard  现场说了什么
              ├─ OME ─▶ recv.py ─▶ 这里
    tele-pc ──┘                      └─ operator 操作者说了什么

收流和转写在同一个进程里。**结果不在本机用** —— vlm-server 走
`GET /transcript` 拉走（架构 §3.1），HTTP 出口也在这个进程里。

单独跑（不给 vlm-server 用，只看转写）：`../run_asr.sh -- --no-http`。

## 两个窗，是两回事

1. **切分的窗**（`ASR_SILENCE_SEC` / `ASR_MIN_SPEECH_SEC` / `ASR_MAX_SEGMENT_SEC`）
   音频在哪里断开送给 whisper。**不按固定长度切** —— 固定长度会从词中间切断，
   接缝处出现丢字和重复。按静音切，只有一直说不停的才用 max 强制断开。
2. **上下文的窗**（`ASR_CONTEXT_SEC`）
   给 VLM 看多长的转写。判断的是「该朝谁转头」，**最近两三轮对话就够**
   （一轮 2～8 秒）。放长会被已经结束的话题的说话人带偏，token 也白涨。

前者是声音的断点，后者是判断所需的上下文长度，决定的理由完全不同，别混。

**上下文窗现在是 60 秒**（`ASR_CONTEXT_SEC`）。发话在内存里留 `ASR_KEEP_SEC`
（默认 600 秒），切多长是**读的时候**才决定的：

    tr.text()              # config.env 里那个窗
    tr.text(30)            # 这一次要 30 秒

改窗口就改 config.env 再重起。事后还能拿 transcript.jsonl 换别的值重放。

**★ 没人说话的时候，`text()` 就返回空字符串 —— 这是定下来的行为，别改。**
不要在这里塞「（无发话）」之类的占位符：要不要把静默这件事告诉 VLM、怎么措辞，
是**造 prompt 那一侧**的决定，不是转写这一侧的。这里只报告事实：这段时间里
没有识别出任何话。

**两路分开存。** 「操作者刚说了什么」和「来场者刚说了什么」决定的是朝谁转头，
混在一起就没法判断了。
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from recv import Inputs, env, log  # noqa: E402

# =====================================================================
# whisper 的输入规格。**这不是配置，不进 config.env** —— 改了模型就不工作。
# gst 按这个形状把音频转好再交上来（recv.py 的 audio_caps）。
# =====================================================================
RATE = 16000
CHANNELS = 1
CAPS = f"audio/x-raw,format=S16LE,rate={RATE},channels={CHANNELS}"

FRAME_MS = 20        # 判断有没有在说话的粒度。内部刻度，不是配置
REPORT_SEC = 30.0


class Utterance:
    """确定下来的一句话。"""

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


# 本底噪声的观测窗 [s]。**要比一次发话长得多。**
# 见 _SourceBuffer 的说明：短窗会被发话本身抬上去。
FLOOR_WIN_SEC = 30
FLOOR_PCTL = 5          # 取这个分位数当本底
FLOOR_MIN_FRAMES = 50   # 攒够 1 秒再信这个本底
FLOOR_EVERY = 25        # 每这么多帧重算一次（0.5 s。每帧都算是白烧 CPU）


class _SourceBuffer:
    """一路音源的攒数据 ＋ 按静音切句。

    用能量（RMS）判断在不在说话。**阈值不写死** —— 会场的本底噪声换个麦克风、
    换个场地就差一个数量级，绝对阈值在安静的房间里什么都收不到，在吵闹的会场
    里又永远找不到切点。所以拿最近 `FLOOR_WIN_SEC` 秒的低分位当本底，
    再看当前帧是它的几倍。

    **★ 观测窗必须远长于一次发话。** 这里踩过一次：窗只有 5 秒、取 10 分位的
    时候，一个人连着说 10 秒，窗里全是发话的能量，本底就被抬到发话的高度，
    于是**从第 1 秒起整段都被判成静音**，接着被下面那条 max_segment 的规则
    整段丢掉 —— 声音凭空消失，日志上什么都看不出来。30 秒窗 ＋ 5 分位是说
    「一段 30 秒里至少有 5% 的时间没人说话」，接待场景里成立。

    **但这个前提不是铁律**，所以丢掉的时长要记账（`dropped_sec`），
    在现场用真实音频量一次：如果它一直在涨，就是本底被抬上去了，
    把 `FLOOR_WIN_SEC` 加长或者 `floor_k` 调小。
    """

    def __init__(self, silence_sec, min_speech_sec, max_segment_sec, floor_k=3.0):
        self.silence_sec = silence_sec
        self.min_speech_sec = min_speech_sec
        self.max_segment_sec = max_segment_sec
        self.floor_k = floor_k

        self.pcm = bytearray()
        self.t0 = None                      # pcm 第一个样本的 unix 时刻
        self.speech_sec = 0.0
        self.silence_run = 0.0
        self._rms_hist = deque(maxlen=int(FLOOR_WIN_SEC * 1000 / FRAME_MS))
        self._pending = bytearray()         # 不够一帧的零头
        self._floor = 0.0
        self._since_floor = 0
        self.dropped_sec = 0.0              # 被当成静音丢掉的时长（要盯着）

    @staticmethod
    def _rms(buf):
        a = np.frombuffer(buf, dtype=np.int16)
        if a.size == 0:
            return 0.0
        # float64 再平方 —— int16 直接平方会溢出成负数
        return float(np.sqrt(np.mean(a.astype(np.float64) ** 2)))

    def add(self, pcm, unix_ns):
        """喂一块 PCM。切出一句就返回 (bytes, t_start, t_end)，还没切到返回 None。"""
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
            if rms > max(self._noise_floor() * self.floor_k, 50.0):
                self.speech_sec += FRAME_MS / 1000
                self.silence_run = 0.0
            else:
                self.silence_run += FRAME_MS / 1000

        dur = len(self.pcm) / 2 / RATE
        ended = (self.speech_sec >= self.min_speech_sec
                 and self.silence_run >= self.silence_sec)
        if ended or dur >= self.max_segment_sec:
            if self.speech_sec < self.min_speech_sec:
                # 一整段都没听出人声。正常情况这就是没人说话，丢掉是对的 ——
                # 但**记账**：本底被抬上去的时候走的也是这条路（见类的说明）。
                self.dropped_sec += dur
                self._reset()
                return None
            return self._cut()
        return None

    def _noise_floor(self):
        """低分位当本底。每 FLOOR_EVERY 帧重算一次，中间沿用上一次的值。"""
        self._since_floor += 1
        if len(self._rms_hist) < FLOOR_MIN_FRAMES:
            return 0.0
        if self._since_floor >= FLOOR_EVERY or self._floor == 0.0:
            self._since_floor = 0
            self._floor = float(np.percentile(self._rms_hist, FLOOR_PCTL))
        return self._floor

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
    """收音频、出文字，并持有最近的转写。**VLM 那半 import 这个类。**

        tr = Transcriber()
        ...                            # feed() 由收流线程调用
        prompt_part = tr.text()        # 最近 ASR_CONTEXT_SEC 秒
        prompt_part = tr.text(30)      # 想换个窗就现场换，不用重跑
    """

    def __init__(self, model=None, device=None, compute_type=None, language=None,
                 context_sec=None, keep_sec=None, on_utterance=None):
        """on_utterance:
        每确定一句就以 `fn(Utterance)` 调用一次。**是在工作线程里调的** ——
        转写是异步的，`feed()` 返回的时候结果还没出来。
        """
        from faster_whisper import WhisperModel      # 加载慢，延迟 import

        self.model_name = model or env("ASR_MODEL")
        self.device = device or env("ASR_DEVICE")
        self.compute_type = compute_type or env("ASR_COMPUTE_TYPE")
        self.language = language or env("ASR_LANGUAGE")
        self.context_sec = float(context_sec or env("ASR_CONTEXT_SEC"))
        self.keep_sec = float(keep_sec or env("ASR_KEEP_SEC"))
        self.on_utterance = on_utterance
        self._warned_keep = False

        self._seg_args = dict(
            silence_sec=float(env("ASR_SILENCE_SEC")),
            min_speech_sec=float(env("ASR_MIN_SPEECH_SEC")),
            max_segment_sec=float(env("ASR_MAX_SEGMENT_SEC")),
        )
        self._bufs = {}
        self._utts = deque()
        self._lock = threading.Lock()
        self._queue = deque()
        self._wake = threading.Event()
        self.n_seg = 0
        self.busy_sec = 0.0
        self.n_by_source = {}       # 每一路出了多少句（状态面板用）
        self.last_by_source = {}    # 每一路最后一句（状态面板用）

        log("info", f"加载模型: {self.model_name} / {self.device} / {self.compute_type}")
        t0 = time.time()
        self.model = WhisperModel(self.model_name, device=self.device,
                                  compute_type=self.compute_type,
                                  download_root=env("ASR_MODEL_DIR"))
        log("info", f"加载完成 {time.time() - t0:.1f}s（语言 {self.language}、"
                    f"上下文窗 {self.context_sec:.0f}s、留 {self.keep_sec:.0f}s）")
        self._warmup()

        threading.Thread(target=self._worker, daemon=True).start()

    def _warmup(self):
        """**启动时真的跑一次编码器。** 不是为了快，是为了让它当场炸。

        踩过一次：`WhisperModel(device="cuda")` 能建起来、显存也真的占上了
        （nvidia-smi 看得见）、`import faster_whisper` 也没事 —— 但第一次调
        编码器才发现 `Library libcublas.so.12 is not found`。而**测试信号是
        静音，被 vad_filter 挡在编码器之前**，所以整条链路怎么测都是绿的，
        只有真的有人说话那一刻才炸。那时候现场已经开始了。

        所以这里拿 0.5 秒噪声、**关掉 vad_filter** 硬跑一遍：库不全就在启动时
        退出，顺带把 CUDA 的 kernel 也预热了（第一句话不会特别慢）。
        """
        t0 = time.time()
        try:
            segs, _ = self.model.transcribe(
                np.zeros(RATE // 2, dtype=np.float32) + 1e-4,
                language=self.language, beam_size=1, vad_filter=False)
            list(segs)
        except Exception as e:
            raise SystemExit(
                f"[error] 预热失败，模型跑不了: {e}\n"
                f"        典型原因是 ctranslate2 找不到 CUDA 库 —— 那几个 so 在\n"
                f"        venv 的 site-packages/nvidia/ 下，要 LD_LIBRARY_PATH 指过去。\n"
                f"        用 run_asr.sh 起动（它会设好），别直接跑 asr.py。"
            ) from None
        log("info", f"预热 {time.time() - t0:.1f}s —— 编码器确认能跑")

    # ---- 入口 ----

    def feed(self, source, pcm, unix_ns):
        """收流线程调这个。**这里不做推理** —— 在流线程里推理会把收流堵死。"""
        buf = self._bufs.get(source)
        if buf is None:
            buf = self._bufs[source] = _SourceBuffer(**self._seg_args)
        cut = buf.add(pcm, unix_ns)
        if cut is not None:
            seg, t_start, t_end = cut
            self._queue.append((source, seg, t_start, t_end))
            self._wake.set()

    @property
    def dropped_sec(self):
        """被判成「整段没人说话」丢掉的时长。**一直涨就是本底判歪了**
        （见 _SourceBuffer 的说明）。安静的场子里它也会慢慢涨，
        涨得比实际的静音时间还快才是问题。"""
        return sum(b.dropped_sec for b in self._bufs.values())

    def levels(self):
        """每一路当前的本底和判定阈值。**现场调 floor_k 时看这个** ——
        和实际说话时的 RMS 比一比就知道阈值卡在哪。"""
        out = {}
        for src, b in self._bufs.items():
            floor = b._floor
            out[src] = (floor, max(floor * b.floor_k, 50.0))
        return out

    # ---- 出口（VLM 那半用的）----

    def recent(self, seconds=None):
        """最近 N 秒的 Utterance。默认 `ASR_CONTEXT_SEC`。"""
        sec = self.context_sec if seconds is None else seconds
        if sec > self.keep_sec and not self._warned_keep:
            self._warned_keep = True
            log("warn", f"要 {sec:.0f}s 的上下文，但内存里只留 "
                        f"{self.keep_sec:.0f}s（ASR_KEEP_SEC）—— "
                        f"实际只拿得到 {self.keep_sec:.0f}s。要更长就改那个值再重起。")
        cutoff = time.time() - sec
        with self._lock:
            return [u for u in self._utts if u.t_end >= cutoff]

    def text(self, seconds=None, with_time=False):
        """最近 N 秒拼成一个字符串，直接能塞进 prompt。

        **没人说话就返回空字符串。** 不塞占位符 —— 要不要把「这段时间没人
        说话」告诉 VLM、怎么措辞，是造 prompt 那一侧的决定（见文件头）。
        """
        lines = []
        for u in self.recent(seconds):
            if with_time:
                lines.append(f"[{time.strftime('%H:%M:%S', time.localtime(u.t_start))}]"
                             f" {u.source}: {u.text}")
            else:
                lines.append(f"{u.source}: {u.text}")
        return "\n".join(lines)

    # ---- 里面 ----

    def _worker(self):
        while True:
            self._wake.wait(0.2)
            self._wake.clear()
            while self._queue:
                source, seg, t_start, t_end = self._queue.popleft()
                try:
                    self._transcribe(source, seg, t_start, t_end)
                except Exception as e:
                    log("error", f"转写失败 ({source}): {e}")

    def _transcribe(self, source, seg, t_start, t_end):
        audio = np.frombuffer(seg, dtype=np.int16).astype(np.float32) / 32768.0
        t0 = time.time()
        segments, _info = self.model.transcribe(
            audio, language=self.language, beam_size=1,
            # 切分那边已经去掉了纯静音，但一句话中间的停顿要在这里落掉 ——
            # whisper 对着静音很容易出幻听（凭空冒出「ご視聴ありがとう」那类）。
            vad_filter=True,
            # 不看前一句。看了会跟着前一句的句式重复，越滚越离谱。
            condition_on_previous_text=False,
        )
        text = " ".join(s.text.strip() for s in segments).strip()
        took = time.time() - t0
        self.n_seg += 1
        self.busy_sec += took

        if not text:
            return None
        u = Utterance(source, text, t_start, t_end)
        with self._lock:
            self._utts.append(u)
            self.n_by_source[source] = self.n_by_source.get(source, 0) + 1
            self.last_by_source[source] = u
            # 按时间裁，不按条数裁 —— 条数裁的话说得快的那一路会把另一路挤掉。
            cutoff = time.time() - self.keep_sec
            while self._utts and self._utts[0].t_end < cutoff:
                self._utts.popleft()
        dur = t_end - t_start
        log("info", f"{source} {dur:4.1f}s -> {took:4.2f}s ({dur / took:4.1f}x) : {text}")
        if self.on_utterance is not None:
            try:
                self.on_utterance(u)
            except Exception as e:
                log("warn", f"on_utterance 抛了: {e}")
        return u


# =====================================================================
# HTTP：把转写交给 vlm-server（架构 §3.1）
#
# **只有这一个端点，而且是「拉」不是「推」。** 消费的时机就是 VLM 判断的时机，
# 拉出来天然就是「截至此刻的最近 N 秒」；窗长变成查询参数，两台机器之间零协调；
# 窗在这边切，所以只有一个时钟参与（推过去再在那边切的话，两机的时钟偏差会
# 直接歪掉窗口）；vlm-server 挂了也不用在任何地方积压。
#
# 用标准库的 http.server，**不引第三方框架** —— 一个端点、几 KB 的 JSON，
# 犯不上。ThreadingHTTPServer 是因为要和转写线程并行，别互相堵。
# =====================================================================

class _Handler(BaseHTTPRequestHandler):
    transcriber = None      # 由 serve_transcript() 填
    inputs = None

    def do_GET(self):
        u = urlparse(self.path)
        if u.path not in ("/transcript", "/"):
            self._json(404, {"ok": False, "error": f"没有 {u.path}，只有 /transcript"})
            return
        q = parse_qs(u.query)
        try:
            sec = float(q["seconds"][0]) if "seconds" in q else None
        except ValueError:
            self._json(400, {"ok": False, "error": "seconds 要是个数字"})
            return

        tr, inp = self.transcriber, self.inputs
        st = inp.stats()
        # **每一路自带状态。** 「确实没人说话」是 utterances 空而 ok=true；
        # 「那一路断了」是 ok=false —— 这两种在 prompt 里该有不同的说法，
        # 混成一个整体的 503 就分不出来了。
        sources = {
            k: {"ok": bool(v["alive"]), "age": v["age"], "shape": v["desc"],
                "n": v["n"]}
            for k, v in st.items()
        }
        self._json(200, {
            "ok": True,
            "t": time.time(),
            "seconds": sec if sec is not None else tr.context_sec,
            "sources": sources,
            "utterances": [u_.as_dict() for u_ in tr.recent(sec)],
        })

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_a):
        pass        # 默认每个请求打一行到 stderr，会把转写日志淹掉


def serve_transcript(tr, inp, host, port):
    """在后台线程里起 HTTP 服务。返回那个 server（stop 的时候 shutdown 它）。"""
    _Handler.transcriber = tr
    _Handler.inputs = inp
    srv = ThreadingHTTPServer((host, int(port)), _Handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    log("info", f"转写 HTTP: http://{host}:{port}/transcript?seconds=N")
    return srv


def _write_status(path, inp, tr, started):
    """状态面板读的那个文件（`status.py`）。

    **面板不认识这里面任何一路的含义** —— 它只负责把这个 dict 摆出来。
    加一路输入、加一个统计量，改这里就行，面板不用动。

    先写临时文件再 rename：面板每 2 秒读一次，读到写了一半的 JSON 会
    整屏报错。rename 在同一个文件系统上是原子的。
    """
    st = {
        "t": time.time(),
        "started": started,
        "ome": {"host": inp.host, "port": inp.port, "app": inp.app},
        "model": {"name": tr.model_name, "device": tr.device,
                  "compute": tr.compute_type, "language": tr.language},
        "window": {"context_sec": tr.context_sec, "keep_sec": tr.keep_sec},
        "inputs": inp.stats(),
        "asr": {
            "n_seg": tr.n_seg,
            "busy_sec": round(tr.busy_sec, 1),
            "dropped_sec": round(tr.dropped_sec, 1),
            "recent": len(tr.recent()),
            "levels": {k: [round(f, 1), round(t, 1)] for k, (f, t) in tr.levels().items()},
            "n_by_source": dict(tr.n_by_source),
            "last_by_source": {k: u.text[:60] for k, u in tr.last_by_source.items()},
        },
    }
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f, ensure_ascii=False)
    os.replace(tmp, path)


def main():
    import argparse
    import signal

    ap = argparse.ArgumentParser(description="从 OME 收两路音频转成文字")
    ap.add_argument("--seconds", type=float, default=0.0, help="跑多久（0 = 一直跑）")
    ap.add_argument("--no-video", action="store_true",
                    help="只收两路音频，不连视频（省一路 H.264 解码，调转写时用）")
    ap.add_argument("--no-http", action="store_true",
                    help="不起 HTTP 服务（只想看转写、不给 vlm-server 用的时候）")
    a = ap.parse_args()

    log_dir = env("LOG_DIR")
    os.makedirs(log_dir, exist_ok=True)
    jsonl = os.path.join(log_dir, "transcript.jsonl")
    status_path = os.path.join(log_dir, "status.json")

    # 转写的结果写两份，**给的是两种读者**：
    #   transcript.jsonl  机器读的。一句一行，带时刻 —— 事后换上下文窗重放、
    #                     和 bag 对时间，都靠它。**这是这台机器上唯一的记录**
    #                     （画面和音频本身只在 robot-pc 的 bag 里）。
    #   <音源>.txt        人读的。tmux 的窗口 1/2 就是 tail -f 这两个文件，
    #                     所以格式按「一眼扫过去」来排，不是按好解析来排。
    def _append(u):
        with open(jsonl, "a") as f:
            f.write(json.dumps(u.as_dict(), ensure_ascii=False) + "\n")
        with open(os.path.join(log_dir, f"{u.source}.txt"), "a") as f:
            f.write(f"{time.strftime('%H:%M:%S', time.localtime(u.t_start))} "
                    f"[{u.t_end - u.t_start:4.1f}s] {u.text}\n")

    tr = Transcriber(on_utterance=_append)

    only = ["onboard", "operator"] if a.no_video else None
    inp = Inputs(only=only, audio_caps=CAPS)

    # **每一路的形状确认一次。** 速率不对不会抛异常，whisper 会把它当成另一个
    # 速度的声音读出「看着挺像那么回事」的文字 —— 静默地错，最难发现。
    ok_by_src = {}

    def on_audio(source, a_):
        ok = ok_by_src.get(source)
        if ok is None:
            ok = (a_.rate == RATE and a_.channels == CHANNELS)
            ok_by_src[source] = ok
            if ok:
                log("info", f"{source}: {a_.rate} Hz {a_.channels}ch，对的")
            else:
                log("error", f"{source}: 收到的是 {a_.rate} Hz {a_.channels}ch，"
                             f"应该是 {RATE} Hz {CHANNELS}ch —— audio_caps 没生效。"
                             f"**这一路不用**")
        if ok:
            tr.feed(source, a_.data, a_.unix_ns)

    inp.add_audio_sink(on_audio)

    # HTTP：vlm-server 从这里拉转写（架构 §3.1）。**没配就不起** ——
    # 单独调转写的时候用不着，不该因为端口被占就起不来。
    srv = None
    host, port = os.environ.get("ASR_HTTP_HOST"), os.environ.get("ASR_HTTP_PORT")
    if host and port and not a.no_http:
        try:
            srv = serve_transcript(tr, inp, host, port)
        except OSError as e:
            raise SystemExit(f"[error] HTTP 起不来 {host}:{port} —— {e}\n"
                             f"        端口被占？换 ASR_HTTP_PORT。"
                             f"（只想跑转写不要 HTTP 就加 --no-http）")
    else:
        log("warn", "没起 HTTP（ASR_HTTP_HOST/PORT 没设或用了 --no-http）"
                    " —— vlm-server 拿不到转写")

    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())

    inp.start()
    log("info", f"转写结果写在 {jsonl}，分路的在 <音源>.txt")
    started = time.time()
    t0 = time.monotonic()
    tick = float(os.environ.get("STATUS_INTERVAL", 2))
    next_report = t0 + REPORT_SEC
    while not stop.is_set():
        if a.seconds > 0 and time.monotonic() - t0 >= a.seconds:
            break
        # **状态每 tick 写一次，日志每 REPORT_SEC 打一次。** 面板要跟手，
        # 日志要能翻 —— 两个节奏不一样，不要用同一个。
        _write_status(status_path, inp, tr, started)
        stop.wait(tick)
        if time.monotonic() >= next_report:
            next_report = time.monotonic() + REPORT_SEC
            st = inp.stats()
            got = " ".join(f"{k}={st[k]['n']}" for k in sorted(st))
            lv = " ".join(f"{k}(本底{f:.0f}/阈{t:.0f})"
                          for k, (f, t) in sorted(tr.levels().items()))
            log("info", f"[10s] 收 {got} / 最近 {tr.context_sec:.0f}s 有 "
                        f"{len(tr.recent())} 句 / 累计 {tr.n_seg} 段、"
                        f"GPU {tr.busy_sec:.1f}s / 当静音丢掉 {tr.dropped_sec:.0f}s / {lv}")
    inp.stop()
    if srv is not None:
        srv.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
