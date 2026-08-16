#!/usr/bin/env python3
"""baseline / mode_A / mode_B 共通のプロンプト足場と答案パース。

3 条件で **場面説明・4 クラス定義・出力形式は完全に共有**し、唯一違うのは「音源情報を
どう与えるか」の一節だけ:
  - baseline: 音源情報なし(視覚のみで判定)
  - mode_A  : 前段が音図を **テキスト化** した一節(textifier.py が生成)
  - mode_B  : 音図を **黄色オーバーレイ** した画像 + その読み方(overlay_info.py)

出力は **4 ラベルのみ**(Left / Right / Teleoperator / Others)。思考過程は出させない。
座標系は VLM の実効入力解像度 **756x756**(§1.2)。遠隔操作者は非可視なので「与えられた
座標枠」で定義する。
"""
import re

LABELS = ("Left", "Right", "Teleoperator", "Others")

# 遠隔操作者の固定領域(1080 の speaking-box 377,645,330,330 を 756 に換算)
TELE_BOX_756 = (264, 452, 495, 683)   # x1,y1,x2,y2

# --- 場面説明(全条件共通・自動付与) ----------------------------------------
SCENE = (
    "This is a fisheye camera view (756x756 pixels; (0,0) is the TOP-LEFT, x increases "
    "right, y increases down) of a Word Wolf game room with two local players and one "
    "remote teleoperator. Several of these may make sound at the same time; identify the "
    "SINGLE STRONGEST (loudest) sound source, which is one of these four:\n"
    "- Left        : the LEFT local player is speaking (seated on the left side of the view).\n"
    "- Right       : the RIGHT local player is speaking (seated on the right side of the view).\n"
    "- Teleoperator: the REMOTE operator is speaking. They are NOT a visible person; their "
    "voice always comes from a fixed given box, x=[264,495], y=[452,683] "
    "(the lower-centre region, over the robot/table).\n"
    "- Others      : none of the above (the sound comes from elsewhere, or there is no "
    "clear single source)."
)

# --- 出力形式(全条件共通・自動付与) タスク明示 + ラベルのみ、思考過程なし ----
ANSWER = (
    "Look at the image and use the information above to determine which ONE of the four "
    "is the STRONGEST sound source right now (if several sound at once, pick the loudest). "
    "Then answer with EXACTLY ONE word — Left, "
    "Right, Teleoperator, or Others — and output only that single word, with no "
    "explanation, no reasoning, and no punctuation."
)

# --- baseline(音源情報なし)の一節 ------------------------------------------
BASELINE_INFO = (
    "No sound-localization information is given. Judge only from the visual scene "
    "(who appears to be the current speaker)."
)


def build(sound_info):
    """SCENE + <条件固有の音源情報> + ANSWER を組み立てる。"""
    return "%s\n\n%s\n\n%s" % (SCENE, sound_info.strip(), ANSWER)


def parse_label(text):
    """出力から 4 ラベルのどれかを取り出す。ラベルのみ出力を想定するが、万一 説明が
    付いても最後に現れたラベル語を拾う(後方互換)。"""
    m = re.findall(r"ANSWER:\s*([A-Za-z]+)", text)
    fallback = [w for w in re.findall(r"\b(Left|Right|Teleoperator|Others)\b", text)]
    cands = ([m[-1]] if m else []) + fallback[::-1]
    for c in cands:
        cl = c.lower()
        for lab in LABELS:
            if cl == lab.lower() or (cl in ("tele", "teleop", "operator") and lab == "Teleoperator"):
                return lab
    return None
