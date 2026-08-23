#!/usr/bin/env python3
"""16ch 麦克风阵列 -> 1-bit 声音图 -> 原始 BGR 帧写到 stdout。

    alsasrc(16ch) ─ appsink ─ 1-bit 生成 ─ stdout ─(管道)─ start_gstreamer.sh

**这个文件管声音图的全部：采集参数、生成参数、算法常量。**
start_gstreamer.sh 只管把 stdout 出来的帧编码后发给 OME，不需要知道
这里面任何一个数 —— 它用 `--print-caps` 问出 gst 需要的 caps 字符串。

    python3 soundmap.py --print-caps
    video/x-raw,format=BGR,width=64,height=64,framerate=15/1

**stdout 是数据通道，所有日志都走 stderr。** 往 stdout 打印一个字都会
让下游的 gst 解析失败。

**16ch 的原始数据不推出去。** 进 OME 的只有派生的声音图。但**记录要它** ——
加 `--publish` 之后会往 ROS 上发 mic_array/audio（S16LE 16ch，10 ms 一块），
由同机的 recorder.py 收进 bag。换声音图算法之后要重跑，这是唯一的源数据。

不加 `--publish` 就完全不碰 ROS（没装 ROS 的机器上照样推流）。
"""
from __future__ import annotations

import argparse
import array
import os
import sys
import time

import numpy as np
from scipy.signal import butter, sosfiltfilt
from scipy.spatial import Delaunay

# =====================================================================
# 配置。改声音图的行为只改这一段。
# =====================================================================

# ---- 输入（miniDSP UMA16v2）----
# 按卡名引用，不用 hw:3 这种编号 —— 编号随 USB 枚举顺序变，重插一次就会
# 打开别的设备而且不报错。名字用 `cat /proc/asound/cards` 或 `arecord -l` 查。
DEVICE = "hw:CARD=UMA16v2,DEV=0"
RATE = 44100          # UMA16v2 支持 8k/11.025k/12k/16k/32k/44.1k/48k
CHANNELS = 16
HW_FORMAT = "S32LE"   # 硬件只能以 S32LE 打开，之后 audioconvert 降到 S16LE
CHUNK_MS = 10         # appsink 每个 buffer 的长度

# 输入停了的判定 [ms]。**"管线活着但没有数据"必须在这个进程里发现。**
# USB 抽风或驱动卡死时 alsasrc 不报错也不再出 buffer，管线还停在 PLAYING，
# 这个进程就那么挂着 —— 外面没有人会来杀它（每个进程自己盯自己，见 cam.py 的
# pad probe），而这条流是 `soundmap.py | gst-launch` 的 shell 管道，要等这个
# 进程也退出才算一轮结束，所以这边不自己退的话，监视循环就卡在那儿再也不重起了。
#
# 这里是自己数时间，**没有用 gst 的 watchdog element**：它的计时器被任何经过
# 的 event 重置而不只是 buffer（实测源不出帧、event 还在流的时候它永远不响），
# 而且第一个 buffer 到达之前根本不计时，"阵列打开了但一个 buffer 都不出"正好
# 整个漏掉 —— 而那正是这里最该发现的情况。
#
# 3000 ms：正常每 CHUNK_MS=10 ms 一个 buffer，三百倍的富余，不会误杀。
# 0 = 关掉。
INPUT_TIMEOUT_MS = 3000

# ---- 生成 ----
# 周期和积分窗是两回事：窗比周期长，每次滑动取。
# 窗长决定信噪比和空间分辨率，同时计算量也大致成正比。
# 464 ms = 20480 sample/ch @44.1kHz，是 word-wolf 的实测条件。
HZ = 15               # 生成周期。N100 实测上限 27 Hz
WINDOW_MS = 464       # 积分窗
SM_SIZE = 64          # 生成分辨率。**原样送出**，放大交给接收端

# ---- 算法常量 ----
# 改这些等于换了一个生成器。
BAND_LOW = 2000       # 带通下限 [Hz]。和 FFT 波束成形版同频带，便于对比
BAND_HIGH = 8000      # 带通上限 [Hz]
FILTER_ORDER = 4
SOUND_SPEED = 345     # [m/s]
DISTANCE = 1.5        # 虚拟栅格到阵列的距离 [m]
MIN_SAMPLES = 256     # 不足这么多样本就返回全 0

