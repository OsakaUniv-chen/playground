#!/usr/bin/env python3
"""stream-server 的人物检测 —— 录制触发的发出方。

    OME(localhost) ──WebRTC──▶ 鱼眼 1080×1080 ──YOLO──▶ 有人/没人
                                                             │ 5 Hz
                                                             ▼
                                              std_msgs/Bool ──▶ robot-pc

**发出去的只是「要不要录」这一个 bool，不是「有几个人、谁在哪」。**
robot-pc 收到 true 就从环形缓冲往前 PREROLL_SEC 开始写，收到 false 就再写
POSTROLL_SEC 才停。判断在这边，记录在那边，它不做任何判断。

**不需要时间戳。** 早先的 msg 里有个 `src_stamp`（那一帧在 robot-pc 上的采集
时刻），用来精确定位前录的切点 —— 去掉了。它要求两机 NTP 同步，还要减一个实测
的链路延迟，而整条链路的延迟（0.2～0.5 s）加上判定的迟滞（约 1 s）本来就被
robot-pc 的 10 s preroll 整个吃掉。为了要回那 1 秒去背一套跨机时钟同步，不划算。
robot-pc 只用自己的时钟，`system-architecture.md` §6.4「基准时钟只有一个」因此
才真的成立。

**画面是前向广角鱼眼，成像圆内切于画面。** Xacti CX-MT500 出 1920×1080，
robot-pc 的 cam.py 裁中央 1080×1080 —— 裁出来的正好就是成像圆：直径 1080、
居中（cx, cy, R = 540, 540, 540）、四边中点顶到画面边，**四个角落在圆外，
是黑的**，约占 21% 的像素。拿 Demonstration_Data/10 的实拍量过，就是这个形状。

**能省掉展开，靠的是「人是竖直的」，不是「填满整幅」。** 前向安装时人的朝向
和方位角无关，所以 COCO 预训练的 person 类直接能用。桶形畸变在边缘会把人压扁
一些，YOLO 扛得住。角上那圈黑区白算一点算力（imgsz 按外接正方形给），但省不
出什么，不值得为它加一层 mask。
※ 如果哪天换成朝上的全向鱼眼（会议相机那种），人的朝向会随方位角旋转，
  那时候必须先展开成全景条带再检测，否则边缘的人全漏。见 README「换相机」。

**输入断了要停止发布，不是发 false。** 见 _tick() 里的注释 ——
这条弄反了会在现场安安静静地什么都不录。

**必须跑在 GPU 上。** 见 PersonDetector —— CPU 上一帧要一两秒，5 Hz 的循环会
一直落后，而日志、tick 数、判定值看起来全都正常。所以宁可起不来也不许退化。

**顺便推一条监视流回 OME**（stream key `detect`，5 fps）：鱼眼 ＋ 检测框 ＋
右上角的录制标志。**只是给人看的**，不进 bag、不参与任何判断 —— 有了它，在
OvenPlayer 上一眼就能确认检测在不在工作、现在到底在不在触发录制。
出口在 srt_out.py，编码器堵住会丢帧而不会拖慢这里的循环。

用法（详见 README）:
    ../run_stream.sh                  生产：从 OME 拉，发 ROS
    ../run_stream.sh test             不发 ROS，只打印
    ../run_stream.sh -- --source file --file a.mp4 --save-vis /tmp/vis
"""
from __future__ import annotations

# GIO 不要去找 proxy。**这一行删掉，从 ROS 节点里用 gi 会整个进程崩掉。**
# GIO 默认的 proxy resolver 走 libproxy，libproxy 内部抛 C++ 异常；而 rclpy
# 载入的 libunwind 劫持了 _Unwind_Resume，栈展开失败 → std::terminate → abort。
# 必须在 import gi.repository 之前（GIO 是延迟加载模块）。
# 连的是 loopback，本来也用不着 proxy。
import os

os.environ.setdefault("GIO_USE_PROXY_RESOLVER", "dummy")

import argparse  # noqa: E402
import collections  # noqa: E402
import json  # noqa: E402
import signal  # noqa: E402
import sys  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402

import numpy as np  # noqa: E402

# =====================================================================
# 配置。改检测的行为只改这一段。
# 部署参数（OME 地址、stream key、ROS 名字）在 config.env，不在这里。
# =====================================================================

