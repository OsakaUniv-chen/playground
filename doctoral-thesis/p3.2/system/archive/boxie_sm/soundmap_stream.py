#!/usr/bin/env python3
"""16ch マイクアレイ -> 1-bit 音響マップ(10 Hz) -> MPEG-TS/UDP で配信サーバへ。

    alsasrc(16ch) ─ appsink ─ 1-bit 生成 ─ appsrc ─ H.264 ─ mpegtsmux ─ udpsink

navcam / fisheye の押し出しと同じ出口（`mpegtsmux alignment=7 ! udpsink`）に
音響マップを 1 本足すもの。カメラと違って途中に**生成**が挟まるので
`gst-launch` 1 本では書けず、この Python が gst の 2 本のパイプライン
（16ch を吸い出す appsink と、64x64 の画を流し込む appsrc）の間を繋ぐ。

**このファイルだけで動く。** 生成器（`archive/pc-b-robot/soundmap/
onebit_soundmap.py` と同じもの）も中に入れてあり、外のディレクトリは
参照しない。設定は soundmap_stream.sh 側に集めてある。

出す画は**黒地に黄色の斑点**（`exp(値 - 最大値)`）。archive の
soundmap_bridge.py が OME へ送っていたのと同じ画なので、受け側の重ね方
（screen 合成で映像に重ねる）や PC-D の前処理をそのまま流用できる。
解像度は生成解像度（64x64）のまま送る ── 情報量はこれしかないので
送出側で引き伸ばしても増えない。拡大は受け側でやる。

**16ch の生データはここから外に出ない。** 送るのは派生物の音響マップだけ。
"""
from __future__ import annotations

import argparse
import sys
import time

import gi
import numpy as np
from scipy.signal import butter, sosfiltfilt
from scipy.spatial import Delaunay

gi.require_version("Gst", "1.0")
from gi.repository import GLib, Gst  # noqa: E402


# =====================================================================
# 1-bit（bit-shift & XOR）音響マップ生成器。CPU のみ。
#
#   1. 準備   (_prepare_lut)   格子点 x マイクの到達時間差を整数サンプルに
#                              丸めた LUT を組む
#   2. 前処理 (_binarize)      2000-8000 Hz の帯域通過（sosfiltfilt で
#                              零位相 ── チャネル間の相対時刻を歪めない）→
#                              符号だけ残して 1 bit/サンプル
#   3. 相関   (_xor_correlate) C(16,2)=120 のマイク対それぞれで LUT ぶん
#                              ずらして XOR、一致率が그 격자점のスコア
#   4. 地図化 (generate)       極座標格子 + Delaunay 補間で 64x64 に落とす
#
# FFT も浮動小数の積和も使わない（帯域通過だけが float で、呼び出しごとに
# 1 回。格子点ごとではない）。サンプル領域の配列は 64 本/ワードに詰めてあり
# (_pack_bits)、XOR + popcount がワード単位で効く ── 実機の FPGA 音響
# カメラがやっているのと同じ形。N100 実測で 25.5 ms/枚・1 コア・最大 27 Hz。
# =====================================================================

_POPCOUNT_M1 = np.uint64(0x5555555555555555)
_POPCOUNT_M2 = np.uint64(0x3333333333333333)
_POPCOUNT_M4 = np.uint64(0x0f0f0f0f0f0f0f0f)
_POPCOUNT_H01 = np.uint64(0x0101010101010101)


def _popcount64(x):
    """uint64 配列の立っているビット数を数える（SWAR）。サンプルごとの
    ループではなく 5 回の要素演算で済ませる ── 1-bit が CPU で安い理由。"""
    x = x - ((x >> np.uint64(1)) & _POPCOUNT_M1)
    x = (x & _POPCOUNT_M2) + ((x >> np.uint64(2)) & _POPCOUNT_M2)
    x = (x + (x >> np.uint64(4))) & _POPCOUNT_M4
    return (x * _POPCOUNT_H01) >> np.uint64(56)