# 显示用的归一化：先减掉本帧的 p99 再逐帧 min-max（见 to_image）。
# 定稿见 test-soundmap/final-decision/make_final_1bit_videos.py。
DISPLAY_PCT = 99.0

# miniDSP UMA16v2 的实际麦克风坐标 [m]。
MIC_POSITIONS = np.array([
    (0.021, -0.063, 0.0), (0.063, -0.063, 0.0), (0.021, -0.021, 0.0), (0.063, -0.021, 0.0),
    (0.021, 0.021, 0.0), (0.063, 0.021, 0.0), (0.021, 0.063, 0.0), (0.063, 0.063, 0.0),
    (-0.063, 0.063, 0.0), (-0.021, 0.063, 0.0), (-0.063, 0.021, 0.0), (-0.021, 0.021, 0.0),
    (-0.063, -0.021, 0.0), (-0.021, -0.021, 0.0), (-0.063, -0.063, 0.0), (-0.021, -0.063, 0.0),
], dtype=np.float64).T  # (3, 16)


def log(msg: str) -> None:
    """所有输出走 stderr。stdout 被帧数据占着。"""
    print(msg, file=sys.stderr, flush=True)


# =====================================================================
# 1-bit（bit-shift & XOR）声音图生成器。纯 CPU。
#
#   1. 准备   (_prepare_lut)   把「栅格点 x 麦克风」的到达时间差取整成
#                              采样数，做成 LUT
#   2. 预处理 (_binarize)      2000-8000 Hz 带通（sosfiltfilt 零相位 ——
#                              不歪曲通道间的相对时刻）→ 只留符号，1 bit/样本
#   3. 相关   (_xor_correlate) C(16,2)=120 个麦克风对，各自按 LUT 移位后
#                              XOR，一致率就是那个栅格点的分数
#   4. 成图   (generate)       极坐标栅格 + Delaunay 插值落到 64x64
#
# 不用 FFT，不用浮点乘加。样本域的数组都按 64 个/字打包（_pack_bits），
# XOR + popcount 按字操作 —— 真实的 FPGA 声学相机就是这么干的。
# N100 实测 25.5 ms/帧、单核、上限 27 Hz。
# =====================================================================

_POPCOUNT_M1 = np.uint64(0x5555555555555555)
_POPCOUNT_M2 = np.uint64(0x3333333333333333)
_POPCOUNT_M4 = np.uint64(0x0f0f0f0f0f0f0f0f)
_POPCOUNT_H01 = np.uint64(0x0101010101010101)


def _popcount64(x):
    """数 uint64 数组里立着的位（SWAR）。5 次元素运算代替逐样本循环 ——
    这就是 1-bit 在 CPU 上便宜的原因。"""
    x = x - ((x >> np.uint64(1)) & _POPCOUNT_M1)
    x = (x & _POPCOUNT_M2) + ((x >> np.uint64(2)) & _POPCOUNT_M2)
    x = (x + (x >> np.uint64(4))) & _POPCOUNT_M4
    return (x * _POPCOUNT_H01) >> np.uint64(56)


