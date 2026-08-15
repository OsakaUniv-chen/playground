#!/usr/bin/env bash
#
# サイネージ端末
#
#   複数の Chrome ウィンドウを全画面で常駐させ、DWELL 秒ごとに次のページを
#   最前面に出す。ウィンドウは起動時に一度だけ立ち上げ、以降は最前面化するだけなので
#   切り替えは瞬時に終わり、アニメーションも途切れない。
#
#   使い方:  ./signage.sh          通常起動
#            ./signage.sh --check  依存関係と URL の疎通だけ確認して終了
#            ./signage.sh --stop   起動中のサイネージを停止
#
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ============================================================
# 設定
# ============================================================
CONF="${SCRIPT_DIR}/urls.conf"

DWELL=10               # 1 ページを表示し続ける秒数
ZOOM=1.0               # ページの拡大率
SUPPRESS_NOTIFICATIONS=1   # 実行中はデスクトップ通知のバナーを止める
LOAD_WAIT=20           # 起動時にウィンドウの出現を待つ上限（秒）
BROWSER="google-chrome"

PROFILE_ROOT="${HOME}/.cache/signage-profiles"
PIDFILE="${HOME}/.cache/signage.pids"
CLASS_PREFIX="signage"

# 解像度は自動検出する。全画面表示なのでウィンドウの初期サイズにしか使わないが、
# 現地のディスプレイを手元で再現したい場合は環境変数で上書きできる:
#   SIGNAGE_W=1920 SIGNAGE_H=1080 ./signage.sh
SCREEN_W="${SIGNAGE_W:-}"
SCREEN_H="${SIGNAGE_H:-}"

