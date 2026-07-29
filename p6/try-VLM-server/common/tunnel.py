#!/usr/bin/env python3
"""SSH 端口転送(本機 LOCAL_PORT -> 3090 の server ポート)。

3090PC は Riken 内網にあり、網関 `Riken` を二段跳び(ProxyJump)する。二段の密码が
違う(網関 grp / 目標 chen)ので、単一 sshpass では喂せない -> 入れ子 sshpass:
外層が chen 密码を 3090PC に、ProxyCommand 内層が grp 密码を網関に喂す。全自動・
遠端零安装。server は 127.0.0.1 のみ監听し、外網には晒さない。

密码は既定で下記定数、環境変数で上書き可: RIKEN_GRP_PASS / PC3090_CHEN_PASS。
"""
import os
import shlex
import socket
import subprocess
import time

DEFAULT_IP = "192.168.3.68"
RIKEN_ALIAS = "Riken"
GRP_PASS = os.environ.get("RIKEN_GRP_PASS", "make rob")
CHEN_PASS = os.environ.get("PC3090_CHEN_PASS", "1")


def start_tunnel(ip, local_port, remote_port):
    """入れ子 sshpass 二段跳びで -L 端口転送を張り、後台 ssh プロセスを返す。"""
    proxy = ("ProxyCommand=sshpass -p " + shlex.quote(GRP_PASS) +
             " ssh -W %h:%p"
             " -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "
             + RIKEN_ALIAS)
    cmd = [
        "sshpass", "-p", CHEN_PASS, "ssh", "-N", "-T",
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "ExitOnForwardFailure=yes",
        "-o", "User=chen",
        "-o", proxy,
        "-L", "%d:127.0.0.1:%d" % (local_port, remote_port),
        ip,
    ]
    return subprocess.Popen(cmd)


def wait_port(port, timeout=25.0):
    """本機 port が繋がる(隧道就绪)まで轮询。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            s = socket.create_connection(("127.0.0.1", port), timeout=1.0)
            s.close()
            return True
        except OSError:
            time.sleep(0.3)
    return False
