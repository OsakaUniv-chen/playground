#!/usr/bin/env python3
"""前段変換器: 音源の忠実な読み取り(manifest に保存済み)を「音源情報の一行/段落」に変換。

これが mode_A の主役。データ源は gt_label を定義したのと同一の前段
(label_current_sm の 領域絶対強度 + 代表点、gen_samples.py 参照)。gt_label という
"答えの単語"は使わず、座標→人のグラウンディング(右座席の人? 下中央=遠隔者? 誰でもない?)を
VLM に委ねる = honest なテスト。5 通りのテキスト形式:

  coord    最強検出の 座標(x,y) + 絶対強度     提案書の例「座標 X,Y の音が最も強い」
  azimuth  最強検出の 方位角 + 時計方向 + 強度  中心からの角度(真上=0°、時計回り)
  nl       最強検出の 自然言語方位 + 強度       「右側・やや下」等
  grid     最強検出の 3x3 格子セル + 強度       「中央下のセル」
  profile  全方向の検出を 座標 + 絶対強度で列挙  VLM 自身に最強選択させる(領域名は伏せる)

絶対強度(0=静か〜1=最大)を必ず併記するので、静か/曖昧な tick は Others と判断でき、
別途「静音フラグ」を足さずに済む。client は --sample と --format だけ指定すればよい。
"""
import math

IMG = 1080
CX = CY = IMG / 2.0
FORMATS = ("coord", "azimuth", "nl", "grid", "profile")
REGIONS = ("Left", "Right", "Teleoperator", "Others")
_TAG = {"Left": "L", "Right": "R", "Teleoperator": "T", "Others": "O"}

_ROW = {0: "top", 1: "middle", 2: "bottom"}
_COL = {0: "left", 1: "centre", 2: "right"}
_PREFIX = "A signal-processing front-end has localised the current sound. "


def _cell(x, y):
    col = min(2, max(0, int(x * 3 // IMG)))
    row = min(2, max(0, int(y * 3 // IMG)))
    return row, col


def _clock(x, y):
    ang = math.degrees(math.atan2(x - CX, CY - y)) % 360.0   # 0=up, 90=right, 180=down
    hour = round(ang / 30.0) % 12
    return ang, (12 if hour == 0 else hour)


def _nl_phrase(x, y):
    row, col = _cell(x, y)
    if row == 1 and col == 1:
        return "centre"
    if col == 1:
        return "%s-centre region" % _ROW[row]
    if row == 1:
        return "%s side" % _COL[col]
    return "%s-%s area" % (_ROW[row], _COL[col])


def _detections(row):
    """manifest 行 -> [(strength, x, y), ...] を強度降順(領域名は伏せる)。"""
    dets = []
    for reg in REGIONS:
        t = _TAG[reg]
        e, x, y = row.get("e%s" % t), row.get("%sx" % t.lower()), row.get("%sy" % t.lower())
        if e in (None, "") or x in (None, "") or y in (None, ""):
            continue
        dets.append((float(e), int(float(x)), int(float(y))))
    dets.sort(reverse=True)
    return dets


def make_sound_info(row, fmt):
    dets = _detections(row)
    if not dets:
        return _PREFIX + "No sound was localised (the scene is silent)."
    s, x, y = dets[0]                                   # 最強検出(front-end の top detection)
    strength = ("The strongest sound has absolute strength %.2f (0 = silent, 1 = "
                "loudest possible). " % s)

    if fmt == "coord":
        return (_PREFIX + strength + "It is at image pixel coordinate (%d, %d). The "
                "image is %dx%d with (0,0) at the TOP-LEFT, x to the right, y downward."
                % (x, y, IMG, IMG))

    if fmt == "azimuth":
        ang, hour = _clock(x, y)
        return (_PREFIX + strength + "It is at azimuth %.0f degrees clockwise from "
                "straight-up (0=up, 90=right, 180=down, 270=left), i.e. roughly the %d "
                "o'clock direction in the image." % (ang, hour))

    if fmt == "nl":
        return (_PREFIX + strength + "It comes from the %s of the view." % _nl_phrase(x, y))

    if fmt == "grid":
        r, c = _cell(x, y)
        return (_PREFIX + strength + "Dividing the image into a 3x3 grid, it is in the "
                "%s-%s cell." % (_ROW[r], _COL[c]))

    if fmt == "profile":
        listing = "; ".join("at (%d, %d) strength %.2f" % (px, py, ps)
                            for ps, px, py in dets if ps > 0.02)
        return ("A signal-processing front-end reports the localised sound energy at "
                "these image positions (image %dx%d, (0,0) top-left; strength 0 = "
                "silent, 1 = loudest): %s. Decide which one, if any, is the actual "
                "speaker." % (IMG, IMG, listing))

    raise ValueError("unknown format: %s (choose from %s)" % (fmt, ", ".join(FORMATS)))
