#!/bin/bash
# OME はこの機械にネイティブで入っており、systemd で常駐している。
# 起動スクリプトは要らない。ここでは状態確認と設定の場所だけまとめる。
source "$(dirname "$0")/../env.sh"

echo "=== サービス ==="
systemctl status ovenmediaengine --no-pager | head -4
echo
echo "=== ポート ==="
ss -tln | grep -E ":1935|:3333|:8081" || echo "  待ち受けていない"
echo
echo "=== 設定 ==="
echo "  /usr/share/ovenmediaengine/conf/Server.xml"
echo "  application: ${OME_APP}"
echo "  入力  rtmp://<PC-C>:${OME_RTMP_PORT}/${OME_APP}/${STREAM_KEY_MAIN}"
echo "        rtmp://<PC-C>:${OME_RTMP_PORT}/${OME_APP}/${STREAM_KEY_SOUNDMAP}"
echo "  出力  ws://localhost:${OME_WS_PORT}/${OME_APP}/<stream_key>"
echo
echo "  再起動: sudo systemctl restart ovenmediaengine"
