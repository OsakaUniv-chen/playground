#!/usr/bin/env python3
"""4クラス採点(mode_A / mode_B 共通)。

判定は厳密 4 クラス(Left / Right / Teleoperator / Others)。指導教員要望により
全体正解率に加え **クラス別 precision**(と recall)、混同行列を出す。
手挑 9 枚は小さいので、数字はクラスあたり件数と併記して読むこと。
"""
from .prompt import LABELS


def new_tally():
    return {
        "cm": {g: {p: 0 for p in LABELS} for g in LABELS},  # cm[gt][pred]
        "pred_dist": {p: 0 for p in LABELS},
        "n_correct": 0, "n_total": 0, "n_unparsed": 0,
    }


def add(tally, gt, pred):
    tally["n_total"] += 1
    if pred is None:
        tally["n_unparsed"] += 1
        return
    tally["pred_dist"][pred] += 1
    if gt in tally["cm"]:
        tally["cm"][gt][pred] += 1
    if pred == gt:
        tally["n_correct"] += 1


def summary(tally):
    """テキストの成績表を返す(正解率 + クラス別 precision/recall + 混同行列)。"""
    cm, n_total = tally["cm"], tally["n_total"]
    acc = tally["n_correct"] / n_total if n_total else 0.0
    lines = []
    lines.append("4-class accuracy: %d/%d = %.1f%%   (unparsed %d, random baseline 25%%)"
                 % (tally["n_correct"], n_total, acc * 100, tally["n_unparsed"]))

    # クラス別 precision / recall
    lines.append("")
    lines.append("per-class:   precision            recall")
    for c in LABELS:
        tp = cm[c][c]
        pred_c = sum(cm[g][c] for g in LABELS)          # このクラスと予測した総数
        gt_c = sum(cm[c][p] for p in LABELS)            # 実際このクラスの総数
        prec = tp / pred_c if pred_c else float("nan")
        rec = tp / gt_c if gt_c else float("nan")
        lines.append("  %-12s %2d/%-2d = %5s     %2d/%-2d = %5s"
                     % (c, tp, pred_c, _pct(prec), tp, gt_c, _pct(rec)))

    # 混同行列
    lines.append("")
    lines.append("confusion (row=gt, col=pred):")
    lines.append("  %-12s %s" % ("", " ".join("%-6s" % p[:6] for p in LABELS)))
    for g in LABELS:
        lines.append("  %-12s %s" % (g, " ".join("%-6d" % cm[g][p] for p in LABELS)))
    return "\n".join(lines)


def _pct(x):
    return "  -  " if x != x else "%4.0f%%" % (x * 100)  # x!=x は NaN
