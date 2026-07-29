#!/usr/bin/env python3
"""mode_B の「音源情報」段落 = 黄色オーバーレイの読み方の説明。

mode_A の textifier 出力に対応する mode_B 側の一段落。共通足場(common/prompt.py の
SCENE + ANSWER)に挿し込むと、mode_A と**同じ足場・違うのは音源情報だけ**になり、
テキスト化 vs 画像重畳を公平に比較できる。

注意: サンプルは sm_to_color = **黄色**(明るいほど大)。旧 eval_labeled.py の prompt は
jet(赤=最大)と書いていて実画像と不整合だったので、ここは黄色として正しく説明する。
"""

SOUND_INFO = (
    "A translucent YELLOW sound-energy heatmap has been overlaid on top of this "
    "scene. Brighter / more saturated yellow means higher sound energy at that "
    "location, while dark, un-highlighted areas are quiet. The single brightest "
    "yellow region marks where the sound is coming from right now. Read the position "
    "of that bright-yellow region relative to the left/right players and the "
    "lower-centre teleoperator region."
)
