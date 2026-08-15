#!/usr/bin/env python3
"""OME から WebRTC で 1 本のストリームを受ける共通モジュール。

PC-D の 4 入力と、PC-B の操作者マイク受信の両方でこれを使う。
OME の出力は WebRTC か LLHLS しか無く、LLHLS は分割配信で数秒遅れるため、
対話にも推論にも WebRTC を使う。

依存は gst の `webrtcbin` と libsoup（`gi.repository.Soup`）。
**`gstreamer1.0-nice` が要る**（`sudo apt install gstreamer1.0-nice`）。
`libnice10` だけでは足りない。webrtcbin は libnice を直接リンクしているが、
ICE の実体は nicesrc / nicesink という gst エレメントで、これは別パッケージ。
入っていないと `libnice elements are not available` の警告だけ出て
`create-answer` が黙って失敗する（reply に answer が入らない）。

OME の signalling:
    1. ws://<host>:3333/<app>/<stream> に接続
    2. {"command": "request_offer"} を送る
    3. offer（sdp + candidates + iceServers）が返る
    4. answer を返す
    5. 以降 ICE candidate を交換

使い方:
    rx = OmeReceiver("127.0.0.1", 3333, "app", "boxiestream",
                     on_video=cb_video, on_audio=cb_audio)
    rx.start()          # 別スレッドで GLib MainLoop が回る
    ...
    rx.stop()

コールバックは gst のストリーミングスレッドから呼ばれる。重い処理を
その場でやらない（キューに積んで別スレッドで処理する）。

インスタンスごとに専用の GLib.MainContext を持つので、4 本を同一プロセスで
並行して受けられる（既定の MainContext を共有すると 2 本目以降が回らない）。
"""

import json
import os
import re
import socket
import threading

# GIO に proxy を探させない。**この 1 行を消すと ROS ノードから使ったときに
# プロセスごと落ちる。** GIO の既定の proxy resolver は libproxy を呼ぶが、
# libproxy は内部で C++ 例外を投げる。一方 rclpy は libunwind を読み込み、
# これが _Unwind_Resume を乗っ取るため巻き戻しに失敗し、
# std::terminate → abort（あるいは SIGSEGV）になる。
# gi.repository を読む前に置くこと（GIO はモジュールを遅延ロードする）。
# 繋ぎ先は LAN か loopback なので proxy はそもそも要らない。
os.environ.setdefault("GIO_USE_PROXY_RESOLVER", "dummy")

import gi  # noqa: E402

gi.require_version("Gst", "1.0")
gi.require_version("GstWebRTC", "1.0")
gi.require_version("GstSdp", "1.0")
gi.require_version("Soup", "2.4")
from gi.repository import Gio, GLib, Gst, GstSdp, GstWebRTC, Soup  # noqa: E402

# "candidate:<foundation> <component> <proto> <priority> <addr> <port> typ ..."
_CAND_RE = re.compile(
    r"^(candidate:\S+\s+\d+\s+\S+\s+\d+\s+)(\S+)(\s+\d+\s+typ\s+(\S+).*)$",
    re.IGNORECASE,
)


