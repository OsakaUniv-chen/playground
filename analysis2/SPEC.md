# analysis2 — 行動解析やり直し 仕様書

**状態**: 仕様のみ（実装はまだ）。実装は別セッション/別モデルが行う前提で、必要な情報は本書・
[docs/ros-topics.md](docs/ros-topics.md)・下記のコピー元パスに揃えてある。

## 0. 背景

指導教員が report-0701 の行動解析に「データを取り違えていないか（タイムスタンプ整合）」の疑義。
調査で analysis1 の欠陥を 2 つ確認済み（`analysis1_until_0702/doa_kappa_debug_0705.md`）:

1. **サウンドマップ生成器の不一致**: オフラインは `generator='new'`（PyTorch 再実装）で GT を再計算、
   ライブは acoular 版 `'old'`。決定レベルの一致は ~71–75% しかない → DoA κ が低く見えた主因。
2. **SimVP 重みの不一致**: オフラインは `config_simvp_exp4_new_epoch31.pt` をロード、
   ライブ（と report の記述）は `config_simvp_exp4.pt` → **report §3.6 の数値は疑わしい**。

加えてタイムスタンプの意味論（SM は別ノードで生成・決定は ~0.46 s 過去の音声を反映・room2 系は別 PC
の時計）が明文化されていなかった。analysis2 は「ライブと同一のコンポーネント・同一の定数・明文化した
時刻規範」で全部やり直す。

## 1. 決定事項（ユーザ確認済み 2026-07-05）

- SM は全 4 条件とも **vendored acoular（'old'）で全再計算**。録画 `/sm_without_transform` は WP2 の検証ゲート専用。
- SimVP は **exp4 のみ**（epoch31 の結果は作廃）。
- **head box も bag を使わず MediaPipe で再検出**（`head_node.py` の検出コードを忠実移植）。録画 `/head/head_box` は照合のみ。
- TEL（テレオペ頭部方位）も全 bag で room2 映像から MediaPipe 再導出（`mode_tele.py` と同一コード系）。
- 廃止（tick 単位一致率で代替）: 旧 §3.4 JS 類似度、§3.7(a)(b)。§3.1 / §3.2 κ / §3.3 P(switch) / §3.6 は修正パイプラインで再実施。
- 問卷復查（GEQ 下位項目）も本 spec の WP-S として含む。
- **この Mac では小規模テストのみ**（WP0–WP2、WP4 の集計）。**全量抽出（WP3）は高スペック PC**（実行コマンドは §9 に用意）。

## 2. ルール

- コードは全て `analysis2/code/` に自己完結。SSD からの import 禁止（必要ソースは WP0 でコピー）。SSD はデータ読み取りのみ。
- 再利用ロジックは API（入力→出力、ROS/GUI なし）。スクリプトは薄い CLI。
- 時間軸は **bag record timestamp**（根拠は ros-topics.md）。
- 乱数は固定 seed。tie-break の発生回数を記録。
- 全 bag 使用（head box を MediaPipe 再検出するため、analysis1 の除外 G6_game3_Tele・G12_game4_PSSP は不要）。

## 3. ディレクトリ

```
analysis2/
  SPEC.md / docs/ros-topics.md / docs/validation-report.md(WP1+WP2の出力)
  code/
    soundmap/            vendored acoular + old generator + mic xml
    pssp/                simvp.py + config_simvp_exp4.pt + loader
    soundmap_api.py  pssp_api.py  labeling.py  head_box.py  head_orientation.py
    bag_io.py  extract.py  validate.py  bag2video.py  analyses/
  results/ticks/  results/metrics/
```

## 4. WP0 — コード抽出

コピー元はすべて `/Volumes/Extreme SSD/WordWolfExp/code/robot pc/ros2_ws/src/boxie_node/boxie_node/`。
改変は import 修正と ROS・表示コードの削除のみ（ロジックは変えない）。