# ============================================================
# 共通処理
# ============================================================
log() { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*"; }
die() { printf '[エラー] %s\n' "$*" >&2; exit 1; }

# URL の # はフラグメント（例: map.html#5/34.5/137/&contents=forecast）なので、
# 単純に # 以降を切り落とすと URL が壊れる。行頭の # だけをコメント行とし、
# 行内コメントは「空白に続く #」に限る。
read_urls() {
    [[ -f "$CONF" ]] || die "URL リストが見つかりません: $CONF"
    URLS=()
    local line
    while IFS= read -r line || [[ -n "$line" ]]; do
        line="${line#"${line%%[![:space:]]*}"}"     # 先頭の空白を除去
        line="${line%"${line##*[![:space:]]}"}"     # 末尾の空白を除去
        [[ -z "$line" || "$line" == "#"* ]] && continue
        line="${line%%[[:space:]]#*}"               # 行内コメントを除去
        line="${line%"${line##*[![:space:]]}"}"
        URLS+=("$line")
    done < "$CONF"
    [[ ${#URLS[@]} -gt 0 ]] || die "URL が 1 つも定義されていません: $CONF"
}

# ターミナルが DISPLAY を引き継いでいない場合（VSCode の統合ターミナル、SSH 等）、
# 起動中の X サーバが 1 つだけなら、それを使う。
ensure_display() {
    [[ -n "${DISPLAY:-}" ]] && return 0
    local socks=(/tmp/.X11-unix/X*)
    [[ -e "${socks[0]}" ]] || return 1
    (( ${#socks[@]} == 1 )) || return 1
    export DISPLAY=":${socks[0]##*/X}"
    [[ -z "${XAUTHORITY:-}" && -f "$HOME/.Xauthority" ]] && export XAUTHORITY="$HOME/.Xauthority"
    xdpyinfo >/dev/null 2>&1 || { unset DISPLAY; return 1; }
    return 0
}

# 通知バナーはサイネージ画面に被るので、実行中だけ止めて終了時に元の設定へ戻す。
# 起動時に出る「〜 is ready」のほか、更新やカレンダーの通知も対象になる。
NOTIF_SAVED=""
suppress_notifications() {
    (( SUPPRESS_NOTIFICATIONS )) || return 0
    command -v gsettings >/dev/null || return 0
    local uid; uid=$(id -u)
    [[ -z "${DBUS_SESSION_BUS_ADDRESS:-}" && -S "/run/user/${uid}/bus" ]] &&
        export DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/${uid}/bus"

    NOTIF_SAVED=$(gsettings get org.gnome.desktop.notifications show-banners 2>/dev/null)
    [[ -n "$NOTIF_SAVED" ]] || return 0
    gsettings set org.gnome.desktop.notifications show-banners false 2>/dev/null
}

restore_notifications() {
    [[ -n "$NOTIF_SAVED" ]] || return 0
    gsettings set org.gnome.desktop.notifications show-banners "$NOTIF_SAVED" 2>/dev/null
    NOTIF_SAVED=""
}

# 接続中のディスプレイの解像度を取得する（primary を優先）
detect_screen() {
    [[ -n "$SCREEN_W" && -n "$SCREEN_H" ]] && return 0
    local geo
    geo=$(xrandr 2>/dev/null | grep -m1 " connected primary" | grep -oE '[0-9]+x[0-9]+')
    [[ -z "$geo" ]] && geo=$(xrandr 2>/dev/null | grep -m1 " connected" | grep -oE '[0-9]+x[0-9]+')
    [[ "$geo" =~ ^([0-9]+)x([0-9]+)$ ]] || return 1
    SCREEN_W="${BASH_REMATCH[1]}"
    SCREEN_H="${BASH_REMATCH[2]}"
    return 0
}

# ============================================================
# --check : GUI なしで実行できる事前確認
# ============================================================
do_check() {
    local ng=0

    echo "--- 依存コマンド ---"
    for cmd in "$BROWSER" wmctrl xdotool; do
        if command -v "$cmd" >/dev/null; then
            echo "  OK       $cmd"
        else
            echo "  不足     $cmd"
            ng=1
        fi
    done

    echo "--- 表示環境 ---"
    local had_display="${DISPLAY:-}"
    if ensure_display; then
        if [[ -n "$had_display" ]]; then
            echo "  OK       DISPLAY=$DISPLAY"
        else
            echo "  OK       DISPLAY=$DISPLAY（このターミナルは引き継いでいないので自動設定）"
        fi
        echo "  OK       WM: $(wmctrl -m 2>/dev/null | sed -n 's/^Name: //p')"
    else
        echo "  不足     X サーバに接続できません。デスクトップ（X11）のターミナルから実行してください"
        ng=1
    fi
    if [[ -n "${WAYLAND_DISPLAY:-}" ]]; then
        echo "  注意     Wayland セッションです。wmctrl / xdotool は動作しない可能性があります"
    fi

    echo "--- URL の疎通 ---"
    read_urls
    for url in "${URLS[@]}"; do
        local code
        code=$(curl -s -o /dev/null -w '%{http_code}' -m 10 -A 'Mozilla/5.0' "$url" 2>/dev/null)
        if [[ "$code" == "200" ]]; then
            echo "  OK       $url"
        else
            echo "  応答 $code  $url"
        fi
    done

    echo "--- 表示設定 ---"
    if detect_screen; then
        echo "  解像度   ${SCREEN_W}x${SCREEN_H}$( [[ -n "${SIGNAGE_W:-}" ]] && echo "（環境変数で指定）" || echo "（自動検出）" )"
    else
        echo "  解像度   検出できません"
        ng=1
    fi
    echo "  表示     全画面で 1 ページずつ、${DWELL} 秒ごとに切り替え"
    echo "  一巡     ${#URLS[@]} ページ × ${DWELL} 秒 = $(( ${#URLS[@]} * DWELL )) 秒"

    [[ $ng -eq 0 ]] && echo "=> 実行可能です" || echo "=> 不足しているものがあります"
    return $ng
}

# ============================================================
# --stop : 起動中のウィンドウを終了
# ============================================================
# google-chrome はラッパースクリプトなので $! が実体とは限らない。
# プロファイルのパスで照合して落とす。
#
# 全プロセスにまとめて TERM を送ると、browser が先に死んで zygote や
# gpu-process が孤児として残ることがある。まず browser（--type= を持たない
# プロセス）だけを終了させ、子プロセスの後始末は Chrome 自身に任せる。
do_stop() {
    local pat="--user-data-dir=${PROFILE_ROOT}"
    local p i

    for p in $(pgrep -f -- "$pat" 2>/dev/null); do
        # 判定中にプロセスが消えることがあるので、リダイレクト自体の
        # エラーもまとめて捨てる
        { tr '\0' ' ' < "/proc/${p}/cmdline"; } 2>/dev/null | grep -q -- "--type=" \
            || kill "$p" 2>/dev/null
    done

    # 終了を待ち、それでも残っていれば強制終了する
    for i in 1 2 3 4 5 6 7 8; do
        pgrep -f -- "$pat" >/dev/null 2>&1 || break
        sleep 0.5
    done
    pkill -9 -f -- "$pat" 2>/dev/null

    if [[ -f "$PIDFILE" ]]; then
        while IFS=: read -r _ pid; do
            [[ -n "${pid:-}" ]] && kill "$pid" 2>/dev/null
        done < "$PIDFILE"
        rm -f "$PIDFILE"
    fi
    log "停止しました"
}

# ============================================================
# ウィンドウの起動と探索
# ============================================================

# URL ごとに独立したプロファイルで Chrome を起動する。
# ・プロファイルを分けないと Chrome が既存プロセスを再利用してしまい、
#   ウィンドウを個別に制御できない
# ・プロファイルは削除せず再利用する（キャッシュが効いて起動が速くなる）
# ・--disable-background-networking と --disable-component-update がないと
#   "Can't update Chrome" のダイアログが別ウィンドウで出て画面に重なる
launch_window() {
    local idx="$1" url="$2"
    local profile="${PROFILE_ROOT}/w${idx}"
    mkdir -p "$profile"

    "$BROWSER" \
        --user-data-dir="$profile" \
        --class="${CLASS_PREFIX}${idx}" \
        --app="$url" \
        --start-fullscreen \
        --window-size="${SCREEN_W},${SCREEN_H}" \
        --force-device-scale-factor="$ZOOM" \
        --disable-background-timer-throttling \
        --disable-backgrounding-occluded-windows \
        --disable-renderer-backgrounding \
        --disable-session-crashed-bubble \
        --disable-infobars \
        --disable-background-networking \
        --disable-component-update \
        --no-first-run \
        --no-default-browser-check \
        --disable-features=TranslateUI \
        >/dev/null 2>&1 &

    # 再起動しても対応が崩れないよう idx:pid の形で記録する
    sed -i "/^${idx}:/d" "$PIDFILE" 2>/dev/null
    echo "${idx}:$!" >> "$PIDFILE"
}

# WM_CLASS からウィンドウ ID を得る。
#
# --app と --class を併用したときの WM_CLASS は
# "<URLから作られたinstance>.<--classの値>" となり、--class の値は 2 番目に入る。
# instance 側にもドットが含まれるので、正規表現ではなく最後の要素を比較する。
# 同じページの別ハッシュを並べても instance が同一になるため、この方法が要る。
find_window() {
    local idx="$1"
    local wid

    wid=$(wmctrl -lx 2>/dev/null | awk -v t="${CLASS_PREFIX}${idx}" \
          '{n = split($3, a, "."); if (a[n] == t) {print $1; exit}}')

    # class が取れない環境向けの保険
    if [[ -z "$wid" ]]; then
        local pid
        pid=$(awk -F: -v i="$idx" '$1 == i {print $2; exit}' "$PIDFILE" 2>/dev/null)
        [[ -n "$pid" ]] && wid=$(wmctrl -lp 2>/dev/null | \
            awk -v p="$pid" '$3 == p && $0 !~ /update Chrome/ {print $1; exit}')
    fi
    echo "$wid"
}

# 全画面状態にする。ドックやトップバーの上にも被せるためにこれが要る
# （通常のウィンドウは _NET_WORKAREA の外に出られない）。
make_fullscreen() {
    wmctrl -i -r "$1" -b add,fullscreen 2>/dev/null
}

# 指定のページを最前面に出す。
#
# windowraise ではなく windowactivate を使う。GNOME/Mutter は全画面ウィンドウに
# ついて「スタック順の最上位」ではなく「フォーカスされているもの」を表示するため、
# raise だけではスタック順が変わっても画面は切り替わらない（実機で確認済み）。
show_page() {
    local idx="$1"
    local wid; wid=$(find_window "$idx")

    # ウィンドウが落ちていたら起動し直す（長時間運用のため）
    if [[ -z "$wid" ]]; then
        log "ウィンドウ ${idx} が見つからないので再起動します"
        launch_window "$idx" "${URLS[$idx]}"
        sleep 8
        wid=$(find_window "$idx")
        [[ -z "$wid" ]] && { log "ウィンドウ ${idx} の再取得に失敗しました"; return 1; }
        make_fullscreen "$wid"
    fi

    xdotool windowactivate "$wid" 2>/dev/null
}

# 終了処理。EXIT からも INT/TERM からも呼ばれるので二重実行を防ぐ。
SHUTTING_DOWN=0
on_exit() {
    (( SHUTTING_DOWN )) && return 0
    SHUTTING_DOWN=1
    log "終了処理中..."
    restore_notifications
    do_stop
}

# Ctrl-C / TERM ではハンドラの後に必ず抜ける。
# exit しないと制御が巡回ループに戻り、次の周回で「ウィンドウが無い」と判定して
# 起動し直してしまうため、一度の Ctrl-C では止まらなくなる。
on_signal() {
    on_exit
    exit 130
}

# ============================================================
# メイン
# ============================================================
main() {
    command -v "$BROWSER" >/dev/null || die "$BROWSER が見つかりません"
    command -v wmctrl     >/dev/null || die "wmctrl が見つかりません（sudo apt install wmctrl xdotool）"
    command -v xdotool    >/dev/null || die "xdotool が見つかりません（sudo apt install wmctrl xdotool）"
    ensure_display || die "X サーバに接続できません。デスクトップ（X11）のターミナルから実行してください"
    detect_screen || die "解像度を検出できません。SIGNAGE_W / SIGNAGE_H で指定してください"

    read_urls
    local n=${#URLS[@]}
    log "解像度 ${SCREEN_W}x${SCREEN_H}（DISPLAY=$DISPLAY）、${n} ページ"

    mkdir -p "$PROFILE_ROOT" "$(dirname "$PIDFILE")"
    : > "$PIDFILE"
    trap on_exit EXIT
    trap on_signal INT TERM
    suppress_notifications

    # --- 全ウィンドウを起動しておく（ここ以降は起動コストが発生しない） ---
    for ((i = 0; i < n; i++)); do
        launch_window "$i" "${URLS[$i]}"
        sleep 1     # 同時起動を避けて負荷を分散する
    done

    log "ウィンドウの出現を待っています..."
    local waited=0 ready=0
    while (( waited < LOAD_WAIT )); do
        ready=0
        for ((i = 0; i < n; i++)); do
            [[ -n "$(find_window "$i")" ]] && ((ready++))
        done
        (( ready == n )) && break
        sleep 1; ((waited++))
    done
    log "${ready}/${n} 件のウィンドウを認識しました"

    for ((i = 0; i < n; i++)); do
        local wid; wid=$(find_window "$i")
        [[ -n "$wid" ]] && make_fullscreen "$wid"
    done

    # ページの読み込みが終わるのを待ってから巡回を始める
    sleep 5

    # --- 巡回ループ ---
    log "巡回を開始します（${DWELL} 秒ごとに切り替え）"
    local tick=0
    while true; do
        local idx=$(( tick % n ))
        show_page "$idx"
        log "表示: ${URLS[$idx]}"
        ((tick++))
        # sleep を待つのに wait を使う。組み込みの wait はシグナルで即座に
        # 中断されるので、Ctrl-C が DWELL 秒待たずに効く。
        sleep "$DWELL" &
        wait $! 2>/dev/null
    done
}

case "${1:-}" in
    --check) do_check ;;
    --stop)  do_stop  ;;
    "")      main     ;;
    *)       die "不明なオプション: $1（--check / --stop）" ;;
esac