class OmeReceiver:
    def __init__(self, host, port, app, stream,
                 on_video=None, on_audio=None, logger=None,
                 prefer_signalling_host=True, use_turn=False,
                 latency_ms=100, retry_sec=5.0, video_format=None,
                 audio_caps=None):
        """
        prefer_signalling_host:
            OME が offer に載せてくる host candidate のアドレスを、signalling で
            繋いだホストのアドレスに書き換える。**既定で有効。**
            OME は起動時に一度だけ NIC を列挙してこのアドレスを決めるので、
            後から DHCP でアドレスが変わったり、ネットワークが上がる前に OME が
            起動していると、実在しないアドレスを配り続ける。そうなると SDP 交換は
            成功して OME 側にセッションも立つのに、メディアだけ永久に来ない。
            この系ではメディアは必ず signalling と同じホストから出る
            （OME は 10000-10004/udp を 0.0.0.0 で待ち受ける）ので、
            OME の申告よりこちらのほうが確実。
        use_turn:
            offer の iceServers（OME 内蔵 TURN, TCP）を使う。UDP が通らない
            ネットワーク用の逃げ道。既定は無効。
        latency_ms:
            webrtcbin の jitterbuffer。遠隔操作なので既定を短めにしてある。
        retry_sec:
            繋がらないときの再試行間隔。送出側（PC-B）がまだ起動していなくても
            待ち続けるので、起動順を気にしなくてよくなる。
        video_format:
            `"RGB"` のように指定すると appsink の直前で変換する。None なら変換しない。
        audio_caps:
            `"audio/x-raw,format=S16LE,rate=16000,channels=1"` のように指定すると
            appsink の直前で resample して揃える。OME から出る音声は Opus 由来の
            48 kHz なので、記録側のレートに落とすときに使う。None なら変換しない。
        """
        self.host = host
        self.url = f"ws://{host}:{port}/{app}/{stream}"
        self.name = stream
        self.on_video = on_video
        self.on_audio = on_audio
        self.log = logger or (lambda level, msg: print(f"[{level}] {msg}", flush=True))
        self.prefer_signalling_host = prefer_signalling_host
        self.use_turn = use_turn
        self.latency_ms = latency_ms
        self.retry_sec = retry_sec
        self.video_format = video_format
        self.audio_caps = audio_caps

        self.pipeline = None
        self.webrtc = None
        self.ws = None
        self.peer_id = None
        self.session_id = None
        self.loop = None
        self.context = None
        self.thread = None
        self.n_video = 0
        self.n_audio = 0
        self.connected = False

        self._session = None
        self._remote_set = False
        self._pending_ice = []
        self._stopping = False
        self._media_host = None
        self._retrying = False
        self._offer_timer = None

    # ---- 起動・停止 ----

    def start(self):
        Gst.init(None)
        self.context = GLib.MainContext.new()
        self.loop = GLib.MainLoop.new(self.context, False)
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        # このスレッドの thread-default にしてから Soup / bus watch を作る。
        # そうしないと既定の MainContext に紐づいてしまい、複数本を並行して
        # 受けたときに 2 本目以降がディスパッチされない。
        self.context.push_thread_default()
        try:
            self._connect()
            self.loop.run()
        finally:
            self.context.pop_thread_default()

    def _connect(self):
        if self._stopping:
            return
        # 直結を明示する。効いているのは冒頭の GIO_USE_PROXY_RESOLVER=dummy の
        # ほうで（これだけでは libproxy の読み込みを止められなかった）、
        # ここは意図を残すための念押し。
        self._session = Soup.Session(
            proxy_resolver=Gio.SimpleProxyResolver.new(None, None))
        msg = Soup.Message.new("GET", self.url)
        self._session.websocket_connect_async(
            msg, None, None, None, self._on_ws_connected)
        self.log("info", f"signalling へ接続: {self.url}")

    def stop(self):
        self._stopping = True
        if self.pipeline is not None:
            self.pipeline.set_state(Gst.State.NULL)
        if self.ws is not None:
            try:
                self.ws.close(Soup.WebsocketCloseCode.NORMAL, None)
            except Exception:
                pass
        if self.loop is not None:
            self.loop.quit()
        if self.thread is not None:
            self.thread.join(timeout=3.0)

    def _timeout(self, seconds, func):
        """自分の MainContext にタイマを付ける。

        `GLib.timeout_add_seconds()` は**既定の MainContext** に付いてしまい、
        ここでは誰もそれを回していないので永久に発火しない。
        （同じ理由で `GLib.source_remove()` も使えない。source を持って
        `destroy()` する。）
        """
        src = GLib.timeout_source_new_seconds(max(1, int(seconds)))
        src.set_callback(lambda *_a: func())
        src.attach(self.context)
        return src

    def _retry(self, why):
        """繋がらなかったので後でやり直す。送出側の起動待ちに使う。"""
        if self._stopping or self.retry_sec <= 0 or self._retrying:
            return
        self._retrying = True
        self._cancel_offer_timer()
        self.log("warn", f"{why} — {self.retry_sec:.0f}s 後に再試行")
        # 片付けはタイマまで遅らせる。WebSocket の signal ハンドラの中から
        # その WebSocket を捨てると解放済みの領域を触ることになる。
        self._timeout(self.retry_sec, self._on_retry_timer)

    def _on_retry_timer(self):
        self._teardown()
        self._retrying = False
        self._connect()
        return False        # 一度きり

    def _cancel_offer_timer(self):
        if self._offer_timer is not None:
            self._offer_timer.destroy()
            self._offer_timer = None

    def _on_offer_timeout(self):
        """request_offer に何も返ってこないまま時間が過ぎた。

        OME はたいてい 404 を返すが、無反応のこともあるので保険として置く。
        """
        self._offer_timer = None
        self._retry("offer が返ってこない（送出がまだ無い）")
        return False

    def _teardown(self):
        self._cancel_offer_timer()
        if self.ws is not None:
            try:
                self.ws.close(Soup.WebsocketCloseCode.NORMAL, None)
            except Exception:
                pass
        if self.pipeline is not None:
            self.pipeline.set_state(Gst.State.NULL)
        self.pipeline = None
        self.webrtc = None
        self.ws = None
        self._session = None
        self.peer_id = None
        self.session_id = None
        self._remote_set = False
        self._pending_ice = []
        self._media_host = None
        self.connected = False

    # ---- signalling ----

    def _on_ws_connected(self, session, result):
        try:
            self.ws = session.websocket_connect_finish(result)
        except Exception as e:
            self._retry(f"signalling に繋がらない: {e}")
            return
        self.log("info", "WebSocket 確立")
        self.ws.connect("message", self._on_ws_message)
        self.ws.connect("closed", self._on_ws_closed)
        self._send({"command": "request_offer"})
        self._offer_timer = self._timeout(
            max(2, int(self.retry_sec)), self._on_offer_timeout)

    def _on_ws_closed(self, ws):
        # 片付け済みの古い接続からの通知は捨てる。繋ぎ直した直後に前の
        # WebSocket の "closed" が届くと、繋がったばかりの接続を捨てて
        # 延々と再接続を繰り返すことになる。
        if ws is not self.ws:
            return
        if not self.connected:
            self._retry("signalling が切れた")
        else:
            self.log("warn", "signalling 切断")

    def _send(self, obj):
        if self.ws is not None:
            self.log("debug", f"-> {str(obj)[:120]}")
            self.ws.send_text(json.dumps(obj))

    def _on_ws_message(self, ws, msg_type, message):
        # 片付け待ち、あるいは古い接続からの取りこぼし
        if self._retrying or ws is not self.ws:
            return
        try:
            data = json.loads(message.get_data().decode())
        except Exception as e:
            self.log("error", f"signalling の解析に失敗: {e}")
            return

        self.log("debug", f"<- {str(data)[:160]}")

        # エラーは `command` を持たずに来る。
        #   {"code": 404, "error": "Cannot create offer"}
        # `command` だけを見ていると素通りして、何も起きないまま待ち続ける。
        # 404 は送出がまだ無いだけで、プロトコルの誤りではない。
        code = data.get("code")
        if code is not None and code != 200:
            self._retry(f"OME: {data.get('error', '')}（code={code}）"
                        f" — 送出側は生きているか")
            return

        cmd = data.get("command")

        if cmd == "offer":
            if "sdp" not in data:
                self._retry("offer に sdp が無い")
                return
            self._cancel_offer_timer()
            self.peer_id = data.get("peer_id")
            self.session_id = data.get("id")
            self._build_pipeline(data)
            self._handle_offer(data)
        elif cmd == "candidate":
            for c in data.get("candidates", []) or []:
                self._add_ice(c)
        elif cmd == "notification":
            self.log("debug", f"notification: {str(data)[:100]}")
        elif cmd in ("close", "stop", "error"):
            self.log("warn", f"OME から {cmd}: {str(data)[:160]}")
            self._retry(f"OME が {cmd} を返した")

    def _handle_offer(self, data):
        sdp_text = data["sdp"]["sdp"]
        self.log("debug", f"offer SDP:\n{sdp_text}")
        _, sdpmsg = GstSdp.SDPMessage.new()
        GstSdp.sdp_message_parse_buffer(sdp_text.encode(), sdpmsg)
        offer = GstWebRTC.WebRTCSessionDescription.new(
            GstWebRTC.WebRTCSDPType.OFFER, sdpmsg
        )
        # 候補は set-remote-description が終わってから入れる（_on_remote_set）。
        self._pending_ice = list(data.get("candidates", []) or [])
        promise = Gst.Promise.new_with_change_func(
            lambda p, _u: self._on_remote_set(p), None
        )
        self.webrtc.emit("set-remote-description", offer, promise)

    def _on_remote_set(self, promise):
        promise.wait()
        self._remote_set = True
        for c in self._pending_ice:
            self._add_ice(c)
        self._pending_ice = []
        promise2 = Gst.Promise.new_with_change_func(
            lambda p, _u: self._on_answer_created(p), None
        )
        self.webrtc.emit("create-answer", None, promise2)

    def _on_answer_created(self, promise):
        promise.wait()
        reply = promise.get_reply()
        answer = reply.get_value("answer") if reply else None
        if answer is None:
            # ほぼ確実に gstreamer1.0-nice が無い。冒頭の docstring を参照。
            self.log("error",
                     "create-answer が失敗した。gstreamer1.0-nice は入っているか "
                     "(gst-inspect-1.0 nicesrc)")
            self._retry("answer を作れない")
            return
        self.webrtc.emit("set-local-description", answer, Gst.Promise.new())
        self.log("debug", f"answer SDP:\n{answer.sdp.as_text()}")
        self._send({
            "command": "answer",
            "id": self.session_id,
            "peer_id": self.peer_id,
            "sdp": {"type": "answer", "sdp": answer.sdp.as_text()},
        })
        self.log("info", "answer を送った")

    # ---- ICE ----

    def _rewrite_candidate(self, cand):
        """host candidate のアドレスを signalling したホストのものに差し替える。

        差し替えないと、OME が起動時に覚えた古いアドレスへ延々と
        connectivity check を投げることになる（実際にそれで詰まった）。
        """
        if not self.prefer_signalling_host or not self._media_host:
            return cand
        m = _CAND_RE.match(cand)
        if not m or m.group(4).lower() != "host":
            return cand
        addr = m.group(2)
        if addr == self._media_host or ":" in addr:     # IPv6 はそのまま
            return cand
        new = m.group(1) + self._media_host + m.group(3)
        if addr != self._media_host:
            self.log("info", f"候補のアドレスを差し替え: {addr} -> {self._media_host}")
        return new

    def _add_ice(self, c):
        if not c:
            return
        cand = c.get("candidate") if isinstance(c, dict) else c
        if not cand:
            return
        mline = c.get("sdpMLineIndex", 0) if isinstance(c, dict) else 0
        self.webrtc.emit("add-ice-candidate", mline, self._rewrite_candidate(cand))

    def _on_ice_candidate(self, _webrtc, mline, candidate):
        self._send({
            "command": "candidate",
            "id": self.session_id,
            "peer_id": self.peer_id,
            "candidates": [{"candidate": candidate, "sdpMLineIndex": mline}],
        })

    def _resolve_media_host(self):
        try:
            return socket.gethostbyname(self.host)
        except Exception:
            return self.host

    def _rewrite_turn_host(self, rest):
        """`host:port?transport=tcp` の host を signalling で繋いだ先に替える。

        candidate と同じ理由。OME が iceServers に載せてくるのも起動時に
        列挙した住所なので、そのまま使うと届かない。SSH のポート転送や
        VPN 越しに使うときは**必ず**こちらでないと繋がらない
        （OME は自分がトンネルの向こうから見えている住所を知らない）。
        """
        if not self.prefer_signalling_host or not self._media_host:
            return rest
        if rest.startswith("["):                 # [IPv6]:port
            end = rest.find("]")
            host, tail = rest[: end + 1], rest[end + 1:]
        else:
            host, sep, rem = rest.partition(":")
            tail = sep + rem
        if host == self._media_host:
            return rest
        self.log("info", f"TURN のアドレスを差し替え: {host} -> {self._media_host}")
        return self._media_host + tail

    def _setup_turn(self, data):
        servers = data.get("iceServers") or data.get("ice_servers") or []
        seen = set()
        for s in servers:
            user = s.get("username") or s.get("user_name") or ""
            cred = s.get("credential") or ""
            for url in s.get("urls", []) or []:
                # turn:host:port?transport=tcp -> turn://user:cred@host:port?transport=tcp
                if not url.startswith("turn:"):
                    continue
                rest = self._rewrite_turn_host(url[len("turn:"):])
                if rest in seen:      # 書き換えると複数の URL が同じ物になる
                    continue
                seen.add(rest)
                uri = (f"turn://{GLib.uri_escape_string(user, None, False)}:"
                       f"{GLib.uri_escape_string(cred, None, False)}@{rest}")
                if self.webrtc.emit("add-turn-server", uri):
                    self.log("info", f"TURN を追加: {rest}")

    # ---- メディア ----

    def _build_pipeline(self, data):
        if self.pipeline is not None:
            return
        self._media_host = self._resolve_media_host()
        self.pipeline = Gst.Pipeline.new(f"recv_{self.name}")
        self.webrtc = Gst.ElementFactory.make("webrtcbin", "recv")
        if self.webrtc is None:
            self.log("error", "webrtcbin が無い")
            return
        self.webrtc.set_property("bundle-policy", "max-bundle")
        self.webrtc.set_property("latency", self.latency_ms)
        self.pipeline.add(self.webrtc)
        self.webrtc.connect("on-ice-candidate", self._on_ice_candidate)
        self.webrtc.connect("pad-added", self._on_pad_added)
        self.webrtc.connect("notify::ice-connection-state", self._on_ice_state)
        self.webrtc.connect("notify::connection-state", self._on_conn_state)
        if self.use_turn:
            self._setup_turn(data)

        bus = self.pipeline.get_bus()
        bus.add_watch(GLib.PRIORITY_DEFAULT, self._on_bus, None)
        self.pipeline.set_state(Gst.State.PLAYING)

    def _on_bus(self, _bus, msg, _u):
        if msg.type == Gst.MessageType.ERROR:
            err, dbg = msg.parse_error()
            self.log("error", f"gst: {err} / {dbg}")
        elif msg.type == Gst.MessageType.WARNING:
            err, dbg = msg.parse_warning()
            self.log("warn", f"gst: {err} / {dbg}")
        return True

    def _on_ice_state(self, obj, _param):
        state = obj.get_property("ice-connection-state")
        self.log("info", f"ICE: {state.value_nick}")
        if state.value_nick in ("failed", "disconnected"):
            self._retry(f"ICE が {state.value_nick}")

    def _on_conn_state(self, obj, _param):
        state = obj.get_property("connection-state")
        self.log("info", f"PeerConnection: {state.value_nick}")
        if state.value_nick == "connected":
            self.connected = True

    def _on_pad_added(self, _webrtc, pad):
        """受信した track を復号して appsink まで繋ぐ。

        OME からは映像 / 音声のどちらか、あるいは両方が来る。caps を見て
        分岐する。decodebin に任せると codec を問わず扱える。
        """
        if pad.get_direction() != Gst.PadDirection.SRC:
            return
        decode = Gst.ElementFactory.make("decodebin")
        decode.connect("pad-added", self._on_decoded)
        self.pipeline.add(decode)
        decode.sync_state_with_parent()
        pad.link(decode.get_static_pad("sink"))

    def _on_decoded(self, _bin, pad):
        caps = pad.get_current_caps().to_string()
        is_video = caps.startswith("video/")
        chain = [Gst.ElementFactory.make("videoconvert" if is_video else "audioconvert")]
        want = None
        if is_video and self.video_format:
            want = f"video/x-raw,format={self.video_format}"
        elif not is_video and self.audio_caps:
            chain.append(Gst.ElementFactory.make("audioresample"))
            want = self.audio_caps
        if want:
            filt = Gst.ElementFactory.make("capsfilter")
            filt.set_property("caps", Gst.Caps.from_string(want))
            chain.append(filt)
        sink = Gst.ElementFactory.make("appsink")
        sink.set_property("emit-signals", True)
        sink.set_property("sync", False)
        sink.set_property("max-buffers", 5)
        sink.set_property("drop", True)      # 受信側は落としてでも遅れない
        sink.connect("new-sample", self._on_sample, is_video)
        chain.append(sink)
        for e in chain:
            self.pipeline.add(e)
            e.sync_state_with_parent()
        for a, b in zip(chain, chain[1:]):
            a.link(b)
        pad.link(chain[0].get_static_pad("sink"))
        self.log("info", f"{'映像' if is_video else '音声'} を受信: {caps[:80]}")

    def _on_sample(self, sink, is_video):
        sample = sink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.ERROR
        buf = sample.get_buffer()
        ok, info = buf.map(Gst.MapFlags.READ)
        if not ok:
            return Gst.FlowReturn.ERROR
        try:
            if is_video:
                self.n_video += 1
                if self.on_video:
                    self.on_video(bytes(info.data), sample)
            else:
                self.n_audio += 1
                if self.on_audio:
                    self.on_audio(bytes(info.data), sample)
        except Exception as e:
            self.log("error", f"コールバックで例外: {e}")
        finally:
            buf.unmap(info)
        return Gst.FlowReturn.OK


