## 1. Agenda (Meeting Goals)

- **Objective:** ワードウルフ実験の解析を締め、投稿先を確定し、次のステップ（新モデル学習・次実験）の計画を承認いただく。
- **Success Criteria (Definition of Done):**
  - [ ] 解析レポートの結論に合意 — 追加解析の要否を判断
  - [ ] 投稿先を1つ決定（＋締切に合わせた執筆スケジュール確認）
  - [ ] 新データでの新モデル学習の**時間表**を承認
  - [ ] 次実験の方向性と博士修了に向けたスケジュール感を確認
- **List of items to discuss:**
  1. ワードウルフ実験 行動データ解析レポート
  2. ワードウルフ実験 論文の投稿先
  3. 新データによる新モデル学習（時間表つき）
  4. 次の実験の計画（博士修了との関係）

---

## 2. Discussion Items

---

### Item 1: ワードウルフ実験 行動データ解析レポート

- **Purpose:** 解析結果とメインストーリーに合意し、追加解析が要るかを判断する。
- **Link:** https://docs.google.com/document/d/19N6_yG-8WOyf11YJYTwiwpkmXLO8iVhC7gPFdoSFUyk/edit?usp=drive_link
- **How to read / focus points:**
  - 要点は §0（結果一覧・結論4点）を参照。
  - 行動レベルではモード差を検出できる（PSSPの+1s発話者予測 左右54.8% > 偶然50%・持続基線45.0%、§4.1.1）。
  - 一方、主観評価は全尺度で群間差なし（GQS4/5・GEQ5/5、§5.4）。効いたのは注視の「対象」ではなく信念（相関 d=0.49–0.73、§5.5）。
- **Author's Conclusion (1 line):**
  > 予測ベースの先読み注視（PSSP）は、偶然水準のモード（Random）と比べて社会的知覚を改善しない。解析は概ね完了、投稿へ進めたい。

---

### Item 2: 論文の投稿先

- **Purpose:** 投稿先を1つに決める。
- **Link:** https://docs.google.com/document/d/1BowCp4S_F34Ldqy4HF5GuK7y-RmSWCBDymKXYjJjoIE/edit?usp=drive_link
- **How to read / focus points:**
  - 結果はヌル/負中心＋方法論的貢献＋信念の相関 → **新規性よりも健全性で評価される中位誌**が相性良し。
  - 誌の第一候補: MDPI MTI（~70%） / 保険: PLOS ONE（~60%）。
  - 会議の選択肢と締切: Humanoids 2026（**7/24**）/ ROBIO 2026（**8/1**）— 締切が近く、選ぶなら即着手が必要。
- **Author's Conclusion (1 line):**
  > 会議（Humanoids/ROBIO）か誌（MTI）かを決める。締切的には会議を狙うなら今すぐ執筆開始。

---

### Item 3: 新データによる新モデル学習

- **Purpose:** 新データで新モデルを学習する計画（時間表）を承認いただく。
- **Link:** <!-- Google Doc link (to be added) -->
- **How to read / focus points:** 下記は暫定スケジュール案。データ量・GPU 状況で前後する。

  | フェーズ | 内容 | 期間（目安） |
  |---|---|---|
  | P1 データ準備 | 新データの前処理・アノテーション・index 作成・train/val 分割 | 第1週 |
  | P2 ベースライン | 既存モデルを新データでそのまま学習・評価（現状把握） | 第2週 |
  | P3 改良・調整 | ハイパラ／構造の調整、アブレーション | 第3〜4週 |
  | P4 評価・比較 | 旧モデルとの定量比較、行動監査での検証 | 第5週 |
  | P5 まとめ | 結果整理・レポート化 | 第6週 |

- **Author's Conclusion (1 line):**
  > 約6週間で新モデルのベースライン→改良→比較まで到達する見込み。開始日と使用リソースを確定したい。

---

### Item 4: 次の実験の計画（博士修了との関係）

- **Purpose:** 次実験の方向性を決め、博士修了に間に合うスケジュールかを確認する。
- **Link:** <!-- Google Doc link (to be added) -->
- **How to read / focus points:**
  - 今回の知見（注視の「対象」より「動作の質」「信念」が効く）を踏まえた次の問い設定。
  - 博士修了に必要な業績・残タスクと逆算した実施可能時期の確認。
- **Author's Conclusion (1 line):**
  > 次実験のテーマ候補と、修了要件から見た優先順位についてご相談したい。

---

## 3. Meeting Wrap-up

<!-- Update in real-time during the meeting -->

- **Review of Success Criteria:**
  - [ ] 解析レポートに合意 → **【Result: 】**
  - [ ] 投稿先を1つ決定 → **【Result: 】**
  - [ ] 新モデル学習の時間表を承認 → **【Result: 】**
  - [ ] 次実験の方向性・修了スケジュールを確認 → **【Result: 】**
