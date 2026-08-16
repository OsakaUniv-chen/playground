# PC-D 高性能サーバ

推論用の GPU を積む 1 台。PC-B は入出力と収録に徹し、重い処理はここに置く。
設計は [../teleop-architecture.md](../teleop-architecture.md)。

**この機械に ROS は要らない。** 受信は gi + numpy だけで動き、頭部指令は TCP で
PC-C へ投げて向こうで ROS に載せ替える（設計 §4.3）。理研にあり（RTX 3090）、
PC-C とは Tailscale で繋がる（§0.1）。

いまここで動いているのは**音声 -> 文字**まで。VLM の判断（`head_controller.decide()`）
は未実装で、その入力を先に用意してある状態。

```
OME ──> audio_send.py ──TCP──> asr.py ──> 書き起こし ──> （将来）VLM ──> 頭部指令
        Python 3.8              Python 3.10
        GStreamer が要る側       GPU が要る側
```

## 1. フォルダを置く

PC-D に置くのは **`common/` と `pc-d-server/` の 2 つ**。**必ず並べて置く。**
`env.sh` は `../common/config.env` を、`recv_ome.py` は
`../common/ome_receiver.py` を相対パスで引く（**受信の実体はこの
`ome_receiver.py` のほう**で、`pc-d-server/` だけ配ると何も受けられない）。

手元（この repo）の `system/` で:

```bash
rsync -a --exclude __pycache__ --exclude log common pc-d-server chen@<PC-D の tailscale アドレス>:~/p32/
```

```
~/p32/common/        config.env・ome_receiver.py（受信の実体）
~/p32/pc-d-server/   2 以降の作業はすべてこのディレクトリで行う
~/p32/venv-asr/      文字起こし用の Python 3.10（次の節で作る）
```

## 2. 環境を作る

**Python が 2 つ要る。** 1 つで済ませられないのは次の理由:

| | 何が要る | なぜ分けるか |
|---|---|---|
| `audio_send.py` | `gi`（GStreamer） | focal の `python3-gi` は **3.8 用しか無い**。3.10 からは import できない |
| `asr.py` | faster-whisper | **3.8 には入らない**（実測でビルドが失敗する） |

間は 16 kHz 単声道 PCM の localhost TCP で繋ぐ。OS の新しい機械へ移して
両方が同じ Python で動くようになったら、この 1 本は落とせる。

**2 本が共有する取り決めは `asr_protocol.py` にだけ書く**（電文の形と
16 kHz 単声道）。別々の Python で動くので、各ファイルに値を書くと片方だけ
直したときに黙って食い違い、**音は流れ続けたまま whisper が別の速さで読んだ
「それらしい文字」が出る**という形で外れる。なお **16 kHz は whisper の
入力仕様であって設定項目ではない**ので、`config.env` には置いていない
（現場で動かす値 ── モデル・言語・窓 ── は `config.env` の `ASR_*`）。
`asr_protocol.py` は受信側が 3.8 なので、**3.8 で読める範囲に保つこと。**

### 2.1 受信側（システムの python3）

**導入済み。共有機なので環境は変えない。** Ubuntu 20.04 / Python 3.8 /
GStreamer **1.16.3**（PC-C の 1.20 より古く、`webrtcbin` の `latency` は
1.18 から ── `ome_receiver.py` はプロパティの有無を見てから設定する）。

```bash
sudo apt install gir1.2-gst-plugins-bad-1.0 gstreamer1.0-nice   # 導入済み
python3 -c "import gi, numpy; print('OK')"
gst-inspect-1.0 nicesrc >/dev/null && echo "nice OK"
```

**`gstreamer1.0-nice` は OME からの受信に必須。** 無いと警告だけ出して
`create-answer` が黙って失敗する（理由は [../README.md](../README.md) §1.3）。

### 2.2 文字起こし側（Python 3.10 の venv）

新しい機械に移すときはこの節だけをやり直す。**sudo は要らない。**

```bash
# 3.10 の入手先。この機械では anaconda3 に 3.10.9 が入っているのでそれを使う
# （conda の環境は作らない ── venv を 1 つ置くだけで、共有の base を汚さない）
~/anaconda3/bin/python -m venv ~/p32/venv-asr
~/p32/venv-asr/bin/pip install --upgrade pip setuptools wheel
~/p32/venv-asr/bin/pip install faster-whisper

# **CUDA 12 のランタイムを pip で入れる。** ctranslate2 4.x は CUDA 12 を
# 要求するが、この機械のシステム CUDA は 11.0/11.1。入れないと
# 「モデルの読み込みだけ成功して推論で libcublas.so.12 not found」になる
# ── 起動直後は正常に見えるので気付きにくい。
~/p32/venv-asr/bin/pip install nvidia-cublas-cu12 nvidia-cudnn-cu12
```