if __name__ == "__main__":
    import argparse
    import time

    ap = argparse.ArgumentParser(description="OME から 1 本受けて統計を出す")
    ap.add_argument("stream")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=3333)
    ap.add_argument("--app", default="app")
    ap.add_argument("--seconds", type=float, default=10.0)
    ap.add_argument("--turn", action="store_true", help="OME 内蔵 TURN(TCP) を使う")
    ap.add_argument("--keep-candidates", action="store_true",
                    help="OME の候補アドレスを書き換えない")
    ap.add_argument("--snapshot", metavar="PATH",
                    help="最初の 1 フレームを PPM で保存して中身を確かめる")
    ap.add_argument("-v", "--verbose", action="store_true", help="SDP まで出す")
    a = ap.parse_args()

    def logger(level, msg):
        if level == "debug" and not a.verbose:
            return
        print(f"[{level}] {msg}", flush=True)

    saved = []

    def on_video(data, sample):
        if not a.snapshot or saved:
            return
        s = sample.get_caps().get_structure(0)
        w, h = s.get_value("width"), s.get_value("height")
        with open(a.snapshot, "wb") as f:
            f.write(f"P6\n{w} {h}\n255\n".encode())
            f.write(data[:w * h * 3])
        saved.append(True)
        print(f"[info] {a.snapshot} に保存した（{w}x{h}）", flush=True)

    rx = OmeReceiver(a.host, a.port, a.app, a.stream,
                     on_video=on_video if a.snapshot else None,
                     logger=logger, use_turn=a.turn,
                     prefer_signalling_host=not a.keep_candidates,
                     video_format="RGB" if a.snapshot else None)
    rx.start()
    t0 = time.time()
    last = (0, 0)
    while time.time() - t0 < a.seconds:
        time.sleep(2.0)
        el = time.time() - t0
        dv, da = rx.n_video - last[0], rx.n_audio - last[1]
        last = (rx.n_video, rx.n_audio)
        print(f"  {el:4.1f}s  映像 {rx.n_video} フレーム ({dv/2:.0f} fps)  "
              f"音声 {rx.n_audio} バッファ ({da/2:.0f}/s)", flush=True)
    rx.stop()
    print("受信なし。送出は生きているか、ICE は connected まで行ったかを見る"
          if rx.n_video == 0 and rx.n_audio == 0 else "OK")