# miniDSP UMA16v2 の実配置（単位 m）。
MIC_POSITIONS = np.array([
    (0.021, -0.063, 0.0), (0.063, -0.063, 0.0), (0.021, -0.021, 0.0), (0.063, -0.021, 0.0),
    (0.021, 0.021, 0.0), (0.063, 0.021, 0.0), (0.021, 0.063, 0.0), (0.063, 0.063, 0.0),
    (-0.063, 0.063, 0.0), (-0.021, 0.063, 0.0), (-0.063, 0.021, 0.0), (-0.021, 0.021, 0.0),
    (-0.063, -0.021, 0.0), (-0.021, -0.021, 0.0), (-0.063, -0.063, 0.0), (-0.021, -0.063, 0.0),
], dtype=np.float64).T  # (3, 16)


class OneBitSoundMap:
    def __init__(self, fs=44100, channels=16, sm_size=64, filter_order=4, min_samples=256):
        self.fs = fs
        self.channels = channels
        self.sm_size = sm_size

        self.band_low = 2000
        self.band_high = 8000
        self.distance = 1.5
        self.sound_speed = 345
        self.filter_order = filter_order
        self.min_samples = min_samples

        if MIC_POSITIONS.shape != (3, channels):
            raise ValueError(
                f"マイク配置は (3, {channels}) を想定。実際は {MIC_POSITIONS.shape}"
            )
        self.mpos = MIC_POSITIONS
        self.gpos = self._create_merged_grid()
        self.n_grid = self.gpos.shape[1]
        self.r0, self.rm = self._compute_distances(self.gpos, self.mpos)

        self._sos = butter(
            self.filter_order, [self.band_low, self.band_high],
            btype="bandpass", fs=self.fs, output="sos",
        )

        self._prepare_lut()
        self._prepare_interpolator()

    # ---- 幾何（FFT ビームフォーマ版と同じ格子。出力が直接重ねられる）----

    @staticmethod
    def _rect_grid(x_min, x_max, y_min, y_max, increment, z):
        i = abs(increment)
        nxsteps = int(round((abs(x_max - x_min) + i) / i)) if i != 0 else 1
        nysteps = int(round((abs(y_max - y_min) + i) / i)) if i != 0 else 1
        bpos = np.mgrid[x_min:x_max : nxsteps * 1j, y_min:y_max : nysteps * 1j, z : z + 0.1]
        bpos.resize((3, nxsteps * nysteps))
        return bpos

    def _create_merged_grid(self):
        grids = [
            self._rect_grid(-5.0, 5.0, -5.0, 5.0, 1.0, 1.5),
            self._rect_grid(-2.5, 2.5, -2.5, 2.5, 0.5, 1.5),
            self._rect_grid(-1.25, 1.25, -1.25, 1.25, 0.1, 1.5),
        ]
        return np.unique(np.append(np.append(grids[0], grids[1], axis=1), grids[2], axis=1), axis=1)

    @staticmethod
    def _compute_distances(gpos, mpos):
        r0 = np.sqrt(np.sum(gpos * gpos, axis=0))
        diff = gpos.T[:, None, :] - mpos.T[None, :, :]
        rm = np.sqrt(np.sum(diff * diff, axis=2))
        return r0.astype(np.float64), rm.astype(np.float64)

    def _create_uv(self):
        pixel_size = 1080
        r_max = np.pi / 2
        x = self.gpos[0, :]
        y = self.gpos[1, :]

        r = np.arctan(np.sqrt(x**2 + y**2) / self.distance)
        r_normalized = (r / r_max) * (pixel_size / 2)
        theta = np.arctan2(y, x)

        u = (pixel_size / 2 + r_normalized * np.cos(theta)).astype(int)
        v = (pixel_size / 2 + r_normalized * np.sin(theta)).astype(int)
        return u, v

    def _prepare_interpolator(self):
        v, u = self._create_uv()
        v = 1080 - v
        cx, cy = 540, 540
        u = 2 * cx - u
        v = 2 * cy - v
        points = np.array([u, v]).T.astype(np.float64)

        coords = (np.arange(self.sm_size, dtype=np.float64) + 0.5) * (1080.0 / self.sm_size) - 0.5
        grid_x, grid_y = np.meshgrid(coords, coords, indexing="ij")
        targets = np.column_stack((grid_x.ravel(), grid_y.ravel()))

        tri = Delaunay(points)
        simplex = tri.find_simplex(targets)
        valid = simplex >= 0
        safe_simplex = np.where(valid, simplex, 0)
        transform = tri.transform[safe_simplex]
        delta = targets - transform[:, 2]
        bary = np.einsum("nij,nj->ni", transform[:, :2], delta)
        weights = np.column_stack((bary, 1.0 - bary.sum(axis=1)))
        vertices = tri.simplices[safe_simplex]
        weights[~valid] = 0.0
        vertices[~valid] = 0

        self._interp_vertices = vertices.astype(np.int64)
        self._interp_weights = weights.astype(np.float64)

    def _interpolate_to_soundmap(self, values):
        transformed_values = np.clip(values, 0, None)
        sampled = np.sum(transformed_values[self._interp_vertices] * self._interp_weights, axis=1)
        final_lm = sampled.reshape(self.sm_size, self.sm_size)
        return np.clip(final_lm, 0, 160)

    # ---- 手順 1: LUT（格子点 x マイクの到達時間差、サンプル単位）----

    def _prepare_lut(self):
        delay_time = (self.rm - self.rm[:, :1]) / self.sound_speed      # (n_grid, ch) [s]
        delay_samples = np.round(delay_time * self.fs).astype(np.int64)  # mic0 基準の LUT
        self._delay_samples = delay_samples

        # mic0 相対の 15 対ではなく C(ch,2)=120 対すべてを使う（SRP-PHAT 風）。
        # 1 格子点あたり約 8 倍の独立な観測を平均できるぶん分散が下がり、
        # 斑点の縁のちらつきが減る。整数シフト + XOR のままなのは変わらない。
        #
        # 対 (i,j) の一致率は「i と j の相対遅延」だけで決まるので、
        # 重複するシフト量をまとめて（unique）時間方向に先に潰し、
        # 格子点への配り直しは最後に 1 回（inv）で済ませる。
        self._pairs = []   # [(i, j, 一意なシフト量, 格子点 -> そのindex), ...]
        for i in range(self.channels):
            for j in range(i + 1, self.channels):
                diff = delay_samples[:, j] - delay_samples[:, i]
                u, inv = np.unique(diff, return_inverse=True)
                self._pairs.append((i, j, u, inv.ravel()))

    # ---- 手順 2: 帯域通過 + 1 bit（符号）化 ----

    def _audio_queue_to_array(self, audio_queue):
        chunks = [np.frombuffer(a, dtype=np.int16).reshape(-1, self.channels) for a in audio_queue]
        if not chunks:
            return np.zeros((0, self.channels), dtype=np.int16)
        return np.vstack(chunks)

    def _binarize(self, audio):
        filtered = sosfiltfilt(self._sos, audio.astype(np.float64), axis=0)
        return (filtered >= 0).astype(np.uint8)   # (N, ch)、1 bit/サンプル

    # ---- ビット詰め: 64 サンプル/ワード。XOR+popcount が 64 サンプル一括で効く ----
    # WORD_PAD はシフトが隣のワードへ食い込むぶんの余白。13cm のアレイでは
    # 遅延は最大でも ~25 サンプル（64 未満）なので、実際には常に 0 ワード。
    WORD_PAD = 2

    def _pack_channel(self, bits_1d, n_words):
        """(N,) の 0/1 -> (n_words,) uint64。ワード w のビット i = サンプル 64w+i。"""
        padded = np.zeros(n_words * 64, dtype=np.uint8)
        padded[:bits_1d.shape[0]] = bits_1d
        weights = np.uint64(1) << np.arange(64, dtype=np.uint64)
        return (padded.reshape(n_words, 64).astype(np.uint64) * weights[None, :]).sum(
            axis=1, dtype=np.uint64)

    def _pack_bits(self, bits):
        """(N, ch) -> (ch, n_words + 2*WORD_PAD)。両側に余白を置いてあるので、
        範囲内のシフトはただのスライスになる（対ごとの境界判定が要らない）。"""
        n = bits.shape[0]
        n_words = -(-n // 64)   # ceil(n / 64)
        pad = self.WORD_PAD
        packed = np.zeros((self.channels, n_words + 2 * pad), dtype=np.uint64)
        for m in range(self.channels):
            packed[m, pad:pad + n_words] = self._pack_channel(bits[:, m], n_words)
        return packed, n_words

    # ---- 手順 3: LUT ぶんずらして XOR + popcount（全マイク対）----

    def _xor_correlate(self, bits):
        packed, n_words = self._pack_bits(bits)     # (ch, n_words + 2*pad)
        pad = self.WORD_PAD
        word_idx = np.arange(n_words)

        match_total = np.zeros(self.n_grid, dtype=np.int64)
        for i, j, u, inv in self._pairs:
            ref_words = packed[i, pad:pad + n_words]        # (n_words,) 基準側

            q, r = np.divmod(u, 64)                          # ワード / ビットのずれ
            start = pad + q
            idx = start[:, None] + word_idx[None, :]          # (k, n_words)
            hi = packed[j][idx] >> r[:, None].astype(np.uint64)
            shift_lo = np.where(r == 0, np.uint64(1), (64 - r).astype(np.uint64))
            lo = packed[j][idx + 1] << shift_lo[:, None]      # r==0 では使わない
            shifted = np.where(r[:, None] == 0, packed[j][idx], hi | lo)

            match_words = ~(ref_words[None, :] ^ shifted)     # 立っているビット = 一致
            match_per_shift = _popcount64(match_words).sum(axis=1, dtype=np.int64)

            match_total += match_per_shift[inv]

        # 分母は n ではなく n_words*64。詰めるときの 63 ビット以下のゼロ尾は
        # 全格子点に一様に効く 0.2% 未満の希釈なので、特別扱いしない。
        valid_per_pair = n_words * 64
        return match_total / (valid_per_pair * len(self._pairs))   # 0.5 = 偶然一致

    # ---- 手順 4: スコア -> 2 次元の地図 ----
    # GAIN は合成音ではなく実録（G11_game4_DoA）で較正した値。実際の残響と
    # 雑音の下では一致率の頂点は 0.6-0.7 程度で 1.0 には届かないので、
    # 「0.5->0, 1.0->160」と素直に割り当てると起きない範囲に目盛りを使い切り、
    # 表示側の exp(sm - sm.max()) を通した後にほぼ 1 画素まで潰れる。
    # 50 は「見える画素（変換後 >0.05）」の割合が FFT 版と同程度（0.03-0.04）
    # になるように選んだ。**表示側の変換とセットで意味を持つ値**。
    GAIN = 50.0

    def _score_to_db(self, score):
        # FFT 版の図と違い、これは物理的な音圧レベルの推定ではない。
        # exp(sm - sm.max()) を掛けたときに同じように見えるための量。
        return np.clip(score - 0.5, 0.0, None) * self.GAIN

    def generate(self, audio_chunks):
        """audio_chunks: 生の int16/16ch バイト列の列。
        返り値は (sm_size, sm_size) の float（0-160）。"""
        audio = self._audio_queue_to_array(list(audio_chunks))
        if audio.shape[0] < self.min_samples:
            return np.zeros((self.sm_size, self.sm_size))
        bits = self._binarize(audio)
        score = self._xor_correlate(bits)
        return self._interpolate_to_soundmap(self._score_to_db(score))


# =====================================================================
# 取り込み -> 生成 -> 送出
# =====================================================================


class SoundMapStreamer:
    """appsink（16ch 音声）と appsrc（64x64 の画）の 2 本を持ち、間で生成する。"""

    def __init__(self, args):
        self.a = args
        self.gen = OneBitSoundMap(
            fs=args.rate, channels=args.channels, sm_size=args.size
        )

        # 窓（1 枚に使う長さ）と歩幅（何バイトごとに 1 枚作るか）。
        # **この 2 つは別物。** 窓は周期より長く取り、毎回ずらして使う
        # （積分時間が S/N と空間分解能を決める一方、計算量もほぼ比例）。
        # S16LE なので 1 サンプル 2 バイト。
        self.window_bytes = args.rate * args.window_ms // 1000 * args.channels * 2
        self.stride_bytes = (args.rate // args.hz) * args.channels * 2
        self.chunks = []            # 直近 window_bytes ぶんを保持する
        self.buffered = 0
        self.since_last = 0
        self._caps_set = False

        self.n_audio = 0
        self.n_map = 0
        self.n_dropped = 0
        self.map_ms_total = 0.0

        self.loop = GLib.MainLoop()

    # ---- パイプライン ----

    def _capture_desc(self) -> str:
        a = self.a
        spb = a.rate * a.chunk_ms // 1000
        if a.fake:
            src = f"audiotestsrc is-live=true wave=ticks samplesperbuffer={spb} ! audioconvert"
        else:
            # UMA16v2 は S32LE / 16ch でしか開けないので、そこで受けてから
            # audioconvert で S16LE に落とす（生成器が読む形式）。
            src = (
                f"alsasrc device={a.device} buffer-time=200000 "
                f"latency-time={a.chunk_ms * 1000} "
                f"! audio/x-raw,format={a.hw_format},rate={a.rate},channels={a.channels} "
                f"! audioconvert"
            )
        # 送出用の tee は無い。16ch はここから外に出ない。
        #
        # drop=true: 生成が間に合わなくなったときは遅れを溜め込むより古い入力を
        # 捨てて現在に追いつく（このマップは表示・推論用で、記録用ではない）。
        # 10 Hz・25 ms/枚なので通常は落ちない。
        return (
            f"{src} ! audio/x-raw,format=S16LE,rate={a.rate},channels={a.channels} "
            f"! appsink name=sm emit-signals=true sync=false max-buffers=8 drop=true"
        )

    def _send_desc(self) -> str:
        """navcam_stream.sh の出口と同じ形（mpegtsmux alignment=7 ! udpsink）。

        alignment=7（7 x 188 = 1316 バイト）は必須。既定のままだと
        mpegtsmux が大きなバッファを出し、UDP の 1 パケットに収まらずに
        受け側で TS が組み直せない。
        """
        a = self.a
        scale = ""
        if a.out_size and a.out_size != a.size:
            # 最近傍で拡大する。斑点の輪郭がぼやけないほうが「どこが鳴って
            # いるか」を読みやすく、生成解像度以上の情報も増えないため。
            scale = (
                f"! videoscale method=nearest-neighbour "
                f"! video/x-raw,width={a.out_size},height={a.out_size} "
            )
        # 符号化はソフトウェア（x264enc）。64x64・10 Hz では VA-API を使う
        # 意味が無く（小さすぎて逆に通らないことがある）、CPU 負荷も無視できる。
        #
        # appsrc は block=false、queue は leaky=downstream。受け側が居なくても
        # 送出で詰まらせず、生成だけは走り続けるようにする（UDP なので相手の
        # 有無に関係なく送りっぱなしになるが、経路の詰まりに対する保険）。
        return (
            f"appsrc name=mapsrc is-live=true do-timestamp=true format=time "
            f"block=false max-bytes=4000000 "
            f"! queue max-size-buffers=3 leaky=downstream "
            f"{scale}"
            f"! videoconvert "
            f"! x264enc tune=zerolatency speed-preset=ultrafast "
            f"bitrate={a.bitrate} key-int-max={a.hz} "
            # appsrc から来るのは BGR なので、放っておくと x264enc が
            # High 4:4:4 (Y444) を選ぶ。復号できない受け手が多いので固定する。
            f"! video/x-h264,profile=baseline "
            # 途中から受け始められるよう、SPS/PPS を IDR ごとに入れ直す。
            f"! h264parse config-interval=-1 "
            f"! mpegtsmux alignment=7 "
            f"! udpsink host={a.host} port={a.port} buffer-size=2097152 "
            f"sync=false async=false"
        )

    def start(self):
        Gst.init(None)

        desc = self._capture_desc()
        print(f"[capture] {desc}", flush=True)
        self.capture = Gst.parse_launch(desc)
        self.capture.get_by_name("sm").connect("new-sample", self._on_new_sample)
        bus = self.capture.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self._on_capture_message)

        desc = self._send_desc()
        print(f"[send] {desc}", flush=True)
        self.send = Gst.parse_launch(desc)
        self.mapsrc = self.send.get_by_name("mapsrc")
        bus = self.send.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self._on_send_message)

        self.send.set_state(Gst.State.PLAYING)
        self.capture.set_state(Gst.State.PLAYING)
        GLib.timeout_add_seconds(10, self._report)

    # ---- 生成 ----

    def _on_new_sample(self, sink):
        sample = sink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.ERROR
        buf = sample.get_buffer()
        ok, info = buf.map(Gst.MapFlags.READ)
        if not ok:
            return Gst.FlowReturn.ERROR
        try:
            self._on_audio(bytes(info.data))
        except Exception as e:  # ここで例外を漏らすとパイプラインが止まる
            print(f"[error] 音声の処理: {e}", flush=True)
        finally:
            buf.unmap(info)
        return Gst.FlowReturn.OK

    def _on_audio(self, data: bytes):
        self.n_audio += 1

        # 窓は捨てずに持ち越す。古いものから落として window_bytes を保つ。
        self.chunks.append(data)
        self.buffered += len(data)
        self.since_last += len(data)
        while self.buffered - len(self.chunks[0]) >= self.window_bytes:
            self.buffered -= len(self.chunks.pop(0))

        if self.since_last >= self.stride_bytes and self.buffered >= self.window_bytes:
            self.since_last = 0
            self._generate(list(self.chunks))

    def _generate(self, chunks):
        t0 = time.monotonic()
        try:
            smap = self.gen.generate(chunks)
        except Exception as e:
            print(f"[error] 生成失敗: {e}", flush=True)
            return
        self.map_ms_total += (time.monotonic() - t0) * 1000
        self.n_map += 1
        self._push(np.asarray(smap, dtype=np.float32))

    def _push(self, smap):
        """マップを「黒地に黄色の斑点」の画にして appsrc へ流す。

        archive の soundmap_bridge._push_to_ome と同じ変換で、大元は QC 動画
        （`soundmap-generator/soundmap-video/bag2video.py` の
        `labeling.transform_sm` / `sm_to_color`）:

            v   = exp(smap - smap.max())        正規化
            BGR = [0, v, v]                     黒地に黄色

        **min-max 正規化ではなく exp を使うこと。** 生成器の GAIN=50 は
        この exp 変換に掛けた後の「見える画素」の割合が FFT 版と同程度に
        なるよう較正されている。min-max に替えると較正が外れ、静かな場面でも
        画面いっぱいの熱図になって「どこが鳴っているか」が読めなくなる。

        背景が黒なので、受け側は screen 合成でカメラ映像に重ねられる。
        """
        # 生成器が窓を満たせないときは全 0 が返る。その場合は変換しない
        # （exp を掛けると一様な値になって、無音なのに一面が光る）。
        v = np.exp(smap - smap.max()) if smap.max() > 0 else smap
        v = (v * 255.0).clip(0, 255).astype(np.uint8)
        img = np.stack([np.zeros_like(v), v, v], axis=-1)      # BGR = 黄
        h, w = img.shape[:2]

        if not self._caps_set:
            self.mapsrc.set_property("caps", Gst.Caps.from_string(
                f"video/x-raw,format=BGR,width={w},height={h},"
                f"framerate={self.a.hz}/1"
            ))
            self._caps_set = True
        ret = self.mapsrc.emit("push-buffer", Gst.Buffer.new_wrapped(img.tobytes()))
        if ret != Gst.FlowReturn.OK:
            self.n_dropped += 1

    # ---- bus ----

    def _on_capture_message(self, bus, msg):
        if msg.type == Gst.MessageType.ERROR:
            err, dbg = msg.parse_error()
            print(f"[error] 入力: {err} | {dbg}", flush=True)
            self.loop.quit()          # マイクが取れないなら続ける意味が無い
        elif msg.type == Gst.MessageType.WARNING:
            err, dbg = msg.parse_warning()
            print(f"[warn] 入力: {err} | {dbg}", flush=True)
        elif msg.type == Gst.MessageType.EOS:
            print("[error] 入力が終了した", flush=True)
            self.loop.quit()

    def _on_send_message(self, bus, msg):
        # udpsink は投げっぱなしなので、受け側の有無ではここに何も来ない。
        if msg.type == Gst.MessageType.ERROR:
            err, dbg = msg.parse_error()
            print(f"[error] 送出: {err} | {dbg}", flush=True)
            self.loop.quit()
        elif msg.type == Gst.MessageType.WARNING:
            err, dbg = msg.parse_warning()
            print(f"[warn] 送出: {err} | {dbg}", flush=True)

    # ---- 状況 ----

    def _report(self):
        avg = self.map_ms_total / self.n_map if self.n_map else 0.0
        print(
            f"[10s] 入力 {self.n_audio} buf / マップ {self.n_map} 枚 "
            f"(生成 {avg:.1f} ms/枚, 送れなかった {self.n_dropped} 枚)",
            flush=True,
        )
        self.n_audio = self.n_map = self.n_dropped = 0
        self.map_ms_total = 0.0
        return True

    def stop(self):
        self.send.set_state(Gst.State.NULL)
        self.capture.set_state(Gst.State.NULL)


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="16ch マイクアレイ -> 1-bit 音響マップ -> MPEG-TS/UDP",
    )
    # ---- 入力（miniDSP UMA16v2）----
    p.add_argument("--device", default="hw:CARD=UMA16v2,DEV=0",
                   help="ALSA デバイス。番号ではなくカード名で指す（挿し直しでずれない）")
    p.add_argument("--rate", type=int, default=44100, help="サンプリング周波数 [Hz]")
    p.add_argument("--channels", type=int, default=16, help="チャネル数")
    p.add_argument("--hw-format", default="S32LE",
                   help="デバイスから取る形式（UMA16v2 は S32LE 固定）")
    p.add_argument("--chunk-ms", type=int, default=10,
                   help="appsink に届く 1 バッファの長さ [ms]")
    # ---- 生成 ----
    p.add_argument("--hz", type=int, default=10, help="マップの生成周期 [Hz]")
    p.add_argument("--window-ms", type=int, default=464,
                   help="1 枚に使う音声の長さ [ms]（周期より長く、毎回ずらす）")
    p.add_argument("--size", type=int, default=64, help="生成解像度 [px 四方]")
    # ---- 送出 ----
    p.add_argument("--host", default="stream-server.local", help="UDP の宛先")
    p.add_argument("--port", type=int, default=9004, help="UDP のポート")
    p.add_argument("--bitrate", type=int, default=500,
                   help="H.264 の kbps（64x64 ならこれで十分すぎる）")
    p.add_argument("--out-size", type=int, default=0,
                   help="0 以外にすると送出前に最近傍で拡大する（既定 0 = 生成解像度のまま）")
    # ---- その他 ----
    p.add_argument("--fake", action="store_true",
                   help="アレイを繋がずに経路だけ確かめる（audiotestsrc に差し替え）")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if args.rate % args.hz:
        print(f"[warn] rate({args.rate}) が hz({args.hz}) で割り切れない。"
              f"歩幅は {args.rate // args.hz} sample に丸める", flush=True)
    streamer = SoundMapStreamer(args)
    print(
        f"1-bit 音響マップ: {args.channels}ch {args.rate}Hz / "
        f"{args.hz} Hz 生成・窓 {args.window_ms} ms・{args.size}px "
        f"-> udp://{args.host}:{args.port}",
        flush=True,
    )
    streamer.start()
    try:
        streamer.loop.run()
    except KeyboardInterrupt:
        pass
    finally:
        streamer.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
