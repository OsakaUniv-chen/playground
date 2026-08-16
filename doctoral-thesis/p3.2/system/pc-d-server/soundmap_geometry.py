#!/usr/bin/env python3
"""音響マップの画素 <-> 方向角。`decide()` が「どこが鳴っているか」を
頭部の指令角に直すのに使う。

**この対応付けは推測ではなく、生成器の写像から導いて検算してある。**
`pc-b-robot/soundmap/onebit_soundmap.py` の `_create_uv` /
`_prepare_interpolator` を逆に辿り、893 個の格子点で突き合わせた結果、
**中央値 0.13 度・最大 0.31 度**で一致する（残差は生成器が画素座標を
`int()` で丸めているぶん）。検算はこのファイルを直接実行すると再現できる。

## 投影

音響マップは**方位等距離投影**。画像の中心が正面で、中心からの距離が
そのまま「正面からの角度」に比例する:

    中心 (31.5, 31.5) = 正面 0 度
    中心から 32 画素  = 正面から 90 度

軸の向き（検算で確認した実際の向き。直感と縦が逆なので注意）:

    col が大きい = 右
    row が**小さい** = 上

## この角度は「頭からの相対」であって絶対ではない

**16ch アレイはカメラと一緒に頭部に載っている**（todo-list の
「カメラとアレイを載せると慣性が変わる」）。つまりマップの中心は
**そのときの頭の向き**であって、機体の正面ではない。したがって:

    次の指令 = いまの頭の角度 + このモジュールが返す角度

`いまの頭の角度` を PC-D は読めない ── **PC-B はモータの読み戻しをしない**
（BLE の帯域を食うため。設計 §5.1）。自分が出した指令を覚えておくしかない。

そこから来る制約: **判断の周期は、頭が落ち着くより遅くすること。**
PC-B 側は EMA（既定 alpha=0.25 @10 Hz、時定数 0.35 s）で目標へ寄せるので、
可動域いっぱいでも約 1.5 秒で収まる。落ち着く前に次の相対角を足すと、
まだ動いている途中の姿勢を「いまの角度」とみなすことになり、**ずれが
足し算で溜まっていく。**
"""

import math

SM_SIZE = 64
_PIX = 1080                 # 生成器が内部で使う画素系
_HALF_FOV_DEG = 90.0        # 中心から端までが正面から 90 度

# 中心。`(i + 0.5) * PIX / SM - 0.5` を 1080/2 について解いた値で、
# ちょうど 31.5 ではない（生成器の格子の取り方がそうなっている）。
CENTER = (_PIX / 2 + 0.5) * SM_SIZE / _PIX - 0.5
# 中心から端（1080/2 画素）までのインデックス数
_HALF_IDX = (_PIX / 2) * SM_SIZE / _PIX


def index_to_angles(row, col):
    """音響マップの (row, col) -> (yaw_deg, pitch_deg)。

    **頭部から見た相対角**。右が +yaw、上が +pitch（PC-B の
    `head/command` と同じ向き）。row / col は小数でよい。
    """
    dr = row - CENTER
    dc = col - CENTER
    # 画像平面での半径 -> 正面からの角度（方位等距離投影）
    rad_idx = math.hypot(dc, dr)
    polar = math.radians(rad_idx / _HALF_IDX * _HALF_FOV_DEG)
    # 画像平面での向き。col が右、row の**負**が上
    theta = math.atan2(-dr, dc)

    # 単位ベクトルに直してから角度にする。tan を経由すると端（90 度）で
    # 発散するので、こちらを使う。
    vx = math.sin(polar) * math.cos(theta)      # 右
    vy = math.sin(polar) * math.sin(theta)      # 上
    vz = math.cos(polar)                        # 前
    yaw = math.degrees(math.atan2(vx, vz))
    pitch = math.degrees(math.atan2(vy, math.hypot(vx, vz)))
    return yaw, pitch


def peak(sm, min_value=0.0):
    """いちばん強い所を (row, col) で返す。無ければ None。

    最大値の画素だけを見ると 1 画素ぶん（約 2.8 度）刻みになるので、
    その周り 3x3 の重心を取って小数で返す。**マップは 64x64 しか
    情報が無い**ので、これ以上の分解能は無い。
    """
    n = len(sm)
    best = None
    for r in range(n):
        for c in range(n):
            v = sm[r][c]
            if best is None or v > best[0]:
                best = (v, r, c)
    if best is None or best[0] <= min_value:
        return None
    _, r0, c0 = best

    num_r = num_c = den = 0.0
    for r in range(max(0, r0 - 1), min(n, r0 + 2)):
        for c in range(max(0, c0 - 1), min(n, c0 + 2)):
            w = sm[r][c]
            if w <= 0:
                continue
            num_r += w * r
            num_c += w * c
            den += w
    if den <= 0:
        return float(r0), float(c0)
    return num_r / den, num_c / den


def _selftest():
    """生成器の写像と突き合わせる。numpy と scipy が要る（PC-B 側の依存）。"""
    import os
    import sys

    import numpy as np

    here = os.path.dirname(os.path.abspath(__file__))
    gen_dir = os.path.join(here, "..", "pc-b-robot", "soundmap")
    if not os.path.isdir(gen_dir):
        print(f"生成器が見つからない（{gen_dir}）。PC-B 側のフォルダが要る")
        return 1
    sys.path.insert(0, gen_dir)
    from onebit_soundmap import OneBitSoundMapGenerator

    g = OneBitSoundMapGenerator()
    v, u = g._create_uv()                 # 生成器はこの順で受けている
    v = _PIX - v
    u = 2 * 540 - u
    v = 2 * 540 - v
    to_idx = lambda p: (p + 0.5) * SM_SIZE / _PIX - 0.5   # noqa: E731
    row, col = to_idx(u), to_idx(v)

    x, y = g.gpos[0], g.gpos[1]
    # 真値の角度（格子は z=distance の平面上にある）
    truth_yaw = np.degrees(np.arctan2(x, g.distance))
    truth_pitch = np.degrees(np.arctan2(y, np.hypot(x, g.distance)))

    err = []
    inside = np.hypot(x, y) < 4.0
    for k in np.nonzero(inside)[0]:
        yaw, pitch = index_to_angles(row[k], col[k])
        err.append(math.hypot(yaw - truth_yaw[k], pitch - truth_pitch[k]))
    err = np.array(err)
    print(f"格子 {inside.sum()} 点で突き合わせ")
    print(f"  角度の誤差: 中央値 {np.median(err):.3f} 度 / 最大 {err.max():.3f} 度")

    print("  代表点:")
    for tx, ty in [(0, 0), (1.0, 0), (0, 1.0), (-1.0, 0), (1.0, 1.0)]:
        k = int(np.argmin(np.hypot(x - tx, y - ty)))
        yaw, pitch = index_to_angles(row[k], col[k])
        print(f"    ({x[k]:+.2f},{y[k]:+.2f}) -> (row {row[k]:5.1f}, col {col[k]:5.1f})"
              f" -> yaw {yaw:+6.1f}° pitch {pitch:+6.1f}°"
              f"   （真値 {truth_yaw[k]:+6.1f}° {truth_pitch[k]:+6.1f}°）")

    ok = err.max() < 1.0
    print("  一致した" if ok else "  **ずれている。写像を見直すこと**")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(_selftest())
