# ROSbag トピックリファレンス（解析用）

対象データ: `/Volumes/Extreme SSD/WordWolfExp/ROSbag/`（rosbag2, sqlite3）。
bag 命名: `G{組}_game{n}_{条件}`、ロボット4条件（Tele/PSSP/DoA/Random）は game3–6。
時間軸: **bag の record timestamp（robot PC の受信時刻）で統一**。

レートは全52 bag（ロボット4条件）の実測値（総メッセージ数 ÷ 総収録時間）。

## 解析に使うトピック

| トピック | 実測レート | 内容・使い方 |
|---|---|---|
| `/audio/audio_raw` | 344.5 Hz | 16ch int16 @44.1kHz、**128サンプル/msg（≈2.9ms）**。サウンドマップ再生成の入力 |
| `/camera/image_raw/compressed` | 29.5 Hz | room1 魚眼 1080×1080（ゲーム場、ロボット視点）。head box の**再検出**入力・PSSP clip の gray 入力 |
| `/room2_camera/image_raw/compressed` | 29.9 Hz | テレオペレータ映像。TEL 頭部方位を MediaPipe で**再導出**（全 bag に存在 → 全条件で TEL 信号を作れる） |
| `/room2_audio/vad` | 49.9 Hz | テレオペマイクの VAD（bool）。speaking-box マスクのゲート |
| `/boxie/boxie_motors` | 2.6 Hz | 指令角 `[pitch, yaw, roll]`（deg, int）。**変化時のみ発行 → 前値保持（ffill）で参照**。実行レベルの注視方向（P(switch) 等）。Random 局では「実際に実行された乱数注視」でもある（決定レベルの RAND ベースラインは再現可能な乱数ウォーク `rand_side` を別途生成） |


## 各方策が同一 tick 時刻でどの観測を使うか（出力時刻軸）

**基準は「出力時刻軸」**（PPT 参照）: すべての方策が同じ tick 時刻 t（4Hz）で決定を出すが、
使う観測は各自の処理遅延の分だけ過去になる。これでモード間比較が「同一の壁時計時刻での決定」として
公平になる。mode_doa / mode_pssp は単一スレッドで、tick 内に「音声窓 → **SM 生成 ~0.2s** → ラベル → publish」
を同期実行するため、DoA の出力は音声窓が 0.2s 手前で締まる。

| 方策 | tick t で使う観測（例 t=1.5s） | 遅延 |
|---|---|---|
| GT（真値） | 音声窓終端 = t（1.04~1.5s） | 0（正しさの基準） |
| DoA | 音声窓終端 = **t−0.2s**（0.84~1.3s）＝ t−0.2 までの最新 160 msg | 0.2s（SM 生成） |
| PSSP | DoA と同じ最新窓（→t−0.2）を最新フレームに、2Hz×10 の clip から SimVP が **+0.5/1.0/1.5/2.0s** を予測 | 0.2s＋推論 |
| Tele | room2 の最新フレーム ≤ t（顔検出、~30Hz） | ~0.033s（フレーム量子化） |
| Random | 観測非依存（切替確率 0.065 / tick） | 0 |

### 5 モードの数式定義（教員確認用）

記号: 出力時刻 $t$（4Hz グリッド）、音声窓長 $L=0.46$s、SM 生成遅延 $\tau=0.2$s、Tele フレーム遅延 $\tau_T\approx0.033$s。

**構成要素**
- $\mathrm{SM}(s)=\mathrm{BeamForm}\big(\text{audio}\in[s-L,\,s]\big)$ : 窓終端 $s$ の 64×64 サウンドマップ（acoular 'old'、最新 160 msg）。
- $\mathrm{HB}(s)$ = ts $\le s$ の最新 head box（MediaPipe 再検出＋前値保持）、$\mathrm{vad}(s)$ = ts $\le s$ の最新 vad を基準にした 0.25s 窓判定。
- $\mathrm{lab}(s)=\mathrm{Label}_7\big(\mathrm{mask}_{\mathrm{vad}(s)}(\mathrm{SM}(s)),\,\mathrm{HB}(s)\big)\in\{L,R,T,O\}$ : VAD マスク後に method7 で 4 ラベル抽出。

**5 モード（tick $t$ での決定）**

$$\mathrm{GT}(t)=\mathrm{lab}(t)\qquad(\text{窓終端}=t,\ \text{遅延}\,0,\ \text{真値})$$

$$\mathrm{DoA}(t)=\mathrm{lab}(t-\tau)=\mathrm{lab}(t-0.2)\qquad(\text{窓終端}=t-0.2)$$

$$\mathrm{PSSP}_h(t)=\mathrm{Label}_7\big(\widehat{\mathrm{SM}}_h(t),\,\mathrm{HB}(t)\big),\quad h\in\{0.5,1.0,1.5,2.0\}$$
$$\big(\widehat{\mathrm{SM}}_h(t)\big)_h=\mathrm{SimVP}\big(C(t)\big),\quad C(t)=\big(\phi(t-\tau-0.5j)\big)_{j=9,\dots,0}\ (\text{10 フレーム @2Hz})$$

ここで $\phi(s)=[\,\mathrm{fuse}(\mathrm{mask}_{\mathrm{vad}(s)}(\mathrm{SM}(s)),\,g(s)),\ g(s)\,]$、$g(s)$ = ts $\le s$ の room1 映像のグレースケール。主使用は $h=1.0$。

$$\mathrm{Tele}(t)=\begin{cases}L & \mathrm{yaw}(t)>0\\ R & \mathrm{yaw}(t)\le0\end{cases}\in\{L,R\}$$

$\mathrm{yaw}(t)$ = ts $\le t$ の最新 room2 顔フレーム（鮮度 $\le0.5$s、MediaPipe FaceMesh + solvePnP）の頭部 yaw。

$$\mathrm{Random}(t_k)=\begin{cases}\mathrm{flip}(\mathrm{Random}(t_{k-1})) & \text{確率 }p=0.065\\ \mathrm{Random}(t_{k-1}) & \text{確率 }1-p\end{cases}\in\{L,R\}$$

観測非依存。実機の乱数列（無 seed）は復元不可のため、解析では bag 名から導出した seed の**再現可能な
乱数ウォーク**（`rand_side`）を生成して RAND(t) とする。実際に実行された乱数注視が必要な場合のみ
録画実行系 $\mathrm{side}(\mathrm{yaw}_{\mathrm{motor}}(t))$ を使う。

**PSSP 予測地平線の注意**: $\mathrm{PSSP}_{1.0}(t)$ の入力窓 $[t-0.66,\,t-0.2]$ の中心は $t-0.43$、その +1.0s 先 ＝ 予測対象の内容中心 $\approx t+0.57$s（例 $t{=}1.5\!\to\!2.07$s）。当面は標準の $\mathrm{GT}(t+1.0)$（$t{=}1.5\!\to\!2.5$s）と照合し、~0.4s のずれは限界として記録（SPEC §6.5）。

※ 解析は各方策を**それぞれの遅延窓で直接計算する**（グリッドずらしや補間はしない）。GT と DoA は窓終端が 0.2s 違うので別計算・別列。

下流遅延（実行系のみ）: policy 集約（≤0.25s）→ basic_mode の **1.0s ホールド**＋EMA（α=0.25, yaw 5° 閾値）→
モータ実行。指令角（boxie_motors）は決定より ~1s 以上遅れる → κ を「決定レベル（再計算ラベル）」と
「実行レベル（boxie_motors）」で分けて見る根拠。
