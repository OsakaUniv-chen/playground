# vad_check — room1 発話ゲートの選定と VAD 重畳動画（QC）

発話ターン長が短く出る疑い（誰も話していない区間でも beamforming ラベルが跳ねる）を確かめ、
**room1 の実発話区間**を検出する手法を選ぶための作業場所。選んだゲートは
[`../code/room1_vad.py`](../code/room1_vad.py) に一元化し、発話ターン長の**追加集計**
（`analyses/turn_length_vad.py` → `results/metrics/10_3b_speech_vadgated.md`）に使う。
既存結果は上書きしない。

## 選定の経緯（→ 採用: silero strict）
1. A=音圧しきい値 vs B=webrtcvad … 音圧は webrtcvad に劣り却下。
2. A=silero(緩) vs B=webrtcvad … webrtcvad は小声を途中で落とすため却下。
3. A=silero緩 vs B=silero厳 … **B（strict: threshold 0.7 / ratio 0.6）を採用**。
   複数 bag で挙動が安定。パラメータは `room1_vad.py` に固定。

## いまの動画: room1 VAD のみ
`vad_compare.py` は選定後の確認用に、同じシーン（room1 魚眼＋スクロール帯＋room1 マイク音声）で
**room1 VAD**（silero, `room1_vad.py`）を重ねる。room1 マイク16chの発話ゲートで、**GT と同じ窓
`[t-0.46s, t]`** で判定（silero 自体はクリップ全体をストリーミングして RNN 文脈を保ち、各窓と積を取る）。
帯の描画は `../code/vad_overlay.py` に共通化し、bag2video / bag2video_all_bag も同じ帯を出す。

## 実行
```
cd analysis2/vad_check
OPENBLAS_NUM_THREADS=1 ../code/venv/bin/python vad_compare.py [BAG_NAME] [START_S] [DURATION_S]
# 既定: G11_game4_DoA 60s..120s → out/G11_game4_DoA_vad_compare.mp4
```
出力 mp4（720x980, 15fps, room1 音声つき）:
- 上: room1 魚眼。下端に room1 VAD の現在状態・room1 dBFS・時刻。
- 下: スクロール帯（灰=room1 RMS 包絡（参考）、青=room1 VAD、右端=現在線。ラベルは黒背景で
  帯に隠れない）。

（目安: G11_game4_DoA 60–120s で room1 VAD ≈ 73%。）

依存: `silero-vad`（`requirements.txt` に追記済み、モデル同梱で実行時DLなし、torch 既存）。
`webrtcvad-wheels` は選定段階で使ったのみで現行スクリプトでは未使用。