`LD_LIBRARY_PATH` は `env.sh` が組み立てて、`run.sh` が **asr プロセスにだけ**
渡す（受信側は GStreamer なので、その loader に CUDA を混ぜない）。

確認:

```bash
source env.sh
LD_LIBRARY_PATH="$ASR_LD_LIBRARY_PATH" "$ASR_PYTHON" -c "
import ctranslate2; print('CUDA devices', ctranslate2.get_cuda_device_count())"
```

### 2.3 この機械での実測（3090）

| | |
|---|---|
| モデル | `medium` / float16 / cuda |
| VRAM | **2.5 GB**（24 GB のうち。large-v3 でも余裕はある） |
| 速度 | 11 秒の音声を 0.26 秒 = **43x 実時間**。3.5 秒の発話で 0.38 秒 |
| 初回起動 | モデルの取得に 2 分ほど。2 回目以降は 1.2 秒 |

`config.env` の `ASR_MODEL` を変えれば `large-v3` にも `small` にも振れる。
**GPU を VLM と分け合うようになったら**、ここを落とすか `ASR_DEVICE=cpu`
にする余地がある（速度に 40 倍の余裕がある）。

## 3. 設定を埋める

| ファイル | 埋めるもの |
|---|---|
| `config.env` | `OME_HOST` と `HEAD_RELAY_HOST` ── **どちらも PC-C の tailscale アドレス**（PC-C 側で `tailscale ip -4`）。`ASR_PYTHON` は 2.2 で作った venv |

`../common/config.env` の `PC_C_IP` は **PC-C の LAN 側**アドレスなので、PC-D
からは届かない。`config.env` の `OME_HOST` がそれを上書きする形になっている。

**Tailscale はノード共有（Share device）では駄目。** 共有された側から相手の
tailnet へ発起できないため、signalling も頭部指令も通らない。正式メンバーとして
同じ tailnet に入れること（[../todo-list.md](../todo-list.md)）。

## 4. 起動

```bash
cd ~/p32/pc-d-server
./run.sh              # asr + audio_send
./run.sh status       # 生きているか      ./run.sh stop で停止
```

`run.sh` が中で `env.sh` を読むので `source` は要らない。**PC-B / PC-C との
起動順は気にしなくてよい** ── 送出側が未起動でも 5 s ごとに繋ぎ直す。

書き起こしは 2 か所に出る:

| | |
|---|---|
| `log/asr.log` | 1 発話 1 行。動いているかはここを見る |
| `log/transcript.jsonl` | 確定した発話を JSON で 1 行 1 件。**この系で PC-D に残る唯一の記録**（映像や音声そのものは PC-B の bag にしかない） |

```
[INFO] [asr]: operator  3.5s -> 0.38s ( 9.1x) : すみません、ちょっといいですか
```

送出側が居ないうちは `Cannot create offer（code=404）` を 5 秒ごとに出す。
**異常ではない** ── PC-B / PC-C が上がれば勝手に繋がる。

## 5. 文字起こしの窓（VLM を繋ぐときに調整する）

**窓は 2 つあり、決める理由がまったく別。混ぜないこと。**

### (1) 切り出しの窓 ── 音声をどこで区切って whisper に渡すか

**固定長で切らない。** 固定長だと語の途中で切れて、境目で欠落や重複が出る。
無音で区切り、長すぎる発話だけ強制的に切る。

| 変数 | 既定 | 意味 |
|---|---|---|
| `ASR_SILENCE_SEC` | 0.6 | これだけ無音が続いたら発話の終わり |
| `ASR_MIN_SPEECH_SEC` | 0.4 | これ未満の音は発話とみなさず捨てる |
| `ASR_MAX_SEGMENT_SEC` | 8 | 喋り続けているときの強制的な区切り |

喋っているかどうかは RMS で見るが、**閾値は固定していない**。現場の暗騒音は
マイクと会場で桁が変わるので、静かな側の分位点を暗騒音として追い、その 3 倍を
超えたら発話とする。**定常的な音（空調など）は背景として扱われる**
（440 Hz の正弦を流し続けると、最初の 1 区間だけ拾ってあとは無視した ──
狙いどおりの挙動）。

### (2) 文脈の窓 ── VLM に見せる書き起こしの長さ

`ASR_CONTEXT_SEC`（既定 **15 秒**）。