| コピー先 | コピー元 |
|---|---|
| `code/soundmap/acoular/`（xml 含む全体） | `soundmap/acoular/` |
| `code/soundmap/sound_map.py` | `soundmap/sound_map.py`（ライブで動いた 'old' 生成器） |
| `code/pssp/simvp.py`, `utils_all_load.py` | `train_orig_sm_target_sm_gray/` 同名（`.cuda()` 固定を device 引数化） |
| `code/pssp/config_simvp_exp4.pt` | `train_orig_sm_target_sm_gray/results/config_simvp_exp4.pt` |
| `code/labeling.py` | `check_doa.py` の `run_extract_target`(method7)系 + `HeadBoxProcessor`、`policy_utils.py` の `mask_speaking_box_in_sound_map` / VAD utils / transform('B')・`visualize_sm` |
| `code/head_box.py` | `head_node.py` の `HeadDetector`（MediaPipe FaceDetection, model_selection=1, min_detection_confidence=0.5, history_max_count=6, `is_reasonable_update` 含めて忠実移植） |
| `code/head_orientation.py` | `policy_utils.py` の `HeadOrientationDetector`（FaceMesh + solvePnP。**内部の flip と符号規約をそのまま**） |
| `code/bag_io.py` | `analysis1_until_0702/analysis_script/` の `_targeting_env.py`（CDR デコーダ）+ `p_switch_analysis.py`（`decode_boxie_yaw` 等）+ `head_box_check.py`（画像・Int32MultiArray デコーダ）から流用。`Vector3Stamped`（/tele/head_orientation）を追加。使う topic は §5 のみ（TargetPos/String デコーダは不要） |

環境: `numpy scipy scikit-learn traits numba packaging opencv-python torch mediapipe pyarrow`
（vendored acoular が要求すれば `tables` 追加）。スモークテスト: vendored acoular の import →
合成音で SM 1 枚生成 / exp4 を cpu ロード / MediaPipe を 1 フレームに適用。

### API シグネチャ

```python
# soundmap_api.py — 'old' 生成器のラッパ
class SoundMapAPI:
    def __init__(self, xml_path=..., fs=44100, channels=16, blocksize=4096, sm_size=64): ...
    def generate(self, audio_chunks: list[bytes]) -> np.ndarray   # (64,64) float [0,160]

# labeling.py
def transform_sm(sm) -> np.ndarray                    # exp(x - x.max())
def sm_to_color(sm_t, plot_size=1080) -> np.ndarray   # (1080,1080,3) uint8
def mask_speaking_box(sm) -> np.ndarray
def extract_label(sm_color, head_boxes, method=7, rng=None) -> (label, metrics, points)
class HeadBoxProcessor: ...                            # 前回有効 box の保持
def vad_active_at(vad_ts, vad_val, t, window=0.25) -> bool

# pssp_api.py
class PsspAPI:
    def __init__(self, weights_path=EXP4, device="auto"): ...   # auto: cuda→mps→cpu
    @staticmethod
    def make_clip_frame(sm_masked, gray64) -> np.ndarray   # (2,64,64) [fused, gray]
    def predict(self, clip10) -> np.ndarray                # (10,2,64,64)→(4,64,64)、index1 が +1.0s

# head_box.py — 状態あり。フレームを時系列順に流す
class HeadBoxAPI:
    def detect(self, bgr_frame) -> list    # [[x,y,w,h],[x,y,w,h]] 1080²座標、-99=無効

# head_orientation.py — 状態あり（FaceMesh トラッキング）。1 ストリームに 1 インスタンス
class HeadOrientationAPI:
    def detect(self, bgr_image) -> (pitch, yaw, roll) | None   # deg, int
    @staticmethod
    def yaw_to_side(yaw) -> str            # yaw > 0 → 'left'
```

## 5. 再現定数（ライブと同値にすること）

| 項目 | 値 |
|---|---|
| 決定 tick | 0.25 s（4 Hz） |
| 音声窓 | 160 msg = 0.464 s |
| SM | acoular BeamformerBase, r_diag=True, synthetic(f=2000, num=3), blocksize 4096, Blackman-Harris, overlap 66.1%, +30 dB, 3 段 merged grid, z=1.5 m, c=345, 出力 64×64 clip[0,160] |
| VAD ゲート | 窓 0.25 s、基準=最新 vad msg。非アクティブ時に speaking box をゼロ化 |
| ラベル抽出 | method 7: L/R/Tele は P87.5、Others は P98。speaking box = (377, 645, 330, 330)。全ゼロ tie は乱数 |
| 4→2 ラベル | Left/Right はそのまま発行、Teleoperator/Others は内部ランダムウォーク（切替確率 0.065/tick）の出力を発行 |
| PSSP clip | deque 19（tick 毎に追加）→ `[::2]` → 10 フレーム @2Hz（0.5s 間隔。音声内容は直近 ≈5s: 最旧窓開始 t−5.16 〜 最新窓終端 t−0.2） |
| SimVP | (10,2,64,64), pred_len=4, gsta, 重み exp4。**target_frame=1 = +1.0 s** |
| 下流 | policy 鮮度ガード 1.0 s / basic_mode: 20 Hz, **ホールド 1.0 s**, EMA α=0.25, yaw 5° 閾値 / boxie: clamp pitch±30° yaw±60° |