# ---- 节奏 ----
# 5 Hz 是和 robot-pc 的**约定**，不是随手挑的：recorder 的
# TRIGGER_TIMEOUT_SEC 是 3 s，5 Hz 意味着连丢 15 帧才会触发 fail-open。
# 改这个数必须同时看 robot-pc 那边的超时。
RATE_HZ = 5.0

# ---- 模型 ----
# COCO 预训练，只取 class 0 = person。
# **要求是「10 米外走过来的人也要算」**，所以这里按 recall 优先挑：
# 那个距离上人在画面里只有 60～100 px 高，是小目标，而小目标恰恰是模型大小
# 和输入尺寸最吃紧的地方。
#
# rog-server（4070 Laptop）实测，1080×1080 输入，含前后处理和 NMS：
#     m@960 29.9ms   m@1088 39.5ms   x@960 75.8ms   x@1088 97.8ms   x@1280 133.9ms
# 5 Hz 的预算是 200 ms，x@1088 只用掉一半。**大模型在这里几乎是白拿的**，
# 而漏掉一个人是不可逆的（没录下来的东西事后找不回来），所以直接上 x。
MODEL = "yolo11x.pt"
IMGSZ = 1088             # 1080 原生不缩（32 的倍数）。再往下降会开始丢远处的人
CONF = 0.35              # ★ 现场手调。10 m 处的人 conf 大约落在 0.2～0.4
IOU = 0.5

# ---- 判定：漏电积分 ----
# 每个 tick 给一个分数：检到人就加，没检到就漏掉一点。分数越过 ON_TH 置位，
# 漏干（回到 0）才落下。**不用「连续 N 帧没人」那种写法**，因为「连续」这个
# 条件对偶发误检没有免疫力 —— 只要每隔几秒有一帧误检，连续计数就永远凑不齐，
# present 一旦置位就再也落不下来，门控失效。
#
# 这个写法的关键性质，是能算出来的：设某个东西在 p 比例的帧里被检到，
#     每 tick 的平均变化 = p × 1.0 − (1−p) × DECAY
#     盈亏平衡点  p* = DECAY / (1 + DECAY) = 0.20
# 也就是说 **DECAY 这一个旋钮定义了「检出率低于 20% 的一律当噪声」**。
#
# ★ 代价：这条线对两边是同一条。一个真人如果只有 15% 的帧被检到（比如 15 m
#   外），同样触发不了。所以 DECAY 要卡在两个实测值中间：
#       无人清场时的误检率  <  p*  <  10 m 处真人的检出率
#   这两个数用现场测试量（人站 5/10/15 m 各 30 秒），量完回来调这里。
S_MAX = 10.0             # 分数上限。也就是「尾巴」最长 S_MAX/DECAY = 40 tick = 8 s
ON_TH = 2.0              # 升到这里置位。近处的人 2 个 tick（0.4 s）就到
DECAY = 0.25             # 每个空帧漏掉多少。见上面的 p*
# 尾巴长度是自适应的，这是白拿的好处：人待久了分数顶到 S_MAX，走后 8 s 才落下；
# 只是一闪而过的话分数只到 ON_TH，1.6 s 就落下。长交互给长尾、误闪给短尾。

# ---- 输入的新鲜度 ----
# 画面超过这么久没更新，就认为输入断了。
# 30 fps 的源，5 Hz 取样，正常 age 在 0.03～0.2 s。
STALE_SEC = 1.5

# ---- 跑在 CPU 上的告警线 ----
# GPU 是 30～130 ms，CPU 是 1000 ms 量级，这条线不会误判。
SLOW_WARN_MS = 300.0

# =====================================================================


def env(key):
    """config.env 负责的项，这里不给默认值（两处写默认值必然对不上）。"""
    try:
        return os.environ[key]
    except KeyError:
        raise SystemExit(
            f"[error] {key} 未设置 —— 没读到 config.env。用 ../run_stream.sh 启动，"
            f"或先 `source config.env`。"
        ) from None


def log(level, msg):
    print(f"[{level}] {msg}", flush=True)


# =====================================================================
# 输入源。三个实现同一个接口：start() / latest() / stop()
#   latest() -> (rgb ndarray(h,w,3), arrival_unix_ns) 或 None
#
# 只保留「最新的一枚」，不排队。**推理跟不上就丢旧帧**，这对判断有没有人
# 是正确的行为 —— 迟到的判断没有价值，而排队会让延迟越积越大。
# =====================================================================


