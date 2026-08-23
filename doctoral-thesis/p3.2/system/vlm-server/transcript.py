#!/usr/bin/env python3
"""从 stream-server 拉最近 N 秒的转写。

**转写不在这台机器上做**（架构 §3、§5.2）—— 3090 的 24 GB 要整块留给 VLM，
放不下 whisper 的 1.8 GB。所以 ASR 跑在 stream-server 上，这边在**要判断的那
一刻**去拉一次。

**为什么是「拉」不是「推」：** 消费的时机就是判断的时机，拉出来天然就是
「截至此刻的最近 N 秒」；窗长是这边的查询参数，两台机器之间零协调；窗在服务端
切，所以只有一个时钟参与；这台机器挂了也不用在任何地方积压。

**只用标准库。** 一个 GET、几 KB 的 JSON，犯不上引 requests。

用法:
    tc = TranscriptClient()
    print(tc.text(60))          # 直接能塞进 prompt 的字符串
    r = tc.fetch(60)            # 要看每一路状态就用这个
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request


def env(key):
    try:
        return os.environ[key]
    except KeyError:
        raise SystemExit(
            f"[error] {key} 未设置 —— 没读到 config.env。用 ./run.sh 启动。"
        ) from None


class TranscriptClient:
    """`GET /transcript?seconds=N` 的客户端。

    **拉不到不抛异常，返回一个 ok=False 的结果。** VLM 那边不该因为转写这一路
    断了就整个停摆 —— 画面和声音图还在，凭它们也能判断。是「没人说话」还是
    「转写坏了」，看返回里的 `ok`。
    """

    def __init__(self, url=None, timeout=None):
        self.url = url or env("TRANSCRIPT_URL")
        self.timeout = float(timeout or env("TRANSCRIPT_TIMEOUT"))
        self.n_ok = 0
        self.n_fail = 0
        self.last_error = None

    def fetch(self, seconds=None):
        """拉一次。返回的 dict 一定有 `ok` / `utterances` / `sources` 三个键。

        服务端的形状（成功时）:
            {"ok": true, "t": ..., "seconds": 60,
             "sources": {"onboard": {"ok": true, "age": .., "shape": ..}, ...},
             "utterances": [{"source":.., "text":.., "t_start":.., "t_end":..}]}
        """
        url = self.url
        if seconds is not None:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}seconds={seconds}"
        try:
            with urllib.request.urlopen(url, timeout=self.timeout) as r:
                data = json.loads(r.read().decode())
            self.n_ok += 1
            self.last_error = None
            # 服务端就算回了别的形状，也保证这三个键在，调用方不用到处判空
            data.setdefault("ok", True)
            data.setdefault("utterances", [])
            data.setdefault("sources", {})
            return data
        except (urllib.error.URLError, OSError, ValueError, TimeoutError) as e:
            self.n_fail += 1
            self.last_error = str(e)
            return {"ok": False, "error": str(e), "t": time.time(),
                    "utterances": [], "sources": {}}

    def text(self, seconds=None, with_time=False):
        """拼成一个字符串，直接能塞进 prompt。

        **没人说话就是空字符串 —— 定下来就这样。** 不塞「（无发话）」之类的
        占位符：要不要把静默告诉 VLM、怎么措辞，是造 prompt 那一侧的决定。
        转写坏了同样返回空 —— **要区分这两种就看 `fetch()["ok"]`**，
        它们在 prompt 里该有不同的说法。
        """
        lines = []
        for u in self.fetch(seconds).get("utterances", []):
            if with_time:
                ts = time.strftime("%H:%M:%S", time.localtime(u["t_start"]))
                lines.append(f"[{ts}] {u['source']}: {u['text']}")
            else:
                lines.append(f"{u['source']}: {u['text']}")
        return "\n".join(lines)


def main():
    import argparse

    ap = argparse.ArgumentParser(description="从 stream-server 拉转写")
    ap.add_argument("--seconds", type=float, default=None)
    ap.add_argument("--watch", type=float, default=0.0,
                    help="每这么多秒拉一次，一直拉（0 = 只拉一次）")
    ap.add_argument("--raw", action="store_true", help="打印原始 JSON")
    a = ap.parse_args()

    tc = TranscriptClient()
    print(f"[info] {tc.url}（超时 {tc.timeout:.0f}s）", flush=True)
    while True:
        r = tc.fetch(a.seconds)
        if a.raw:
            print(json.dumps(r, ensure_ascii=False, indent=2), flush=True)
        elif not r["ok"]:
            print(f"  ✗ 拉不到: {r.get('error')}", flush=True)
        else:
            bad = [k for k, v in r["sources"].items() if not v.get("ok")]
            head = f"  最近 {r.get('seconds')}s: {len(r['utterances'])} 句"
            if bad:
                head += f"  ★ 这几路没数据: {' '.join(bad)}"
            print(head, flush=True)
            for u in r["utterances"]:
                print(f"    {u['source']:9s} {u['text']}", flush=True)
        if a.watch <= 0:
            break
        time.sleep(a.watch)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