## 6. タイムスタンプ規範

**基準は「出力時刻軸」**（PPT と同じ）。各モードは同じ tick 時刻 t で決定を出すが、使う観測は
各自の遅延分だけ過去になる。これでモード間比較が「同一の壁時計時刻での決定」として公平になる。

1. **tick グリッド**: bag 毎に `t_k = t0 + k·0.25s`。**先頭 10 秒はバッファ充填に使い捨てる**
   （音声窓 160 と PSSP clip 19 が満ちる）→ `t0` = 最初の audio msg の record ts + 10 s。
   終端は audio/camera の max ts の小さい方。10 秒以降は全 tick で全モード計算可能 → **有効性フラグは持たない**。
2. **各モードは自分の遅延窓を直接計算する**（グリッドずらし・補間はしない）:

   | 信号 | tick t で使う観測 | 遅延 |
   |---|---|---|
   | `GT(t)` | ts ≤ t の最新 160 audio（窓終端 = t） | 0（真値の基準） |
   | `DoA(t)` | ts ≤ **t − 0.2s** の最新 160 audio（窓終端 = t−0.2。例 t=1.5 → 0.84~1.3） | 0.2s（SM 生成） |
   | `PSSP(t)` | DoA と同じ最新窓（→t−0.2）を最新フレームとする 2Hz×10 フレーム clip → SimVP | 0.2s＋推論 |
   | `Tele(t)` | ts ≤ t の最新 room2 フレーム（顔検出、鮮度 ≤0.5s、無ければ None） | ~0.033s（フレーム量子化） |
   | `RAND(t)` | 観測非依存の乱数ウォーク（0.065 flip/tick、bag 名 seed で再現可能）→ `rand_side` | 0（決定レベルのベースライン） |

   VAD マスクは各 SM 生成で「最新 vad ≤（窓終端）を基準に 0.25s 窓」で判定（`was_vad_active_recently` と同一、GT/DoA/PSSP のみ）。
   head box は 30Hz 全フレーム再検出（HeadBoxAPI→HeadBoxProcessor）の ts ≤ t の最新。
3. **GT と DoA は別列**（同一アルゴリズムだが窓終端が 0.2s 違う → 別計算。0.2 は tick の整数倍でないため
   GT の流用不可）。→ **1 tick あたり beamforming 2 回**（GT 窓・DoA 窓）。PSSP の clip 入力は DoA の
   beamforming 履歴を 2Hz サブサンプルして再利用。ラベルは GT/DoA=4、Tele=L/R（None 可）。
4. **PSSP は 4 horizon 全部を保存**: SimVP pred_len=4 → **+0.5/+1.0/+1.5/+2.0s** の各予測 SM からラベルを抽出し
   `pssp_p05/p10/p15/p20` として保存（ライブは +1.0 のみ使用だが後段比較の柔軟性のため全部残す）。
5. **PSSP 精度の照合時刻**: horizon h は GT(t+h) と照合するのが**標準**（+1.0 は t=1.5 → **GT(2.5s)**）。
   **ただし厳密には** PSSP 入力窓 0.84~1.3 の中心は 1.07s なので、+1.0 予測の真の対象は **2.07s** であって 2.5s ではない
   （入力遅延分ずれる）。**当面は標準の GT(t+1.0)=2.5s で比較**し、この ~0.2–0.4s のずれは限界として記録、
   将来 2.07s 基準への補正を検討。
6. **録画信号のサンプル**: `/boxie/boxie_motors` の yaw 符号→L/R（left=yaw>0）、last-message-≤-t、ffill。
   実行系（executed κ・P(switch)）と Random 局の RAND 信号に使用。連続角度は保存しない。
