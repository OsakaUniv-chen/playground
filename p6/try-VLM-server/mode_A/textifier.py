#!/usr/bin/env python3
"""前段変換器(mode_A): 音図の最強音源の位置を、抽象度の異なる 3 通りのテキストにする。

方針は「**数値そのまま → だんだん人間語へ**」という抽象度のグラデーション:

  coord : 最強音源のピーク座標 (x, y)                     …… 数値そのまま
  grid  : 画像を 3x3 に区切ったときの最強セル (row, col)   …… 粗い離散化
  nl    : その 3x3 セルを自然言語の方位に言い換え          …… 人間語

いずれも「最強の音源はどこか」という同一情報を、表現の抽象度だけ変えて与える。位置は
gt を定義したのと同一前段(領域絶対強度＋代表点、gen_samples.py)の **最強領域の代表点**
から取り、gt ラベルの語そのものは使わない。座標系は VLM 実効入力の **756x756**(§1.2)。
"""
FORMATS = ("coord", "grid", "nl")

SRC = 1080.0            # manifest の代表点は 1080 座標
IMG = 756               # VLM 実効入力解像度
GRID = 3                # 3x3 グリッド
CELL = IMG / GRID       # 252
REGIONS = ("Left", "Right", "Teleoperator", "Others")
_TAG = {"Left": "L", "Right": "R", "Teleoperator": "T", "Others": "O"}
_PREFIX = "A sound-localization front-end has located the current strongest sound. "

_ROW = {0: "top", 1: "middle", 2: "bottom"}
_COL = {0: "left", 1: "centre", 2: "right"}


def _nl_phrase(r, c):
    if r == 1 and c == 1:
        return "centre"
    if c == 1:
        return "%s-centre" % _ROW[r]      # top-centre / bottom-centre
    if r == 1:
        return "%s side" % _COL[c]         # left side / right side
    return "%s-%s" % (_ROW[r], _COL[c])    # top-left, bottom-right, ...


def _dominant_xy(row):
    """manifest 行 -> 最強領域の代表点を 756 座標で返す (x, y)。"""
    best, bx, by = -1.0, IMG // 2, IMG // 2
    for reg in REGIONS:
        t = _TAG[reg]
        e, x, y = row.get("e%s" % t), row.get("%sx" % t.lower()), row.get("%sy" % t.lower())
        if e in (None, "") or x in (None, "") or y in (None, ""):
            continue
        if float(e) > best:
            best = float(e)
            bx = int(round(int(float(x)) * IMG / SRC))
            by = int(round(int(float(y)) * IMG / SRC))
    return bx, by


def _cell(x, y):
    c = min(GRID - 1, max(0, int(x // CELL)))
    r = min(GRID - 1, max(0, int(y // CELL)))
    return r, c


# 音源判定は音図信号を最優先、画像は位置対応づけの補助（全形式共通・末尾に付与）
_SUFFIX = (" Decide PRIMARILY from this sound information; use the image only to map the "
           "indicated location to one of the four labels — do NOT pick whoever merely "
           "looks like they are speaking (mouth, gestures).")


def make_sound_info(row, fmt):
    x, y = _dominant_xy(row)

    if fmt == "coord":
        msg = _PREFIX + "Its peak is at image pixel coordinate (%d, %d)." % (x, y)
    elif fmt == "grid":
        r, c = _cell(x, y)
        msg = (_PREFIX + "The image is divided into a 3x3 grid (rows 1-3 top to bottom, "
               "columns 1-3 left to right). The strongest sound is in the cell at "
               "row %d, column %d." % (r + 1, c + 1))
    elif fmt == "nl":
        r, c = _cell(x, y)
        msg = _PREFIX + "It comes from the %s of the view." % _nl_phrase(r, c)
    else:
        raise ValueError("unknown format: %s (choose from %s)" % (fmt, ", ".join(FORMATS)))
    return msg + _SUFFIX
