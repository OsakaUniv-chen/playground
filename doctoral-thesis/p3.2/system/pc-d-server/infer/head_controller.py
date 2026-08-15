#!/usr/bin/env python3
"""頭部の向きを決めて PC-B へ送る（設計 §4.2）。

現状は骨格だけ。VLM も音響マップもまだ繋いでいないので、既定では
何も出さない（--demo で正弦波を出して経路だけ確認できる）。

    ここ ── TCP/JSON ──> PC-C の head_relay.py ── ROS ──> PC-B の head_driver

**ROS は使わない。** PC-D は理研にあって PC-B と同じ LAN に居らず、
distro も違う（PC-D は galactic、PC-B / PC-C は humble で既定の RMW が
別物）。跨ぐのは 10 Hz・整数 3 個の指令 1 本だけなので、DDS を通さず
素の TCP で PC-C へ送り、向こうで ROS に載せ替える。おかげで
**この機械に ROS を入れる必要が無い**（受信側 recv_ome.py も rclpy 不要）。

接続先は PC-C から張った SSH トンネルの出口なので既定は 127.0.0.1。
`HEAD_RELAY_HOST` / `HEAD_RELAY_PORT` で変えられる。

可動域制限と smoothing は PC-B の head_driver が掛ける。**こちらは
制限を知らずに指令を出してよい。** 実際に適用された値は PC-B の
`<robot>/head/applied` に出る。

記録の観点（設計 §5.5）:
    ここが出す指令は「モデルの判断」。操作者がゲームパッドで出す台車指令とは
    別 topic なので、bag の中で自然に区別できる。両方を混ぜないこと。
"""

import argparse
import json
import math
import os
import socket
import time


class HeadClient:
    """PC-C の head_relay へ繋ぎ、切れたら勝手に繋ぎ直す。"""

    def __init__(self, host=None, port=None, retry_sec=5.0, log=print):
        self.host = host or os.environ.get("HEAD_RELAY_HOST", "127.0.0.1")
        self.port = int(port or os.environ.get("HEAD_RELAY_PORT", 7997))
        self.retry_sec = retry_sec
        self.log = log
        self.sock = None
        self._last_try = 0.0
        self.n_sent = 0
        self.n_dropped = 0

    def _connect(self):
        """繋がっていなければ繋ぐ。失敗しても例外は出さない。"""
        if self.sock is not None:
            return True
        now = time.monotonic()
        if now - self._last_try < self.retry_sec:
            return False                    # 待ち時間の途中
        self._last_try = now
        try:
            s = socket.create_connection((self.host, self.port), timeout=3.0)
            s.settimeout(3.0)
            self.sock = s
            self.log(f"head_relay へ接続: {self.host}:{self.port}")
            return True
        except OSError as e:
            self.log(f"head_relay へ繋がらない（{self.retry_sec:.0f}s 後に再試行）: {e}")
            return False

    def send(self, pitch_deg, yaw_deg, roll_deg=0.0):
        """[pitch, yaw, roll] を度で送る。届かなくても落ちない。

        **送れなかったぶんは捨てる。** 溜めて後から流すと古い指令で首が
        動くことになる。次の周期の値のほうが常に正しい。
        """
        if not self._connect():
            self.n_dropped += 1
            return False
        line = json.dumps({
            "pitch": round(float(pitch_deg), 2),
            "yaw": round(float(yaw_deg), 2),
            "roll": round(float(roll_deg), 2),
        }) + "\n"
        try:
            self.sock.sendall(line.encode())
            self.n_sent += 1
            return True
        except OSError as e:
            self.log(f"送信に失敗した。繋ぎ直す: {e}")
            self.close()
            self.n_dropped += 1
            return False

    def close(self):
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None


class HeadController:
    def __init__(self, demo=False):
        self.client = HeadClient()
        self.demo = demo
        self.t0 = time.monotonic()

    def publish_goal(self, pitch_deg, yaw_deg, roll_deg=0):
        """[pitch, yaw, roll] を度で送る。"""
        self.client.send(pitch_deg, yaw_deg, roll_deg)

    def tick_demo(self):
        t = time.monotonic() - self.t0
        # 可動域 (pitch ±30, yaw ±60) の内側で振る
        self.publish_goal(15 * math.sin(t * 0.3), 40 * math.sin(t * 0.5))

    # ---- ここから先が本体。未実装 ----

    def decide(self, image, acoustic_map):
        """場面から「誰に向くか」を決める。

        入力は `gst/recv_ome.py` の `OmeInputs` から取る:
            inp.latest_video("stream")   場面
            inp.latest_video("soundmap") 誰が喋っているか
            inp.latest_audio("mic")      現場の音

        TODO:
          - 音響マップをそのまま VLM に食わせるか、前段で言語化するか
          - 推論周期と許容遅延（feasibility study なのでまず結果が出るか）
        """
        raise NotImplementedError


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true", help="正弦波を出して経路を確認する")
    ap.add_argument("--hz", type=float, default=10.0, help="送出周期")
    ap.add_argument("--seconds", type=float, default=0, help="0 なら止めるまで")
    a = ap.parse_args()

    node = HeadController(demo=a.demo)
    print("--demo: 正弦波を出している（経路確認用）" if a.demo
          else "待機中。VLM を繋いだら decide() から publish_goal() を呼ぶ",
          flush=True)

    period = 1.0 / a.hz
    t_start = time.monotonic()
    last_report = t_start
    try:
        while True:
            if a.demo:
                node.tick_demo()
            time.sleep(period)
            now = time.monotonic()
            if now - last_report >= 10.0:
                c = node.client
                print(f"  送信 {c.n_sent} / 捨てた {c.n_dropped} (10s)", flush=True)
                c.n_sent = c.n_dropped = 0
                last_report = now
            if a.seconds and now - t_start >= a.seconds:
                break
    except KeyboardInterrupt:
        pass
    finally:
        node.client.close()


if __name__ == "__main__":
    main()