7. **解釈用オフセット**（WP1 で実測・記録のみ）: SM 生成 ~0.2s、音声窓中心 0.23s、下流ホールド 1.0s＋EMA。
   実行系（motors）の時刻や lag 曲線を読むときに使う。
8. **別 PC 時計の監査**: `/room2_*` の `header.stamp − record_ts` 分布を bag 毎に測る（使うのは record ts）。

## 7. WP1 — bag 監査（`validate.py audit`、Mac で可）

bag 毎に validation-report.md へ: トピック有無と件数 / 主要トピックのメッセージ間隔 p50/p95/max
（audio ギャップ検出）/ 実効 tick レート / **SM 生成 ~0.2 s の確認**（/sm_without_transform の発行間隔）/
room2 時計オフセット / 以前除外の 2 bag が使用可能かの確認。

## 8. WP2 — 再現ゲート（Mac で可、DoA+PSSP 各 1 bag）

Todo-0702 §3.1 で合意済みの sanity check。全量実行前に必須:

0. **サウンドマップ動画で目視 QC**（`bag2video.py`、bag はスクリプト内にハードコード、args 不要）: 対象 bag を
   再生し、4 Hz tick 毎に SoundMapAPI で SM を再生成 → room1 カメラに重畳（4 ラベル・再検出 head box・
   speaking box・VAD 状態を注記、mode_doa の blend_img + plot_annotations 相当）、音声と同期した mp4 を出力
   （参照実装 `train_orig_sm_target_sm_gray (copy)/11/bag2Video.py` の音声再構成＋ffmpeg mux を流用）。
   数値ゲートの前に、SM が実際の話者を追えているかを目視確認する。
1. 再生成 SM vs **録画 `/sm_without_transform`**: 生 SM 類似度（Pearson r・最大画素位置）＋両者から抽出したラベルの一致率（ラベル抽出は決定的なので、これが決定再現の検証を兼ねる）。
2. TEL 再導出 vs 録画 `/tele/head_orientation`（yaw MAE・side 一致率）。
3. 再検出 head box vs 録画 `/head/head_box`（IoU・L/R 有効判定の一致率）。

**ゲート**: SM ラベル一致 ≥95%（DoA / PSSP とも同一生成器）、TEL side ≥95%。未達なら停止し原因究明
（窓整合 / uint8 量子化 / VAD タイミング / device 数値差 / mediapipe バージョン差）を
validation-report.md に記録してから進む。PSSP の SimVP 予測は録画に不可逆な参照しかない
（/pssp/predictions は正規化済み）ため、入力 SM の一致＋コード・重み（exp4）の同一性で担保する。
ここで acoular の 1 tick あたり処理時間も実測（WP3 の見積り）。

## 9. WP3 — 全量抽出（`extract.py`、**高スペック PC で実行**）

bag 毎に `results/ticks/{bag}.parquet` + `{bag}_sm.npz`（QC 用 SM スタック: `gt_sm` / `doa_sm` / `pred_p10`、
float16。任意・大容量）。bag 単位で resume 可能（処理済みスキップ、`--force` で再実行）。

実行コマンド（高スペック PC、CUDA 想定）:

```bash
cd analysis2/code
python extract.py \
  --rosbag-root "/path/to/WordWolfExp/ROSbag" \
  --bags all --device auto --workers 8 --out ../results/ticks
# 動作確認: --bags G11_game6_PSSP            （1 bag のみ）
# 中断後は同コマンドで再開（処理済み .parquet はスキップ、--force で再実行）
# --workers は物理コア数まで（律速は acoular の GT+DoA=2 SM/tick）
# --frame-stride N: MediaPipe を N フレーム毎に（1=全30Hz、速度優先なら 2-3）
# --save-sm: gt/doa/pred の SM スタックも npz 出力（大容量、任意）
```

parquet 列（1 行 = 1 tick）:

先頭 10 秒スキップ済みなので有効性フラグ列は無し。

