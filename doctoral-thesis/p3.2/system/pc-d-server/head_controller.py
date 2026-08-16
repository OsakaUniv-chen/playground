#!/usr/bin/env python3
"""頭部の向きを決めて PC-B へ送る。

**現状は骨格だけ。** `decide()` は未実装で、既定では何も送らない。
最小 demo は遠隔操作だけなので、この経路は demo に含まれない。

    ここ ── TCP/JSON ──> PC-C の head_relay.py ── ROS ──> PC-B の head_driver

**ROS は使わない。** PC-D は理研にあって PC-B と同じ LAN に居らず、
distro も違う（PC-D は galactic、PC-B / PC-C は humble で既定の RMW が
別物）。跨ぐのは 10 Hz・整数 3 個の指令 1 本だけなので、DDS を通さず
素の TCP で PC-C へ送り、向こうで ROS に載せ替える。おかげで
**この機械に ROS を入れる必要が無い**（受信側 recv_ome.py も rclpy 不要）。

接続先は PC-C から張った SSH トンネルの出口なので既定は 127.0.0.1。
`HEAD_RELAY_HOST` / `HEAD_RELAY_PORT` で変えられる。

可動域制限と smoothing は PC-B の head_driver が掛ける。**こちらは
制限を知らずに指令を出してよい。** 適用後の値は topic に出ないので、
必要なら head_driver のパラメータから事後に再現する。

記録の観点:
    ここが出す指令は「モデルの判断」。操作者がゲームパッドで出す台車指令とは
    別 topic なので、bag の中で自然に区別できる。両方を混ぜないこと。
"""

import json
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
    _last_goal = None

    def __init__(self):
        self.client = HeadClient()

    def publish_goal(self, pitch_deg, yaw_deg, roll_deg=0):
        """[pitch, yaw, roll] を度で送る。**値が変わったときだけ送る。**

        VLM を挟むと推論時間ぶん間隔が延びるので、固定周期では出せない。
        頭部指令には watchdog が無く（台車と違って「止め忘れ」が危険では
        ないため）、TCP なので取りこぼしも無いので、変化時だけ送れば足りる。
        PC-B 側の smoothing が間を埋める。

        roll は PC-B が受け取っても無視する（通電して初期位置に保つだけ）。
        """
        goal = (round(float(pitch_deg), 2), round(float(yaw_deg), 2),
                round(float(roll_deg), 2))
        if goal == self._last_goal:
            return
        self._last_goal = goal
        self.client.send(*goal)

    # ---- ここから先が本体。未実装 ----

    def decide(self, image, acoustic_map):
        """場面から「誰に向くか」を決める。

        入力は `recv_ome.py` の `OmeInputs` から取る:
            inp.latest_video("stream")   場面
            inp.latest_video("soundmap") 誰が喋っているか
            inp.latest_audio("mic")      現場の音

        TODO:
          - 音響マップをそのまま VLM に食わせるか、前段で言語化するか
          - 推論周期と許容遅延（feasibility study なのでまず結果が出るか）
        """
        raise NotImplementedError


if __name__ == "__main__":
    raise SystemExit(
        "decide() が未実装なので、単体で起動しても送るものが無い。\n"
        "VLM を繋ぐときは decide() を実装し、その結果を publish_goal() に渡す。"
    )