class _Latest:
    """线程间传一枚最新帧。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._item = None
        self.n = 0

    def put(self, rgb, unix_ns):
        with self._lock:
            self._item = (rgb, unix_ns)
            self.n += 1

    def get(self):
        with self._lock:
            return self._item


class OmeSource:
    """生产用：从本机 OME 走 WebRTC 拉 fisheye。

    收流本身用 ome_receiver.py（原样沿用旧实现，有实绩）。
    """

    def __init__(self, host, port, app, stream):
        from ome_receiver import OmeReceiver

        self._latest = _Latest()
        self.stream = stream
        self.rx = OmeReceiver(
            host, port, app, stream,
            on_video=self._on_video,
            logger=lambda lv, m: log(lv, f"[{stream}] {m}"),
            video_format="RGB",   # 直接要 RGB，省一次 Python 侧的转换
        )

    def _on_video(self, data, sample):
        # gst 把每行补到 4 byte 边界，所以要按 stride 还原再切掉padding。
        # 1080×3 = 3240 正好整除 4，实际不会有 padding，但换分辨率就会有。
        s = sample.get_caps().get_structure(0)
        w, h = s.get_value("width"), s.get_value("height")
        if not h:
            return
        a = np.frombuffer(data, dtype=np.uint8)
        stride = a.size // h
        if stride * h != a.size:
            return
        rgb = a.reshape(h, stride)[:, : w * 3].reshape(h, w, 3)
        self._latest.put(rgb, time.clock_gettime_ns(time.CLOCK_REALTIME))

    def start(self):
        self.rx.start()

    def latest(self):
        return self._latest.get()

    def stop(self):
        self.rx.stop()

    def describe(self):
        return f"OME {self.rx.url}"


class _CvSource:
    """cv2.VideoCapture 的公共部分（文件 / v4l2 都用它）。

    单独开一个线程一直读，主循环只拿最新的 —— 和 OME 那条路径行为一致，
    这样「推理跟不上会怎样」在测试和生产里是同一个行为。
    """

    def __init__(self, opener, loop_file=False, fps_limit=None):
        import cv2

        self.cv2 = cv2
        self._opener = opener
        self._loop_file = loop_file
        self._fps_limit = fps_limit
        self._latest = _Latest()
        self._stop = threading.Event()
        self._thread = None
        self.cap = None

    def start(self):
        self.cap = self._opener()
        if not self.cap.isOpened():
            raise SystemExit("[error] 打不开输入源")
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        period = 1.0 / self._fps_limit if self._fps_limit else 0.0
        rewound = 0
        while not self._stop.is_set():
            t0 = time.monotonic()
            ok, bgr = self.cap.read()
            if not ok:
                # 回到开头再试。**要限次数** —— 文件解不出来时（0 帧、编码不认）
                # read 会一直失败，不限的话这里就是个 100% CPU 的空转。
                if self._loop_file and rewound < 3:
                    rewound += 1
                    self.cap.set(self.cv2.CAP_PROP_POS_FRAMES, 0)
                    continue
                log("warn", "输入结束")
                break
            rewound = 0
            self._latest.put(
                self.cv2.cvtColor(bgr, self.cv2.COLOR_BGR2RGB),
                time.clock_gettime_ns(time.CLOCK_REALTIME),
            )
            if period:
                dt = period - (time.monotonic() - t0)
                if dt > 0:
                    time.sleep(dt)

    def latest(self):
        return self._latest.get()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)
        if self.cap:
            self.cap.release()


class FileSource(_CvSource):
    """测试用：拿录好的视频当输入。调阈值、看漏检都用这个。"""

    def __init__(self, path, loop=True, fps_limit=30.0):
        import cv2

        super().__init__(lambda: cv2.VideoCapture(path), loop, fps_limit)
        self.path = path

    def describe(self):
        return f"file {self.path}"


class V4L2Source(_CvSource):
    """测试用：桌面上插个 USB 摄像头就能验证整条链路。"""

    def __init__(self, dev):
        import cv2

        super().__init__(lambda: cv2.VideoCapture(dev), False, None)
        self.dev = dev

    def describe(self):
        return f"v4l2 {self.dev}"


# =====================================================================
# 检测
# =====================================================================


class PersonDetector:
    """逐帧检测。**不做跟踪。**

    早先这里用 ByteTrack，理由是「让人数稳」。去掉了，因为在这个工作点上它
    是净负债：

    ① 5 Hz 对 ByteTrack 是错的工作点。它的关联是 IoU ＋ Kalman，参数按 30 fps
       调的；200 ms 一帧，走动的人位移约 24 cm，加上鱼眼畸变，前后两帧的框很
       容易匹配不上，轨迹碎裂。
    ② **它会引入系统性的漏报，方向正好反了。** ultralytics 只输出
       is_activated 的轨迹，而新轨迹要下一帧被匹配上才置位 —— 每个新出现的人
       天生晚一帧，而按 ① 匹配失败是常态。逐帧 predict 不会这样：检到就是检到。
    ③ persist=True 是跨断流的隐藏状态。断流恢复时 Kalman 状态是陈的，匹配必然
       失败 —— 恰好在最需要它工作的那一刻。
    ④ 它要 lap 这个包，不显式装的话 ultralytics 会在第一次 track 时联网
       AutoUpdate，现场没网就当场失败。

    要精确的人数或停留时长，事后拿录下来的 30 fps 鱼眼离线重跑，比这条 5 Hz
    的链路准得多。在线这一路只负责稳地做一个二值判断。
    """

    def __init__(self, model=MODEL, imgsz=IMGSZ):
        import torch
        from ultralytics import YOLO

        # ---- ① 必须有 GPU，没有就退出 ----
        # **不要在这里 try/except 退回 CPU。** ultralytics 的 predict 会自己
        # select_device('')，CUDA 不可用时它会**默默**挑 cpu 并把模型搬过去，
        # 不报错。那时候一帧要一两秒，5 Hz 的循环一直落后重对齐，判定几秒才更新
        # 一次，而日志、tick 数、判定值看起来全都正常 —— 现场根本发现不了。
        if not torch.cuda.is_available():
            raise SystemExit(
                "[error] CUDA 不可用 —— 本节点必须跑在 GPU 上。\n"
                "        先看 nvidia-smi；若 nvidia-smi 正常而这里失败，多半是\n"
                "        nvidia_uvm 掉进坏状态（挂起/恢复后常见）：\n"
                "            sudo rmmod nvidia_uvm && sudo modprobe nvidia_uvm\n"
                "        另外建议开持久化模式：sudo nvidia-smi -pm 1"
            )
        self.torch = torch
        self.device = "cuda:0"
        self.imgsz = imgsz
        self.yolo = YOLO(model)
        self.yolo.to(self.device)
        log("info", f"模型 {os.path.basename(model)} imgsz={imgsz} "
                    f"conf={CONF} GPU {torch.cuda.get_device_name(0)}")

    def warmup(self, h=1080, w=1080):
        """先跑一枚假的。**第一次推理要 1～3 秒**（cudnn autotune ＋ 权重上卡），
        不预热的话第一个 tick 一定会超时，日志开头永远有一条假的告警。

        顺便断言实际用的就是 GPU —— 见 __init__ ①。`.to("cuda")` 只挪了
        nn.Module，**真正决定推理设备的是 predictor**，所以要在这里查它。
        """
        t0 = time.monotonic()
        self(np.zeros((h, w, 3), dtype=np.uint8))
        dev = self.yolo.predictor.device
        if dev.type != "cuda":
            raise SystemExit(f"[error] 推理实际跑在 {dev} 上 —— 本节点必须用 GPU")
        log("info", f"预热 {time.monotonic() - t0:.2f}s  实际推理设备 {dev}")

    def __call__(self, rgb):
        """返回 (人数, boxes[N,4] xyxy, confs[N])。"""
        # ultralytics 要连续内存，且 numpy 输入按 BGR 解释 —— 但我们只关心
        # 「有没有人」，RGB/BGR 对 person 类的影响可以忽略。这里仍然转一下，
        # 是为了让 --save-vis 存出来的图颜色是对的，看图调参不会误判。
        img = np.ascontiguousarray(rgb[:, :, ::-1])
        # **device 显式传进去**，不依赖 select_device 的自动挑。CUDA 中途出问题
        # 时这样会报错，而不是静默地滑到 CPU 上。
        r = self.yolo.predict(img, imgsz=self.imgsz, conf=CONF, iou=IOU,
                              classes=[0], device=self.device, verbose=False)[0]
        b = r.boxes
        if b is None or len(b) == 0:
            return 0, np.zeros((0, 4), np.float32), np.zeros((0,), np.float32)
        return len(b), b.xyxy.cpu().numpy(), b.conf.cpu().numpy()


# =====================================================================
# 判定
# =====================================================================


class PresenceGate:
    """漏电积分。检到人加分，没检到漏分；越过 ON_TH 置位，漏干才落下。

    参数和为什么是这个形状，见顶部配置段的「判定：漏电积分」。
    """

    def __init__(self, s_max=S_MAX, on_th=ON_TH, decay=DECAY):
        self.s_max = s_max
        self.on_th = on_th
        self.decay = decay
        self.score = 0.0
        self.present = False

    def update(self, n):
        if n > 0:
            self.score = min(self.score + 1.0, self.s_max)
        else:
            self.score = max(self.score - self.decay, 0.0)

        # 施密特：置位看 ON_TH，落下看 0，中间保持不变。
        if not self.present:
            if self.score >= self.on_th:
                self.present = True
        elif self.score <= 0.0:
            self.present = False

        return self.present, self.score


# =====================================================================
# 输出
# =====================================================================


class RosSink:
    """发 std_msgs/Bool 到 /<ROBOT_NAME>/record/trigger。

    **是「要不要录」，不是「有没有人」。** 名字这么起是为了以后能往里 OR
    别的触发源（比如操作者按钮）而不用动 robot-pc 那边一行代码。

    用 std_msgs 而不是自定义 msg：这条 topic 上要传的就只有一个 bool，而
    自定义类型要在两台机器上都 build 出来、类型 hash 还得对得上（rog-server
    是 Jazzy，robot-pc 是 Humble）。robot-pc 侧记录用的 AudioChunk / SoundMap
    那些仍然要建 teleop_msgs，只是这条不用等它。
    """

    def __init__(self, robot_name):
        import rclpy
        from rclpy.node import Node
        from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy
        from std_msgs.msg import Bool

        self.rclpy = rclpy
        self.Bool = Bool
        rclpy.init()
        self.node = Node("person_detect")
        # BEST_EFFORT ＋ depth 1：这是个 5 Hz 常发的状态量，
        # **丢一帧不需要重传** —— 下一帧 200 ms 后就来了，重传只会添堵。
        # ★ robot-pc 的 subscriber 如果用默认的 RELIABLE，两边不兼容，一条都
        #   收不到，**而且 ROS 不报错**。`ros2 topic info -v` 能看出来。
        qos = QoSProfile(depth=1,
                         reliability=ReliabilityPolicy.BEST_EFFORT,
                         history=HistoryPolicy.KEEP_LAST)
        topic = f"/{robot_name}/record/trigger"
        self.pub = self.node.create_publisher(Bool, topic, qos)

        # 纯 publisher 不 spin 也能发，但**不 spin 的节点在 `ros2 node list`
        # 里不出现，`ros2 topic info` 也看不到**。现场排查「消息到底发没发」
        # 时全靠这两个命令，所以起个后台线程 spin 着。
        self._spin = threading.Thread(target=self._spin_forever, daemon=True)
        self._spin.start()
        self.last = None
        log("info", f"ROS publisher: {topic} (std_msgs/Bool, BEST_EFFORT)")

    def _spin_forever(self):
        try:
            self.rclpy.spin(self.node)
        except Exception:
            pass        # shutdown 时会抛，正常

    def publish(self, present):
        m = self.Bool()
        m.data = bool(present)
        self.pub.publish(m)
        # **翻转的时刻要留在日志里。** 5 Hz 常发的值不能每条都打，但事后想解释
        # 「这段 bag 为什么从这里开始」时，日志里这一行是最直接的对照物。
        if present != self.last:
            log("info", f"trigger={present}")
            self.last = present

    def stop(self):
        self.rclpy.shutdown()      # 先 shutdown，spin 线程才会退出来
        self.node.destroy_node()


class PrintSink:
    """没有 ROS 时用（--print-only）。只打印，流水账由 JsonlLog 负责。"""

    def __init__(self):
        self.last = None

    def publish(self, present):
        # 屏幕上只在状态变化时打一行，不然 5 Hz 会把日志刷没。
        if present != self.last:
            log("info", f"trigger={present}")
            self.last = present

    def stop(self):
        pass


class JsonlLog:
    """每个 tick 一行的流水账。**不管有没有触发录制都写。**

    这是唯一能回答「我们漏录了吗」的东西 —— 门控有个根本性的观测盲区：
    **漏掉的东西不在 bag 里**，事后翻 bag，「规则太严没触发」和「那段时间真的
    没人」长得一模一样，分辨不出来。这份流水账在门控之外，所以能：

      ① 查漏 —— 找「检到了人但分数没爬到 ON_TH」的时段；
      ② **离线重放调参** —— 改一组 DECAY/ON_TH/CONF 拿它重跑，直接看会多录
         多少、少录多少，不用重新做实验。顶部说的「DECAY 要卡在两个实测值
         中间」就是靠这个变得可操作的；
      ③ 回答「这一整天有多少人接近过机器人」—— bag 里只有被录下来的那部分。

    5 Hz 跑一整天约 43 万行、几十 MB。相比录制的 10.2 GB/小时可以忽略。
    """

    def __init__(self, path):
        d = os.path.dirname(os.path.abspath(path))
        if d:
            os.makedirs(d, exist_ok=True)
        self.path = path
        self.f = open(path, "a", buffering=1)   # 行缓冲：崩了也不丢已写的行

    def write(self, now_ns, n, boxes, confs, score, present):
        rec = {
            "t": round(now_ns / 1e9, 3),
            "n": int(n),
            # 取整 ＋ 两位小数，纯粹是为了让文件小一点、肉眼也能读
            "boxes": [[int(v) for v in b] for b in boxes],
            "conf": [round(float(c), 2) for c in confs],
            "score": round(float(score), 2),
            "present": bool(present),
        }
        self.f.write(json.dumps(rec) + "\n")

    def stop(self):
        self.f.close()


# =====================================================================
# 主循环
# =====================================================================


class Runner:
    def __init__(self, source, detector, sink, jsonl=None, save_vis=None,
                 publish_vis=False):
        self.source = source
        self.detector = detector
        self.sink = sink
        self.jsonl = jsonl
        self.gate = PresenceGate()
        self.save_vis = save_vis
        # 合成流的出口。**第一帧到了才建** —— caps 要写死宽高，而只有拿到
        # 真实画面才知道是多少（生产是 1080×1080，但 --source file 什么都可能）。
        self.publish_vis = publish_vis
        self.vis_out = None
        self.running = True
        self.n_tick = 0
        self.n_stale = 0
        self.dt_ms = collections.deque(maxlen=50)
        self._slow_warned = False
        self._vis_i = 0
        if save_vis:
            os.makedirs(save_vis, exist_ok=True)

    def stop(self, *_a):
        self.running = False

    def run(self):
        period = 1.0 / RATE_HZ
        next_t = time.monotonic()
        last_report = time.monotonic()
        while self.running:
            next_t += period
            self._tick()
            if time.monotonic() - last_report >= 10.0:
                self._report()
                last_report = time.monotonic()
            dt = next_t - time.monotonic()
            if dt > 0:
                time.sleep(dt)
            else:
                # 落后了就重新对齐，不要把欠下的 tick 攒着补 —— 补出来的
                # 判断用的都是同一枚画面，没有意义，只会让后面一直追不上。
                next_t = time.monotonic()

    def _tick(self):
        item = self.source.latest()
        now_ns = time.clock_gettime_ns(time.CLOCK_REALTIME)

        # ---- 输入断了 ----
        # **停止发布，不要发 false。**
        # robot-pc 的 recorder 在 TRIGGER_TIMEOUT_SEC(3 s) 收不到消息时会
        # fail-open 退化成连续记录 —— 那是我们要的行为。
        # 而发 false 是在告诉它「我看清楚了，确实不用录」，
        # 它就会安安静静地什么都不录。OME 掉了、robot-pc 停推了、
        # 这个进程的收流线程卡死了 —— 每一种都会走到这里。
        if item is None or (now_ns - item[1]) / 1e9 > STALE_SEC:
            self.n_stale += 1
            if self.n_stale in (1, 25) or self.n_stale % 250 == 0:
                age = "-" if item is None else f"{(now_ns - item[1]) / 1e9:.1f}s"
                log("warn", f"画面没有更新（age={age}）—— 停止发布，"
                            f"让 robot-pc 走 fail-open")
            return
        if self.n_stale:
            log("info", f"画面恢复（中断了 {self.n_stale} 个 tick）")
            self.n_stale = 0

        rgb, _arrival_ns = item
        t0 = time.monotonic()
        n, boxes, confs = self.detector(rgb)
        self.dt_ms.append((time.monotonic() - t0) * 1e3)

        present, score = self.gate.update(n)
        self.sink.publish(present)
        if self.jsonl:
            self.jsonl.write(now_ns, n, boxes, confs, score, present)
        self.n_tick += 1

        if self.save_vis or self.publish_vis:
            img = self._annotate(rgb, boxes, confs, present, score)
            if self.publish_vis:
                self._publish(img)
            if self.save_vis:
                import cv2
                cv2.imwrite(
                    os.path.join(self.save_vis, f"{self._vis_i:06d}.jpg"), img)
                self._vis_i += 1

    def _annotate(self, rgb, boxes, confs, present, score):
        """画检测框 ＋ 右上角的录制标志。返回 BGR。

        **右上角那个标志是这条流存在的理由** —— 在 OvenPlayer 上一眼就能看出
        「现在到底在不在触发录制」，不用去翻 robot-pc 的日志、也不用等事后看
        bag 才发现那一段没录上。
        """
        import cv2

        F = cv2.FONT_HERSHEY_SIMPLEX
        img = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        for i, (x1, y1, x2, y2) in enumerate(boxes.astype(int)):
            cv2.rectangle(img, (x1, y1), (x2, y2), (0, 255, 0), 2)
            cv2.putText(img, f"{confs[i]:.2f}", (x1, max(0, y1 - 6)),
                        F, 0.8, (0, 255, 0), 2)

        # **两个角都先压一块半透明暗底再写字。** 鱼眼画面里什么亮度都可能出现，
        # 直接写字的话遇到亮背景（白墙、天空、展台灯）就糊成一片 —— 而这条流
        # 的全部意义就是「一眼能看出」，看不清就等于没有。
        h, w = img.shape[:2]

        def plate(x0, y0, x1, y1):
            roi = img[y0:y1, x0:x1]
            img[y0:y1, x0:x1] = cv2.addWeighted(
                roi, 0.35, np.zeros_like(roi), 0.65, 0)

        col = (0, 0, 255) if present else (170, 170, 170)     # BGR：红 / 灰
        plate(w - 280, 10, w - 10, 100)
        cv2.circle(img, (w - 250, 45), 14, col, -1)
        cv2.putText(img, "REC" if present else "IDLE", (w - 225, 56), F, 1.1, col, 3)
        cv2.putText(img, f"score {score:4.1f}   n={len(boxes)}", (w - 268, 88),
                    F, 0.7, (235, 235, 235), 2)
        # 左上角的墙钟时刻：事后拿这条流的截图和 bag 对时间用
        plate(10, 10, 210, 60)
        cv2.putText(img, time.strftime("%H:%M:%S"), (20, 47), F, 1.0,
                    (255, 255, 255), 2)
        return img

    def _publish(self, img):
        if self.vis_out is None:
            from srt_out import from_env
            h, w = img.shape[:2]
            self.vis_out = from_env("STREAM_KEY_DETECT", w, h, int(RATE_HZ),
                                    "DETECT_BITRATE", 2000, logger=log)
            self.vis_out.start()
        self.vis_out.push(img)

    def _report(self):
        if not self.dt_ms:
            return
        ms = sorted(self.dt_ms)
        med = ms[len(ms) // 2]
        log("info", f"tick={self.n_tick} 推理 中位 {med:.0f}ms "
                    f"最差 {ms[-1]:.0f}ms（预算 {1000 / RATE_HZ:.0f}ms）"
                    f" trigger={self.gate.present} score={self.gate.score:.2f}")
        # GPU 是 30～130 ms。到了这条线基本只有一个可能：在 CPU 上跑。
        # warmup 已经断言过设备，所以这里是兜底（比如中途掉卡）。
        if med > SLOW_WARN_MS and not self._slow_warned:
            log("warn", f"★ 推理中位 {med:.0f}ms，远超 GPU 应有的量级 —— "
                        f"是不是滑到 CPU 上了？")
            self._slow_warned = True


def main():
    ap = argparse.ArgumentParser(description="stream-server 的人物检测")
    ap.add_argument("--source", choices=["ome", "file", "v4l2"], default="ome",
                    help="ome=生产（从本机 OME 拉）；file/v4l2=测试")
    ap.add_argument("--file", help="--source file 时的视频路径")
    ap.add_argument("--device", default="/dev/video0", help="--source v4l2 时的设备")
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--imgsz", type=int, default=IMGSZ)
    ap.add_argument("--print-only", action="store_true",
                    help="不发 ROS，只打印。**没有 ROS 的机器上用这个**")
    ap.add_argument("--jsonl",
                    help="流水账写到这个文件。不给的话写 $LOG_DIR/detect-<起动时刻>.jsonl")
    ap.add_argument("--no-jsonl", action="store_true",
                    help="不写流水账。**生产环境不要用** —— 见 JsonlLog 的说明")
    ap.add_argument("--save-vis", help="把标注过的帧存进这个目录（调阈值用）")
    ap.add_argument("--publish-vis", dest="publish_vis", action="store_true",
                    default=None,
                    help="把带框和录制标志的画面推回 OME（--source ome 时默认开）")
    ap.add_argument("--no-publish-vis", dest="publish_vis", action="store_false",
                    help="不推那条监视流")
    ap.add_argument("--seconds", type=float, default=0.0,
                    help="跑多少秒后自己退出（0=一直跑）")
    a = ap.parse_args()

    # 权重固定到一个目录。ultralytics 对「裸文件名」是相对 cwd 解析并下载的，
    # 所以换个工作目录跑就会重下一遍，还会把 .pt 掉在 git 仓库里。
    # 拼成绝对路径喂给它就没这问题（**不要 chdir** —— 那会把 --file、
    # --jsonl 这些相对路径一起带偏）。
    model = a.model
    wd = os.environ.get("YOLO_WEIGHTS_DIR")
    if wd and not os.path.isabs(model) and os.sep not in model:
        os.makedirs(wd, exist_ok=True)
        model = os.path.join(wd, model)

    # ---- 输入 ----
    if a.source == "ome":
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        source = OmeSource(env("OME_HOST"), int(env("OME_WS_PORT")),
                           env("OME_APP"), env("STREAM_KEY_FISHEYE"))
    elif a.source == "file":
        if not a.file:
            raise SystemExit("[error] --source file 要配 --file")
        source = FileSource(a.file)
    else:
        source = V4L2Source(a.device)

    # ---- 输出 ----
    sink = PrintSink() if a.print_only else RosSink(env("ROBOT_NAME"))

    # ---- 流水账 ----
    # **默认开着**，包括生产。理由见 JsonlLog 的 docstring。
    jsonl = None
    if not a.no_jsonl:
        path = a.jsonl
        if not path:
            ld = os.environ.get("LOG_DIR")
            if ld:
                stamp = time.strftime("%Y%m%d-%H%M%S")
                path = os.path.join(ld, f"detect-{stamp}.jsonl")
            else:
                log("warn", "LOG_DIR 没设置且没给 --jsonl —— 这次不写流水账")
        if path:
            jsonl = JsonlLog(path)
            log("info", f"流水账 {path}")

    detector = PersonDetector(model, a.imgsz)
    detector.warmup()

    log("info", f"输入 {source.describe()}  {RATE_HZ:.0f} Hz")
    source.start()

    # 监视流：生产（--source ome）默认开，其余默认关 —— 用 --source file 临时
    # 调参时通常没 source 过 config.env，不该因为缺 SRT 的配置就起不来。
    publish_vis = a.publish_vis if a.publish_vis is not None else (a.source == "ome")
    runner = Runner(source, detector, sink, jsonl, a.save_vis, publish_vis)
    signal.signal(signal.SIGINT, runner.stop)
    signal.signal(signal.SIGTERM, runner.stop)

    if a.seconds > 0:
        threading.Timer(a.seconds, runner.stop).start()

    try:
        runner.run()
    finally:
        source.stop()
        sink.stop()
        if runner.vis_out:
            runner.vis_out.stop()
        if jsonl:
            jsonl.stop()
        log("info", f"终了 tick={runner.n_tick}")


if __name__ == "__main__":
    main()
