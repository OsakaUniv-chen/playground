#!/usr/bin/env python3
"""mode_A / mode_B 共通のプロンプト足場と答案パース。

A/B を公平に比べるため、**場面説明・4クラス定義・回答フォーマットは完全に共有**し、
唯一違うのは「音源情報をどう与えるか」の一行だけ:
  - mode_A: 前段変換器が音図を **テキスト化** した一行(textifier.py が生成)
  - mode_B: 音図を **黄色オーバーレイ** した画像 + その読み方を説明する一行

こうすると「テキスト vs 画像重畳」というモダリティ差だけを切り出して比較できる。

LABELS / parse_label は旧 p6/try-VLM/trial-2 の probe と互換(既発表の 91.7% と可比)。
"""
import re

LABELS = ("Left", "Right", "Teleoperator", "Others")

# --- 場面説明(全モード共通・自動付与) --------------------------------------
SCENE = (
    "This is a fisheye camera view of a Word Wolf game room. At this instant "
    "the sound can come from one of four sources:\n"
    "- Left        : the LEFT local player is speaking (seated on the left side of the view).\n"
    "- Right       : the RIGHT local player is speaking (seated on the right side of the view).\n"
    "- Teleoperator: a REMOTE operator is speaking. The teleoperator is NOT a visible "
    "person; their voice is emitted from the LOWER-CENTRE region of the image "
    "(over the robot / table in the middle-bottom).\n"
    "- Others      : none of the above (the sound comes from elsewhere, or there is "
    "no clear single source)."
)

# --- 回答フォーマット(全モード共通・自動付与) ------------------------------
ANSWER = (
    "Using the scene together with the sound information above, decide which one of "
    "the four is the sound source right now. Give one short reason, then on the FINAL "
    "line write only the answer word, one of: Left, Right, Teleoperator, Others.\n"
    "Final line format -> ANSWER: word"
)


def build(sound_info):
    """SCENE + <モード固有の音源情報一行/段落> + ANSWER を組み立てる。"""
    return "%s\n\n%s\n\n%s" % (SCENE, sound_info.strip(), ANSWER)


def parse_label(text):
    """出力文から Left/Right/Teleoperator/Others を取り出す(trial-2 02_probe.py と同一)。"""
    m = re.findall(r"ANSWER:\s*([A-Za-z]+)", text)
    fallback = [w for w in re.findall(r"\b(Left|Right|Teleoperator|Others)\b", text)]
    cands = ([m[-1]] if m else []) + fallback[::-1]
    for c in cands:
        cl = c.lower()
        for lab in LABELS:
            if cl == lab.lower() or (cl in ("tele", "teleop", "operator") and lab == "Teleoperator"):
                return lab
    return None