| 列 | 内容 |
|---|---|
| `tick_ts`, `tick_idx` | §6.1 のグリッド（出力時刻軸） |
| `gt_label`, `gt_metrics_{L,R,T,O}` | 窓終端 t の SM 4 ラベル（真値）＋領域値 |
| `doa_label`, `doa_metrics_{L,R,T,O}` | 窓終端 t−0.2s の SM 4 ラベル（DoA 決定）＋領域値 |
| `pssp_p05/p10/p15/p20` | SimVP 4 horizon（+0.5/+1.0/+1.5/+2.0s）の各予測 SM の 4 ラベル |
| `tel_side` | room2 映像から再導出した L/R（yaw>0→left、顔なし・鮮度 >0.5s は None） |
| `rand_side` | **Random 方策の再現ベースライン** = 決定レベルの乱数ウォーク（0.065 flip/tick、bag 名派生 seed で再現可能）。全 bag に生成 |
| `headbox_l_*`, `headbox_r_*`, `headbox_carried` | 再検出＋HeadBoxProcessor 後 |
| `vad_active` | 窓終端時点の VAD |
| `motors_side` | 録画 `/boxie/boxie_motors` の yaw 符号→L/R（ffill）。実行レベル注視。Random 局ではこれが「実際に実行された乱数注視」 |

bag メタ（group/game/mode/duration）は `index.csv`。

## 10. WP4 — 解析（`code/analyses/`、ticks だけを読む。Mac で可）

### 10.1 同一観測の一致率 / confusion matrix（Todo §2.5 の本丸）

各ペアで行=A・列=B の計数行列と一致率（両者が定義される tick のみ）。4 ラベル同士は 4×4、
TEL/RAND を含むペアは 4 ラベル側が L/R の tick に絞って 2×2。
**GT** = 真値（窓終端 t、遅延 0）＝正しさの基準。**DoA** = モードの実決定（窓終端 t−0.2、遅延込み）。
PSSP は `pssp_p10`（+1.0s horizon）を主に使う。

| ペア | 形 | 目的 |
|---|---|---|
| PSSP_p10(t) vs GT(t+1s) | 4×4 | +1s 予測は当たるか（標準照合。厳密な対象 2.07s は §6.5） |
| GT(t) vs GT(t+1s) | 4×4 | 1s 持続ベースレート（予測の基線） |
| DoA(t) vs GT(t) | 4×4 | DoA モードの 0.2s 遅延ペナルティ |
| TEL(t) vs DoA(t) / PSSP_p10(t) | 2×2 | 人間 vs 各モード（同一時刻・各自の遅延込みで公平） |
| TEL(t) vs GT(t) | 2×2 | 人間 vs 真の発話者 |
| TEL(t) vs GT(t+lag), lag∈{−1,−0.5,0,0.5,1,1.5,2,2.5,3,5,10}s | 2×2×lag | 人間の追従ラグ曲線（旧 §3.7 の代替） |
| PSSP_p10(t) vs RAND(t) | 2×2 | 学習効果（≈50% 期待） |
| ↳ 補足 | 2×2 | Random 局で PSSP 出力が L/R の tick のみ × RAND |
| RAND(t) vs GT(t) | 2×2 | 操作チェック（≈50% 期待） |

※ RAND(t) = `rand_side`（決定レベルの再現可能な乱数ウォーク、全 bag で生成）。真機の乱数は無 seed で復元不可のため、Todo §2.5 の「各 bag で Random を再実行」＝観測非依存の乱数ウォークとして生成。乱数なので lag は不問。実際に実行された乱数注視が要る場合は Random 局の `motors_side` を使う。
※ 他 horizon（`pssp_p05/p15/p20`）も GT(t+0.5/1.5/2.0) と照合すれば予測地平線ごとの精度が出る（拡張、任意）。

＋ PSSP_p10(t)（参照 GT(t+1s)）の 4 ラベル precision/recall（メモ 1.1）。モード毎＋全体、付録に組毎。
要相談 D: PSSP vs RAND は偶然水準が期待値 — 解釈の言い回しは指導教員に確認。

### 10.2 発話者注視 κ（旧 §3.2 のやり直し＋Todo §2.4 の DoA 疑問への回答）

参照 = `GT` の L/R tick（真の発話者）。注視は 2 レベル併記:
(a) **決定レベル** = `doa_label` / `pssp_p10`（修正パイプラインの決定そのもの。アルゴリズムの正しさを分離）、
(b) **実行レベル** = `motors_side`（motor yaw 符号、ホールド 1 s＋EMA 込み）。
p_o / p_e / κ をモード毎に。想定: DoA の決定レベルは高い（教員の「>80%」への回答）。

