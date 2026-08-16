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

接続先は **PC-C の tailscale アドレス**（`config.env` の `HEAD_RELAY_HOST` /
`HEAD_RELAY_PORT`）。**既定値は持たない** ── 未設定なら起動時に落ちる。
以前は PC-C から張った SSH トンネルの出口（127.0.0.1）を指していたが、
Tailscale で直接繋がるようになってトンネルは要らなくなった。

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
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import soundmap_geometry  # noqa: E402


class HeadClient:
    """PC-C の head_relay へ繋ぎ、切れたら勝手に繋ぎ直す。"""

    def __init__(self, host=None, port=None, retry_sec=5.0, log=print):
        # 既定値は置かない（env.sh 必須）。config.env と二重に持つと片方だけ古くなる。
        self.host = host or os.environ["HEAD_RELAY_HOST"]
        self.port = int(port or os.environ["HEAD_RELAY_PORT"])
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
            # **半開きの接続を TCP に検出させる。** ここは値が変わったときしか
            # 送らないので、相手が消えても何分も気付かない。しかも半開きの
            # ソケットは送信バッファが埋まるまで **エラー無しで書けてしまう**
            # ので、送ったつもりの指令が黙って消える。keepalive を入れておくと
            # 次の send で失敗し、繋ぎ直せる（PC-C の head_relay 側にも同じ
            # 設定を入れてある）。
            s.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            for opt, val in (("TCP_KEEPIDLE", 30), ("TCP_KEEPINTVL", 10),
                             ("TCP_KEEPCNT", 3)):
                if hasattr(socket, opt):
                    s.setsockopt(socket.IPPROTO_TCP, getattr(socket, opt), val)
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
    """頭部の目標角を決めて送る。

    **角度の持ち方に 1 つだけ約束がある。** マップも映像も**頭部に載った**
    センサから来るので、そこから読める向きは「いまの頭からの相対」。
    一方 `head/command` は**絶対角**（起動時の姿勢が 0）。したがって

        次の絶対角 = いまの絶対角 + 相対角

    で、`いまの絶対角` は **PC-D が自分で覚えておく**しかない ── PC-B は
    モータの読み戻しをしない（BLE の帯域。設計 §5.1）。`self.pitch` /
    `self.yaw` がその控え。`publish_goal` を通せば自動で更新される。
    """

    def __init__(self):
        self.client = HeadClient()
        self._last_goal = None
        # いま出している絶対角（起動時の姿勢が 0）。実際のモータは PC-B の
        # smoothing で遅れて追いつくので、**これは「目標」であって
        # 「現在値」ではない**。次の判断まで待つ根拠は decide() を参照。
        self.pitch = 0.0
        self.yaw = 0.0
        self.t_last_goal = 0.0

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
        self.pitch, self.yaw = goal[0], goal[1]
        self.t_last_goal = time.monotonic()
        self.client.send(*goal)

    def look_relative(self, d_yaw_deg, d_pitch_deg):
        """**いまの頭から見た相対角**で指示する。

        マップや映像から読めるのはこちら（頭に載ったセンサなので）。
        絶対角への足し込みと控えの更新はここでやる。
        """
        self.publish_goal(self.pitch + d_pitch_deg, self.yaw + d_yaw_deg)

    def settled(self, margin_sec=0.5):
        """前の指令に頭が追いついたか。

        PC-B は EMA（既定 alpha=0.25 @10 Hz、時定数 0.35 s）で寄せるので、
        可動域いっぱいでも約 1.5 秒で収まる。**落ち着く前に次の相対角を
        足すと、まだ動いている途中の姿勢を「いまの角度」とみなすことに
        なり、ずれが足し算で溜まる。**
        """
        return time.monotonic() - self.t_last_goal > (1.5 + margin_sec)

    # ---- ここから先が本体。未実装 ----

    def decide(self, image, acoustic_map, transcript):
        """場面から「誰に向くか」を決める。**戻り値は相対角 (d_yaw, d_pitch)。**

        入力は 3 系統そろっている:

            inp.latest_video("stream")     場面（頭部のカメラ）
            inp.latest_video("soundmap")   誰が鳴っているか（64x64）
            asr.Transcriber.text()         直近 ASR_CONTEXT_SEC 秒の発話

        呼び出し側の骨格:

            while True:
                if not head.settled():          # 前の指令が収まるまで待つ
                    time.sleep(0.1); continue
                f  = inp.latest_video("stream")
                sm = inp.latest_video("soundmap")
                if f is None or f.age_sec() > 0.5:
                    continue                    # 古い画では判断しない
                d = head.decide(f.array(), sm.array(), tr.text())
                if d is not None:
                    head.look_relative(*d)

        **戻り値を絶対角にしないこと。** マップも映像も頭部に載った
        センサから来るので、そこから読めるのは相対角しかない
        （soundmap_geometry.py 冒頭）。

        決めていないのは次の 3 つ:

        1. **音響マップの渡し方。** 画像のまま VLM に見せるか、
           `soundmap_geometry.peak()` で方向に直してから言葉で渡すか。
           P6 の実測では 32B-AWQ が音響マップの画像から方向を 91.7% で
           読めているが、あれは方向 4 クラスの分類。ここは連続角なので、
           **前段で角度に直して「右 33 度から声」と渡すほうが素直**
           （マップの分解能は 64x64 = 約 2.8 度/画素しか無い）。
        2. **推論周期。** 上の骨格だと「頭が落ち着いてから次」なので
           最短でも 1.5 秒に 1 回。VLM の推論時間がこれより長ければ
           そちらが律速になる。
        3. **向く相手が居ないときの振る舞い。** 正面へ戻すのか、
           そのまま保つのか。回線が切れたときの扱いと合わせて決める
           （todo-list の「回線が切れたときの頭部の挙動」）。

        `None` を返すと「今回は動かさない」。
        """
        raise NotImplementedError

    # ---- VLM が出来るまでの間、経路を通して試すための当て馬 ----

    def decide_loudest(self, image, acoustic_map, transcript):
        """**いちばん強く鳴っている方を向くだけ。** VLM ではない。

        頭部の制御の輪（マップ -> 角度 -> PC-C -> PC-B -> モータ）を、
        VLM が出来る前に実機で 1 度通しておくためのもの。誰が喋っているか
        の判断は一切していないので、**これを demo に使わないこと。**
        """
        if acoustic_map is None:
            return None
        found = soundmap_geometry.peak(acoustic_map)
        if found is None:
            return None
        row, col = found
        return soundmap_geometry.index_to_angles(row, col)


if __name__ == "__main__":
    raise SystemExit(
        "decide() が未実装なので、単体で起動しても送るものが無い。\n"
        "VLM を繋ぐときは decide() を実装し、その結果を publish_goal() に渡す。"
    )