class OneBitSoundMap:
    def __init__(self, fs=RATE, channels=CHANNELS, sm_size=SM_SIZE):
        self.fs = fs
        self.channels = channels
        self.sm_size = sm_size

        if MIC_POSITIONS.shape != (3, channels):
            raise ValueError(
                f"麦克风坐标应为 (3, {channels})，实际是 {MIC_POSITIONS.shape}")
        self.mpos = MIC_POSITIONS
        self.gpos = self._create_merged_grid()
        self.n_grid = self.gpos.shape[1]
        self.rm = self._compute_distances(self.gpos, self.mpos)

        self._sos = butter(FILTER_ORDER, [BAND_LOW, BAND_HIGH],
                           btype="bandpass", fs=self.fs, output="sos")
        self._prepare_lut()
        self._prepare_interpolator()

    # ---- 几何（和 FFT 波束成形版同一套栅格，输出可以直接叠）----

    @staticmethod
    def _rect_grid(x_min, x_max, y_min, y_max, increment, z):
        i = abs(increment)
        nx = int(round((abs(x_max - x_min) + i) / i)) if i != 0 else 1
        ny = int(round((abs(y_max - y_min) + i) / i)) if i != 0 else 1
        bpos = np.mgrid[x_min:x_max:nx * 1j, y_min:y_max:ny * 1j, z:z + 0.1]
        bpos.resize((3, nx * ny))
        return bpos

    def _create_merged_grid(self):
        g = [
            self._rect_grid(-5.0, 5.0, -5.0, 5.0, 1.0, DISTANCE),
            self._rect_grid(-2.5, 2.5, -2.5, 2.5, 0.5, DISTANCE),
            self._rect_grid(-1.25, 1.25, -1.25, 1.25, 0.1, DISTANCE),
        ]
        return np.unique(np.append(np.append(g[0], g[1], axis=1), g[2], axis=1), axis=1)

    @staticmethod
    def _compute_distances(gpos, mpos):
        diff = gpos.T[:, None, :] - mpos.T[None, :, :]
        return np.sqrt(np.sum(diff * diff, axis=2)).astype(np.float64)

    def _create_uv(self):
        pixel_size = 1080
        r_max = np.pi / 2
        x, y = self.gpos[0, :], self.gpos[1, :]
        r = np.arctan(np.sqrt(x ** 2 + y ** 2) / DISTANCE)
        r_norm = (r / r_max) * (pixel_size / 2)
        theta = np.arctan2(y, x)
        u = (pixel_size / 2 + r_norm * np.cos(theta)).astype(int)
        v = (pixel_size / 2 + r_norm * np.sin(theta)).astype(int)
        return u, v

    def _prepare_interpolator(self):
        v, u = self._create_uv()
        v = 1080 - v
        cx, cy = 540, 540
        u = 2 * cx - u
        v = 2 * cy - v
        points = np.array([u, v]).T.astype(np.float64)

        coords = (np.arange(self.sm_size, dtype=np.float64) + 0.5) * (1080.0 / self.sm_size) - 0.5
        gx, gy = np.meshgrid(coords, coords, indexing="ij")
        targets = np.column_stack((gx.ravel(), gy.ravel()))

        tri = Delaunay(points)
        simplex = tri.find_simplex(targets)
        valid = simplex >= 0
        safe = np.where(valid, simplex, 0)
        transform = tri.transform[safe]
        delta = targets - transform[:, 2]
        bary = np.einsum("nij,nj->ni", transform[:, :2], delta)
        weights = np.column_stack((bary, 1.0 - bary.sum(axis=1)))
        vertices = tri.simplices[safe]
        weights[~valid] = 0.0
        vertices[~valid] = 0

        self._interp_vertices = vertices.astype(np.int64)
        self._interp_weights = weights.astype(np.float64)

    def _interpolate_to_soundmap(self, values):
        vals = np.clip(values, 0, None)
        sampled = np.sum(vals[self._interp_vertices] * self._interp_weights, axis=1)
        return np.clip(sampled.reshape(self.sm_size, self.sm_size), 0, 160)

    # ---- 步骤 1：LUT ----

    def _prepare_lut(self):
        delay_time = (self.rm - self.rm[:, :1]) / SOUND_SPEED       # (n_grid, ch) 秒
        delay_samples = np.round(delay_time * self.fs).astype(np.int64)

        # 用全部 C(ch,2) 个麦克风对（SRP-PHAT 式），而不是只用相对 mic0 的
        # ch-1 对：每个栅格点的分数因此平均了约 8 倍多的独立观测，方差更小，
        # 边界抖动也小。仍然全是位运算，任何一位都只需要整数移位 + XOR。
        #
        # match(i,j) 在栅格点 g 只取决于 i 和 j 的相对延迟，所以可以先去重成
        # 唯一的移位量、在时间轴上先归约、最后再广播回栅格点。
        self._pairs = []
        for i in range(self.channels):
            for j in range(i + 1, self.channels):
                diff = delay_samples[:, j] - delay_samples[:, i]
                u, inv = np.unique(diff, return_inverse=True)
                self._pairs.append((i, j, u, inv.ravel()))

    # ---- 步骤 2：带通 + 1-bit（符号）量化 ----

    def _binarize(self, audio):
        filtered = sosfiltfilt(self._sos, audio.astype(np.float64), axis=0)
        return (filtered >= 0).astype(np.uint8)

    # ---- 位打包：64 样本/字，XOR+popcount 一次处理 64 个样本 ----
    # 阵列只有约 13 cm，两麦之间的延迟最多几十个样本（远小于 64），所以移位的
    # 字偏移 q = shift//64 实际上总是 0。WORD_PAD=2 是刻意留的富余，不是调出来的。
    WORD_PAD = 2

    def _pack_channel(self, bits_1d, n_words):
        padded = np.zeros(n_words * 64, dtype=np.uint8)
        padded[:bits_1d.shape[0]] = bits_1d
        weights = np.uint64(1) << np.arange(64, dtype=np.uint64)
        return (padded.reshape(n_words, 64).astype(np.uint64) * weights[None, :]).sum(
            axis=1, dtype=np.uint64)

    def _pack_bits(self, bits):
        n = bits.shape[0]
        n_words = -(-n // 64)
        pad = self.WORD_PAD
        packed = np.zeros((self.channels, n_words + 2 * pad), dtype=np.uint64)
        for m in range(self.channels):
            packed[m, pad:pad + n_words] = self._pack_channel(bits[:, m], n_words)
        return packed, n_words

    # ---- 步骤 3：按 LUT 移位、XOR + popcount，遍历所有麦克风对 ----

    def _xor_correlate(self, bits):
        packed, n_words = self._pack_bits(bits)
        pad = self.WORD_PAD
        word_idx = np.arange(n_words)

        match_total = np.zeros(self.n_grid, dtype=np.int64)
        for i, j, u, inv in self._pairs:
            ref_words = packed[i, pad:pad + n_words]
            q, r = np.divmod(u, 64)
            start = pad + q
            idx = start[:, None] + word_idx[None, :]
            hi = packed[j][idx] >> r[:, None].astype(np.uint64)
            shift_lo = np.where(r == 0, np.uint64(1), (64 - r).astype(np.uint64))
            lo = packed[j][idx + 1] << shift_lo[:, None]   # r==0 时不用，但不能移 64
            shifted = np.where(r[:, None] == 0, packed[j][idx], hi | lo)

            match_words = ~(ref_words[None, :] ^ shifted)
            match_per_shift = _popcount64(match_words).sum(axis=1, dtype=np.int64)
            match_total += match_per_shift[inv]

        # 分母用 n_words*64 而不是 n：最后一个字里最多 63 位的零填充，对所有
        #栅格点是同样的稀释，在 20000 样本的窗里远低于 0.2%，可以忽略。
        return match_total / (n_words * 64 * len(self._pairs))

    # ---- 步骤 4：分数 -> 二维图 ----

    def generate(self, audio: np.ndarray) -> np.ndarray:
        """audio: (N, channels) int16。返回 (sm_size, sm_size) float。

        返回的是**原始一致率分数**，范围 [0,1]（0.5 = 完全不相关）。
        不在这里做任何面向显示的变换 —— 归一化是 to_image 的事，
        存进 bag 的也是这个原始值。
        """
        if audio.shape[0] < MIN_SAMPLES:
            return np.zeros((self.sm_size, self.sm_size))
        score = self._xor_correlate(self._binarize(audio))
        return np.clip(self._interpolate_to_soundmap(score), 0.0, 1.0)


# =====================================================================
# 采集 -> 生成 -> stdout
# =====================================================================

def to_image(smap: np.ndarray) -> np.ndarray:
    """把原始一致率分数变成「黑底黄斑」的 BGR 图。

        x   = max(smap - p99(smap), 0)   减掉本帧的 p99 本底
        v   = x / x.max()                逐帧 min-max
        BGR = [0, v, v]                  黑底黄斑

    减 p99 是关键的一步：一致率的本底会随场景整体浮动，直接 min-max 的话
    安静场景也会亮成一片。减掉本帧自己的 p99 之后剩下的才是"比本底显著高"
    的那部分，也就是真正在响的方向。

    底色是黑的，所以接收端可以用 screen 混合直接叠到相机画面上。
    """
    x = np.clip(smap - np.percentile(smap, DISPLAY_PCT), 0.0, None)
    hi = float(x.max())
    # 窗没填满（生成器返回全 0）或整帧都在本底以下时，直接给全黑。
    v = x / hi if hi > 0 else np.zeros_like(x)
    v = (v * 255.0).clip(0, 255).astype(np.uint8)
    return np.stack([np.zeros_like(v), v, v], axis=-1)   # BGR = 黄


class Runner:
    def __init__(self, fake: bool = False, publish: bool = False):
        self.fake = fake
        self.publish = publish
        self.ros = None                 # RosPub，--publish 时才有
        self.last_ts = 0                # 最后一块音频的采集时刻（UNIX ns）
        self.gen = OneBitSoundMap()

        bytes_per_frame = 2 * CHANNELS                    # S16LE
        self.window_bytes = RATE * WINDOW_MS // 1000 * bytes_per_frame
        self.stride_bytes = RATE // HZ * bytes_per_frame

        self.chunks: list[bytes] = []
        self.buffered = 0
        self.since_last = 0

        self.n_audio = self.n_map = self.n_short = 0
        self.map_ms_total = 0.0
        self.out = sys.stdout.buffer
        # 退出码。输入侧出问题时给非 0，上层的 pipefail 才看得见。
        self.rc = 0
        self.last_audio = time.monotonic()   # 最后一次收到输入的时刻

    def capture_desc(self) -> str:
        spb = RATE * CHUNK_MS // 1000
        if self.fake:
            # **用 pink-noise，不要用 ticks。** ticks 是脉冲串，每次脉冲之后
            # 滤波器状态会一路衰减进非规格化浮点（denormal）区间，x86 在那里
            # 慢 20 倍以上 —— 实测带通从 6 ms 变成 150 ms，于是假源测出来的
            # "生成跟不上" 完全是假象。真麦克风永远有底噪（±30 LSB 就够），
            # 不会掉进这个区间。pink-noise 是宽带的，和真实房间噪声同形。
            src = f"audiotestsrc is-live=true wave=pink-noise samplesperbuffer={spb} ! audioconvert"
        else:
            # UMA16v2 只能以 S32LE / 16ch 打开，接下来 audioconvert 降到 S16LE。
            src = (
                f"alsasrc device={DEVICE} buffer-time=200000 latency-time={CHUNK_MS * 1000} "
                f"! audio/x-raw,format={HW_FORMAT},rate={RATE},channels={CHANNELS} "
                f"! audioconvert"
            )
        # 这里没有送出用的 tee —— 16ch 不从这条路出机体。
        #
        # drop=true：生成跟不上时宁可丢掉旧输入追上当前，也不要把延迟攒起来
        # （这张图是给人看和给 VLM 看的，不是记录用的）。
        return (
            f"{src} ! audio/x-raw,format=S16LE,rate={RATE},channels={CHANNELS} "
            f"! appsink name=sm emit-signals=true sync=false max-buffers=8 drop=true"
        )

    def run(self) -> int:
        # **ROS 节点必须建在 import gi 之前。** 实测：这个进程里已经有 numpy，
        # 再 import gi，然后建 rclpy 节点 -> 直接 abort，连一行错误信息都没有
        # （三者缺任何一个都不复现；节点先建就没事）。见 gst_ros.py 的注释。
        if self.publish:
            self._ros_setup()

        import gi
        gi.require_version("Gst", "1.0")
        from gi.repository import GLib, Gst

        Gst.init(None)
        desc = self.capture_desc()
        log(f"[capture] {desc}")
        self.pipeline = Gst.parse_launch(desc)
        self.pipeline.get_by_name("sm").connect("new-sample", self._on_new_sample)

        self.loop = GLib.MainLoop()
        bus = self.pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self._on_message)

        self.pipeline.set_state(Gst.State.PLAYING)
        GLib.timeout_add_seconds(10, self._report)
        # 输入看门狗。从 PLAYING 开始算，所以"一个 buffer 都没来过"也算卡死。
        if INPUT_TIMEOUT_MS:
            self.last_audio = time.monotonic()
            GLib.timeout_add(INPUT_TIMEOUT_MS // 3, self._check_input)
        try:
            self.loop.run()
        except KeyboardInterrupt:
            pass
        finally:
            self.pipeline.set_state(Gst.State.NULL)
        return self.rc

    def _ros_setup(self):
        """记录那一半。**按需 import** —— 不加 --publish 就完全不碰 ROS。"""
        from gst_ros import RosPub
        from teleop_msgs.msg import AudioChunk, SoundMap

        self.AudioChunk, self.SoundMap = AudioChunk, SoundMap
        # ROBOT_NAME 不给默认值：这里和 recorder 各写一份默认值的话，
        # 对不上的表现是"录了个空 bag"，现场发现不了。
        robot = os.environ.get("ROBOT_NAME")
        if not robot:
            raise SystemExit("[error] --publish 要 ROBOT_NAME（source config.env）")
        self.ros = RosPub("soundmap", robot, lambda lv, m: log(f"[{lv}] {m}"))
        # depth 要和 recorder 那边配得上：音频 100 msg/s，浅了会丢。
        self.pub_audio = self.ros.publisher("mic_array/audio", AudioChunk, 200)
        self.pub_map = self.ros.publisher("soundmap/map", SoundMap, 60)

    def _on_new_sample(self, sink):
        from gi.repository import Gst
        sample = sink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.ERROR
        buf = sample.get_buffer()
        ok, info = buf.map(Gst.MapFlags.READ)
        if not ok:
            return Gst.FlowReturn.ERROR
        try:
            ts = 0
            if self.ros is not None:
                from gst_ros import unix_ns
                ts = unix_ns(self.pipeline, sample, buf,
                             warn=lambda m: log(f"[warn] {m}"))
            self._on_audio(bytes(info.data), ts)
        except Exception as e:
            # 异常漏出去会让整条管线停掉。
            log(f"[error] 处理音频: {e}")
        finally:
            buf.unmap(info)
        return Gst.FlowReturn.OK

    def _on_audio(self, data: bytes, ts: int = 0):
        self.n_audio += 1
        self.last_audio = time.monotonic()
        if self.ros is not None:
            self.last_ts = ts
            m = self.AudioChunk()
            self.ros.stamp(m, ts)
            m.header.frame_id = "mic_array"
            # **原样保存**：S16LE 交错，16 声道。这是唯一的源数据，
            # 不在这里做任何加工（FLAC 之类的压缩留给事后离线做）。
            m.encoding = "S16LE"
            m.channels = CHANNELS
            m.sample_rate = RATE
            m.samples = len(data) // (2 * CHANNELS)
            # **必须给 array.array，不能直接给 bytes。** rosidl 的 uint8[]
            # setter 拿到 bytes 会逐字节检查一遍：14 KB 一条要 750 us，
            # 100 msg/s 就是 8.6% 一个核；给 array.array 走的是缓冲区快路，
            # 4.7 us（实测 158 倍）。内容完全一样。
            m.data = array.array("B", data)
            self.pub_audio.publish(m)

        # 窗不丢弃，滚动保留。从最旧的开始丢，保持 window_bytes。
        self.chunks.append(data)
        self.buffered += len(data)
        self.since_last += len(data)
        while self.chunks and self.buffered - len(self.chunks[0]) >= self.window_bytes:
            self.buffered -= len(self.chunks.pop(0))

        if self.since_last >= self.stride_bytes and self.buffered >= self.window_bytes:
            self.since_last = 0
            self._generate()

    def _generate(self):
        audio = np.frombuffer(b"".join(self.chunks), dtype=np.int16).reshape(-1, CHANNELS)
        t0 = time.monotonic()
        try:
            smap = self.gen.generate(audio)
        except Exception as e:
            log(f"[error] 生成失败: {e}")
            return
        elapsed_ms = (time.monotonic() - t0) * 1000
        self.map_ms_total += elapsed_ms
        self.n_map += 1
        # 生成比周期还慢的话就再也追不上了。这是掉帧而不是报错，所以要说出来。
        if elapsed_ms > 1000.0 / HZ:
            self.n_short += 1
        if self.ros is not None:
            # **存的是生成器出来的原始值**，不是下面那张送去显示的黄斑图 ——
            # to_image 的 p99 + min-max 是逐帧、不可逆的，事后要重算或者换
            # 算法就没得救了。
            # 时刻取窗尾（最新那块音频的采集时刻）：这张图反映的是"到那一刻为止"。
            m = self.SoundMap()
            self.ros.stamp(m, self.last_ts)
            m.header.frame_id = "mic_array"
            m.width = m.height = SM_SIZE
            # 同上：4096 个 float 用 tolist() 要 318 us，frombytes 只要 4.6 us。
            buf = array.array("f")
            buf.frombytes(np.asarray(smap, dtype=np.float32).tobytes())
            m.data = buf
            self.pub_map.publish(m)
        try:
            self.out.write(to_image(np.asarray(smap, dtype=np.float32)).tobytes())
            self.out.flush()
        except BrokenPipeError:
            log("[error] 下游关闭了管道")
            self.loop.quit()

    def _check_input(self):
        """输入还在不在。见 INPUT_TIMEOUT_MS。"""
        if (time.monotonic() - self.last_audio) * 1000 < INPUT_TIMEOUT_MS:
            return True
        log(f"[error] ★ 输入卡死：{INPUT_TIMEOUT_MS} ms 没有新数据（阵列掉线 / "
            f"驱动卡死 / 下游堵住）。退出让 start_gstreamer.sh 整条重起。"
            f"查 lsusb -t 和 dmesg")
        # **硬退出。** 走正常的 loop.quit() 要等采集线程回来，而它这时候正卡在
        # 驱动或者 stdout 的写调用里 —— 看门狗要是自己也会被卡住就没意义了。
        sys.stderr.flush()
        os._exit(1)

    def _on_message(self, bus, msg):
        from gi.repository import Gst
        if msg.type == Gst.MessageType.ERROR:
            err, dbg = msg.parse_error()
            log(f"[error] 输入: {err} | {dbg}")
            self.rc = 1
            self.loop.quit()          # 麦克风拿不到就没有继续的意义
        elif msg.type == Gst.MessageType.WARNING:
            err, dbg = msg.parse_warning()
            log(f"[warn] 输入: {err} | {dbg}")
        elif msg.type == Gst.MessageType.EOS:
            log("[error] 输入结束")
            self.rc = 1
            self.loop.quit()

    def _report(self):
        avg = self.map_ms_total / self.n_map if self.n_map else 0.0
        # 这一行会被状态面板原样借去显示（截到 44 字），所以写短一点。
        line = (f"[10s] 输入 {self.n_audio} buf / 生成 {self.n_map} 帧 "
                f"{avg:.1f} ms/帧（预算 {1000.0 / HZ:.0f}）")
        if self.n_short:
            # 已知的两个原因：① CPU 真的不够；② 输入是
            # 数字静音之后的衰减尾，滤波掉进 denormal 区间慢 20 倍（真麦克风
            # 有底噪就不会，但带硬件噪声门的设备可能输出真正的全 0）。
            line += (f" ★ 超时 {self.n_short} 帧 —— 生成跟不上。"
                     f"先看是不是 CPU 不够；如果输入正好是静音段，"
                     f"那是 denormal 导致的滤波变慢，真实声音下不会出现")
        log(line)
        self.n_audio = self.n_map = self.n_short = 0
        self.map_ms_total = 0.0
        return True


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="16ch 麦克风阵列 -> 1-bit 声音图 -> 原始 BGR 帧写到 stdout")
    p.add_argument("--print-caps", action="store_true",
                   help="打印下游 gst 需要的 caps 字符串后退出（避免两边各写一份）")
    p.add_argument("--fake", action="store_true",
                   help="不接阵列，用 audiotestsrc 只验证链路")
    p.add_argument("--publish", action="store_true",
                   help="同时往 ROS 发 mic_array/audio 和 soundmap/map（给 recorder.py 录）。"
                        "不加就完全不碰 ROS")
    a = p.parse_args(argv)

    if a.print_caps:
        print(f"video/x-raw,format=BGR,width={SM_SIZE},height={SM_SIZE},framerate={HZ}/1")
        return 0

    if RATE % HZ:
        log(f"[warn] RATE({RATE}) 不能被 HZ({HZ}) 整除，步长取整为 {RATE // HZ} sample")
    log(f"1-bit 声音图: {CHANNELS}ch {RATE}Hz / {HZ} Hz 生成 / 窗 {WINDOW_MS} ms / "
        f"{SM_SIZE}px -> stdout")
    return Runner(fake=a.fake, publish=a.publish).run()


if __name__ == "__main__":
    sys.exit(main())