### 10.3 音響シーン＋発話統計（旧 §3.1＋Todo §2.4）

`GT` から: 4 ラベル時間比率（モード毎）、ラベル毎 run 長分布（≒1 発話長）、
L/R 話者切替間隔の分布（中央値・p90、ヒストグラム）。

### 10.4 PSSP +1 s 予測精度（旧 §3.6 のやり直し）

report と同じ 2 指標を修正データ・exp4 で: L/R 限定正解率（母集団 = `GT(t+1s)∈{L,R}` の tick）と
4 ラベル全体正解率。予測 = `pssp_p10`、基線 = 持続（`GT(t)`）、照合先 = `GT(t+1s)`（標準。厳密対象 2.07s は §6.5）。
epoch31 の旧数値との差分を一度だけ記録。全 horizon（p05/p15/p20）版は任意拡張。

### 10.5 P(switch)（旧 §3.3 の再実行）

定義不変（3 分窓の yaw 符号反転 ÷ 720）。`p_switch_analysis.py` を移植して再実行（SM 非依存だが
次回報告の数値を全部 analysis2 産に揃えるため）。

### 実装しない

旧 §3.4 / §3.7(a)(b)（10.1 で代替）。

## 11. WP-S — 問卷復查（rosbag 不要）

1. GEQ を下位項目レベルに分解して条件別集計（`post_survey_results.txt` / `post_survey.py`）。
   教員の読み（Competence: Random 高 / PositiveAffect / Tension: PSSP 低は不思議）とデータの整合確認。
   ブロック中: PositiveAffect コメントの意図・「本人（前・3ケース）」の指す対象 → **教員に確認してから解釈**。
2. G12 の GEQ 逆転修正が使用テーブルに反映済みか再確認。
3. 対象外: 投稿先調査（Todo §3.4）・PSSP 再学習（§3.5）。再学習は本 spec の soundmap/pssp API を再利用予定。

## 12. 成果物対応

| 成果物 | 対応 |
|---|---|
| `docs/ros-topics.md` | 依頼項目 1（済） |
| `code/head_orientation.py` / `soundmap_api.py` / `pssp_api.py` / `head_box.py` | 依頼項目 2 / 3 / 4（＋head box 再検出） |
| `code/extract.py` + `results/ticks/` | 依頼項目 5 |
| `code/bag2video.py` | SM 目視 QC 動画（WP2 step 0） |
| `docs/validation-report.md` | データ正当性への疑義・時刻監査・再現ゲート |
| `results/metrics/`（10.1–10.5） | Todo 2.4 / 2.5 / メモ 1.1、旧 §3.1/3.2/3.3/3.6 のやり直し |

実行順: WP0 → WP1 → WP2（ゲート）→ WP3（高スペック PC）→ WP4 → 報告章の書き直し（別タスク）。
WP-S は独立にいつでも。

## 13. 未決事項（次回ミーティングへ）

1. 要相談 D: PSSP vs RAND 2×2 の結論の言い回し。
2. 「TEL/RAND を含むペアは常に L/R 限定 2×2」の一律適用の可否。
3. GEQ メモ 1.3 の 2 つの要確認。
4. analysis1 の 2 欠陥（生成器・重み）とその影響量の報告方法。
5. limitation 追記: ロボット挙動→人間の発話行動への影響を無視 / 導入スライドの注意誘導（メモ 1.2）。

## 14. リスク

- acoular の CPU 処理時間: WP0 実測 ≈ **0.33 s/SM**（M1 Mac）。GT+DoA=2 SM/tick → ~0.66 s/tick →
  52 bag 単一スレッドで ~8 時間 → WP3 は高スペック PC で bag 並列（`--workers`）必須。
- vendored acoular の依存関係: **numpy<2 必須**（acoular 24.05 + numba）。WP0 で検証済みの組合せを
  `code/requirements.txt` にピン留め（numpy 1.26.4 / traits 6.4.3 / mediapipe 0.10.14 等）。
- MediaPipe のバージョン差（head box・head orientation とも）→ WP2 の照合で定量化。
- WP2 ゲート未達 = ライブ挙動が仕様通り再現できないことを意味する。勝手に方針変更せず、
  録画トピックの部分利用などの代替案はユーザに戻して判断を仰ぐ。