判断させたいのは「**頭を誰に向けるか**」なので、**直近 2〜4 ターンぶんあれば
足りる**（会話の 1 ターンは 2〜8 秒）。長くすると、もう終わった話題の話者に
引きずられるうえ、トークンだけ増える。**VLM を繋いだら、まずここだけを
動かして調整する**（切り出し側は音の切れ目の話なので、触る必要はない）。

**発話は音源ごとに分けて持つ。** `mic`（現場）と `operator`（操作者）は
別物として記録される ── 「操作者が今なにか聞いた」のか「来場者が喋った」のかで
向く先が変わるので、混ぜると判断に使えない。

## 6. VLM を書くときの入口

`asr.py` の `Transcriber` を import する。**同じ Python 3.10 の venv で動かす**
（VLM も faster-whisper と同居させるほうが、書き起こしを取りに行くための
プロセス間通信が要らない）。

```python
from asr import Transcriber

tr = Transcriber()                 # モデルを読む
...
prompt_part = tr.text()            # 直近 ASR_CONTEXT_SEC 秒
#   mic: すみません、ちょっといいですか
#   operator: はい、なんでしょう
utts = tr.recent(seconds=8)        # Utterance の列（source / text / t_start / t_end）
```

映像はまだこの経路に載せていない。**VLM の周期が決まってから**にするため
（30 fps の生 RGB をそのまま TCP に流すと 100 MB/s になる。推論周期に
合わせて間引いてから渡すことになる）。受け口は `recv_ome.py` の
`latest_video("stream")` / `latest_video("soundmap")` に既にある。

## 7. 4 入力の受け方（recv_ome.py）

```python
from recv_ome import OmeInputs

inp = OmeInputs()                   # 接続先は OME_HOST / OME_WS_PORT / OME_APP
inp.start()
f = inp.latest_video("stream")      # "stream" | "soundmap"
if f is not None and f.age_sec() < 0.5:
    rgb = f.array()                 # (h, w, 3) uint8
a = inp.latest_audio("mic")         # "mic" | "operator"
```

**持つのは「いま最新の 1 枚」だけで履歴は溜めない。** 推論が間に合わなければ
古い枚は黙って捨てられるので、`age_sec()` で鮮度を見てから使う。

**音声だけは例外で、連続で要る。** 落ちたぶんの発話が丸ごと消えては
文字起こしにならないので、`add_audio_sink(fn)` で全バッファを受け取れる
（`audio_send.py` がそうしている）。

音響マップは **64×64 のまま**届く。拡大が要るなら受け側の前処理でやる
（送出側で引き伸ばしても情報量は増えない ── 設計 §3.1）。

## 8. 頭部指令と、音響マップ -> 角度

**音響マップの角度は「いまの頭からの相対」。** 16ch アレイはカメラと一緒に
頭部へ載るので、マップの中心は機体の正面ではなく**そのときの頭の向き**。
一方 `head/command` は絶対角（起動時の姿勢が 0）なので、

    次の絶対角 = いまの絶対角 + マップから読んだ相対角

`いまの絶対角` は **PC-D が自分で覚える**（PC-B はモータを読み戻さない ──
BLE の帯域。設計 §5.1）。`HeadController.look_relative()` がその足し込みを
やる。**`settled()` が True になるまで次を出さないこと** ── PC-B の
smoothing が収まるのに約 1.5 秒かかり、途中の姿勢を「いまの角度」と
みなすとずれが足し算で溜まる。

画素から角度への対応付けは `soundmap_geometry.py`。**生成器の写像から導いて
検算済み**（893 格子点で中央値 0.13 度・最大 0.31 度）。検算は再現できる:

```bash
python3 soundmap_geometry.py      # numpy と scipy、PC-B 側のフォルダが要る
```

方位等距離投影なので、中心が正面、中心から 32 画素が正面から 90 度。
**軸は直感と縦が逆**で、`col` が大きいほど右、`row` が**小さい**ほど上。

`decide()` は未実装。**戻り値は相対角 `(d_yaw, d_pitch)`** と決めてある。
経路を実機で 1 度通すためだけの当て馬として `decide_loudest()`（いちばん
強い所を向くだけ）を置いてあるが、**誰が喋っているかを見ていないので
demo には使えない。**

### 未実装の中身

`HeadController.publish_goal(pitch, yaw)` で出す。**値が変わったときだけ
送る**（PC-B 側の smoothing がタイマで間を埋める）。可動域は PC-B が掛けるので、
こちらは制限を知らずに指令を出してよい。中継が落ちていても 5 秒ごとに繋ぎ直し、
送れなかったぶんは捨てる（古い指令で首が動かないように）。

`decide()` が未実装なので `head_controller.py` は単体では動かない
（[../teleop-architecture.md](../teleop-architecture.md) §0.3）。
