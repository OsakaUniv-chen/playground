"""Fetch and print the post-survey responses from Google Sheets.

The sheet is shared via link, so it can be read without credentials through
Google's CSV export endpoint. No auth / gspread / pandas required.
"""
import csv
import io
import os
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict
from contextlib import redirect_stdout

import numpy as np
import requests

from utils.utils import COND_NAMES, GEQ_SCALES, GQS_SCALES, Tee, plot

SHEET_ID = '1JzzvB06Ovemio3ilvPi5BhTNewDLbqmF8J9eqvdkYrg'
# https://docs.google.com/spreadsheets/d/1JzzvB06Ovemio3ilvPi5BhTNewDLbqmF8J9eqvdkYrg/edit
CSV_URL = f'https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv'

# Restrict the analysis to a subset of groups, e.g. [1] or [1, 2, 3, 8].
# Leave empty ([]) to use all groups.
GROUP_FILTER = []

# Survey columns used for the wolf join.
COL_GROUP = 1      # グループ番号 == group_id in the wolf table below.
COL_PID = 2        # 参加者ID (A/B/C) == teleoperator label in the wolf table.
COL_GAME_ID = 3    # ゲームID == game_id in the wolf table below.
COL_WOLF_GUESS = 4  # participant's answer: who they thought the wolf was.
COL_TELEOP_GUESS = 7  # 人による遠隔操作だと思いますか？ (はい/いいえ)
COL_TELEOP_CONF = 8   # 上記回答への自信 (高い/普通/低い)

# Perceived Teleoperation Likelihood: guess (はい/いいえ) + confidence mapped to
# a single 0-100% subjective-probability scale (higher = more "human-operated";
# the はい/いいえ boundary is 50%).
PTL_MAP = {
    ('いいえ', '高い'): 0,
    ('いいえ', '普通'): 20,
    ('いいえ', '低い'): 40,
    ('はい', '低い'): 60,
    ('はい', '普通'): 80,
    ('はい', '高い'): 100,
}

# ---------------------------------------------------------------------------
# Subscale -> survey column ranges (0-based, end-exclusive). Scale names are
# imported from utils so the plot data keys match exactly.
# GQS (Godspeed) columns 9-31; GEQ columns 32-53.
# ---------------------------------------------------------------------------
GQS_ITEMS = {
    GQS_SCALES[0]: [9, 10, 11, 12, 13],        # Anthropomorphism (5 items)
    GQS_SCALES[1]: [14, 15, 16, 12, 17, 18],   # Animacy (6; shares col 12,
                                               # 人工的な↔生物的な, with Anthro)
    GQS_SCALES[2]: [19, 20, 21, 22, 23],       # Likeability (5)
    GQS_SCALES[3]: [24, 25, 26, 27, 28],       # Perceived Intelligence (5)
    GQS_SCALES[4]: [29, 30, 31],               # Perceived Safety (3; col 31 reversed)
}
GEQ_ITEMS = {
    GEQ_SCALES[0]: range(32, 37),   # Flow (5)
    GEQ_SCALES[1]: range(37, 42),   # Competence (5)
    GEQ_SCALES[2]: range(42, 47),   # Positive Affect (5)
    GEQ_SCALES[3]: range(47, 51),   # Negative Affect (4)
    GEQ_SCALES[4]: range(51, 54),   # Tension (3)
}
PRACTICE_MODES = {'Onsite', 'Video'}  # GQS not collected (no robot strategy)
LIKERT_MIN = 1
LIKERT_MAX = 5
REVERSE_CODE_ITEMS = {
    31,  # Perceived Safety: 平穏な (1) <-> 驚いた (5), so higher = safer.
}

# ---------------------------------------------------------------------------
# Ground-truth wolf / teleoperator assignment (kept out of the survey to avoid
# leaking it). Extracted from the per-group 実験計画表 (page 1 of each
# G*_カラー.pdf): the wolf is the red-marked minority word, the teleoperator is
# the blue-highlighted cell ('-' for the onsite game, which has no remote
# participant). The join key is (group_id, game_id) — both appear as the
# auto-filled グループ番号 / ゲームID in each survey response.
# ---------------------------------------------------------------------------
WOLF_TABLE_RAW = """\
group_id | game_id | mode   | wolf | teleoperator
1  | 1 | Onsite | B | -
1  | 2 | Video  | A | A
1  | 3 | Tele   | A | B
1  | 4 | PSSP   | C | C
1  | 5 | DoA    | B | A
1  | 6 | Random | B | B
2  | 1 | Onsite | B | -
2  | 2 | Video  | C | A
2  | 3 | PSSP   | A | B
2  | 4 | DoA    | A | C
2  | 5 | Tele   | C | A
2  | 6 | Random | B | B
3  | 1 | Onsite | A | -
3  | 2 | Video  | C | A
3  | 3 | Tele   | C | B
3  | 4 | DoA    | C | C
3  | 5 | PSSP   | C | A
3  | 6 | Random | A | B
4  | 1 | Onsite | B | -
4  | 2 | Video  | B | A
4  | 3 | DoA    | B | B
4  | 4 | PSSP   | A | C
4  | 5 | Tele   | C | A
4  | 6 | Random | B | B
5  | 1 | Onsite | B | -
5  | 2 | Video  | B | A
5  | 3 | Random | A | B
5  | 4 | Tele   | C | C
5  | 5 | PSSP   | A | A
5  | 6 | DoA    | A | B
6  | 1 | Onsite | B | -
6  | 2 | Video  | B | A
6  | 3 | Tele   | B | B
6  | 4 | Random | B | C
6  | 5 | PSSP   | B | A
6  | 6 | DoA    | B | B
7  | 1 | Onsite | A | -
7  | 2 | Video  | B | A
7  | 3 | DoA    | A | B
7  | 4 | Tele   | B | C
7  | 5 | Random | A | A
7  | 6 | PSSP   | A | B
8  | 1 | Onsite | C | -
8  | 2 | Video  | B | A
8  | 3 | PSSP   | A | B
8  | 4 | Random | C | C
8  | 5 | Tele   | A | A
8  | 6 | DoA    | A | B
9  | 1 | Onsite | A | -
9  | 2 | Video  | C | A
9  | 3 | Random | A | B
9  | 4 | DoA    | C | C
9  | 5 | PSSP   | B | A
9  | 6 | Tele   | C | B
10 | 1 | Onsite | C | -
10 | 2 | Video  | C | A
10 | 3 | PSSP   | B | B
10 | 4 | Tele   | A | C
10 | 5 | DoA    | A | A
10 | 6 | Random | A | B
11 | 1 | Onsite | C | -
11 | 2 | Video  | B | A
11 | 3 | Random | B | B
11 | 4 | DoA    | C | C
11 | 5 | Tele   | C | A
11 | 6 | PSSP   | B | B
12 | 1 | Onsite | C | -
12 | 2 | Video  | A | A
12 | 3 | Tele   | A | B
12 | 4 | PSSP   | C | C
12 | 5 | Random | B | A
12 | 6 | DoA    | C | B
13 | 1 | Onsite | C | -
13 | 2 | Video  | B | A
13 | 3 | DoA   | C | B
13 | 4 | Random   | B | C
13 | 5 | PSSP | C | A
13 | 6 | Tele    | C | B
"""


def parse_wolf_table():
    """Parse WOLF_TABLE_RAW into {(group_id, game_id): {group_id, game_id,
    mode, wolf, teleoperator}}."""
    lines = [ln for ln in WOLF_TABLE_RAW.strip().splitlines() if ln.strip()]
    header = [c.strip() for c in lines[0].split('|')]
    table = {}
    for ln in lines[1:]:
        cells = [c.strip() for c in ln.split('|')]
        rec = dict(zip(header, cells))
        table[(rec['group_id'], rec['game_id'])] = rec
    return table


WOLF_TABLE = parse_wolf_table()


def fetch_rows():
    """Return the survey as (header, rows) where each row is a list of cells."""
    resp = requests.get(CSV_URL, timeout=30)
    resp.raise_for_status()
    resp.encoding = 'utf-8'
    reader = csv.reader(io.StringIO(resp.text))
    rows = list(reader)
    if not rows:
        return [], []
    return rows[0], rows[1:]


def enrich_with_wolf(header, rows):
    """Return (header, rows) with wolf-truth columns appended to each row.

    Joins on (グループ番号, ゲームID) == (group_id, game_id). Adds:
      mode, true_wolf, teleoperator, wolf_correct
    `wolf_correct` compares the participant's guess (col 4) to the truth
    ('TRUE'/'FALSE'/'' when no match).
    """
    extra = ['mode', 'true_wolf', 'teleoperator', 'wolf_correct']
    new_header = header + extra
    new_rows = []
    for row in rows:
        group = row[COL_GROUP].strip() if len(row) > COL_GROUP else ''
        game_id = row[COL_GAME_ID].strip() if len(row) > COL_GAME_ID else ''
        rec = WOLF_TABLE.get((group, game_id))
        if rec:
            guess = (row[COL_WOLF_GUESS].strip()
                     if len(row) > COL_WOLF_GUESS else '')
            correct = '' if not guess else str(guess == rec['wolf']).upper()
            row = row + [rec['mode'], rec['wolf'], rec['teleoperator'],
                         correct]
        else:
            row = row + ['', '', '', '']
        new_rows.append(row)
    return new_header, new_rows


def drop_teleoperator_rows(rows):
    """Remove a teleoperator's own response for any game where they operated
    the robot instead of playing (every game with a teleoperator, i.e. games
    2-6; the onsite game 1 has none).

    Returns (kept_rows, dropped_rows). Matches on (group, game) -> teleoperator
    against the participant's own ID.
    """
    kept, dropped = [], []
    for row in rows:
        group = row[COL_GROUP].strip() if len(row) > COL_GROUP else ''
        pid = row[COL_PID].strip() if len(row) > COL_PID else ''
        game = row[COL_GAME_ID].strip() if len(row) > COL_GAME_ID else ''
        rec = WOLF_TABLE.get((group, game))
        if rec and rec['teleoperator'] != '-' and pid == rec['teleoperator']:
            dropped.append(row)
        else:
            kept.append(row)
    return kept, dropped


def filter_groups(rows, groups=None):
    """Keep only rows whose group id is in `groups` (e.g. [1, 2, 8]); defaults
    to GROUP_FILTER. Empty/None keeps all groups."""
    if groups is None:
        groups = GROUP_FILTER
    if not groups:
        return rows
    wanted = {str(g) for g in groups}
    return [r for r in rows
            if (r[COL_GROUP].strip() if len(r) > COL_GROUP else '') in wanted]


def _item_score(row, col):
    """Return a 1-5 item score, applying reverse coding where needed."""
    if col >= len(row) or not row[col].strip():
        return None
    try:
        value = int(row[col])
    except ValueError:
        return None
    if not LIKERT_MIN <= value <= LIKERT_MAX:
        return None
    if col in REVERSE_CODE_ITEMS:
        return LIKERT_MIN + LIKERT_MAX - value
    return value


def _subscale_mean(row, cols):
    """Mean of a participant's scored items for one subscale.
    Returns None if no numeric item is present."""
    vals = []
    for c in cols:
        score = _item_score(row, c)
        if score is not None:
            vals.append(score)
    return sum(vals) / len(vals) if vals else None


def build_plot_data(rows):
    """Aggregate enriched survey rows into the `data` structure used by
    utils.plot: {condition: {subscale: np.array([per-participant mean,
    ...])}}. GQS is skipped for practice conditions (Onsite/Video)."""
    acc = {cond: defaultdict(list) for cond in COND_NAMES}
    for row in rows:
        mode = row[-4]                      # 'mode' from enrich_with_wolf
        if mode not in acc:
            continue
        scales = dict(GEQ_ITEMS)
        if mode not in PRACTICE_MODES:
            scales = {**GQS_ITEMS, **GEQ_ITEMS}
        for scale, cols in scales.items():
            m = _subscale_mean(row, cols)
            if m is not None:
                acc[mode][scale].append(m)
    return {cond: {s: np.array(v) for s, v in sc.items()}
            for cond, sc in acc.items()}


def analyze_teleoperation(rows):
    """Print, per mode: wolf-identification accuracy, mean PTL (0-100%), and
    Teleoperation Yes-Rate. PTL / Yes-Rate only exist for robot conditions
    (the teleoperation question is not asked in practice)."""
    by_mode = defaultdict(list)
    for r in rows:
        by_mode[r[-4]].append(r)   # 'mode' from enrich_with_wolf

    print('=' * 72)
    print('Teleoperation metrics by mode')
    print('=' * 72)
    print(f"{'mode':<8}{'wolf_acc':>16}{'PTL (mean±sd)':>22}{'yes_rate':>16}")
    for cond in COND_NAMES:
        rs = by_mode.get(cond)
        if not rs:
            continue
        wc = [r[-1] for r in rs if r[-1] in ('TRUE', 'FALSE')]
        acc = (f"{100*sum(x=='TRUE' for x in wc)/len(wc):.0f}% "
               f"({sum(x=='TRUE' for x in wc)}/{len(wc)})" if wc else '-')
        ptl = [PTL_MAP[(r[COL_TELEOP_GUESS].strip(), r[COL_TELEOP_CONF].strip())]
               for r in rs
               if (r[COL_TELEOP_GUESS].strip(),
                   r[COL_TELEOP_CONF].strip()) in PTL_MAP]
        ptl_s = (f"{np.mean(ptl):.0f}±{np.std(ptl, ddof=1) if len(ptl)>1 else 0:.0f}%"
                 f" (n={len(ptl)})" if ptl else '-')
        guesses = [r[COL_TELEOP_GUESS].strip() for r in rs
                   if r[COL_TELEOP_GUESS].strip() in ('はい', 'いいえ')]
        yes = (f"{100*sum(g=='はい' for g in guesses)/len(guesses):.0f}% "
               f"({sum(g=='はい' for g in guesses)}/{len(guesses)})"
               if guesses else '-')
        print(f'{cond:<8}{acc:>16}{ptl_s:>22}{yes:>16}')


def analyze_subscales(data):
    """Print a table of mean ± SD per subscale (rows) x mode (columns)."""
    scales = list(GQS_ITEMS) + list(GEQ_ITEMS)
    conds = [c for c in COND_NAMES if data.get(c)]
    label_w = max(len(s.replace('\n', '')) for s in scales)
    col_w = 14

    print('Subscale scores: mean ± SD (rows = subscale, cols = mode)')
    header = ' ' * label_w + ''.join(f'{c:>{col_w}}' for c in conds)
    print(header)
    print('-' * len(header))
    for scale in scales:
        label = scale.replace('\n', '')
        cells = []
        for cond in conds:
            arr = data[cond].get(scale)
            if arr is None or len(arr) == 0:
                cells.append(f'{"-":>{col_w}}')
            else:
                sd = arr.std(ddof=1) if len(arr) > 1 else 0.0
                cells.append(f'{f"{arr.mean():.2f}±{sd:.2f}":>{col_w}}')
        print(f'{label:<{label_w}}' + ''.join(cells))


# ===========================================================================
# Survey analysis (player-eval.md §4)
#
# The single analysis is an individual-level linear mixed model fitted in R
# (lme4/lmerTest/emmeans). Python only prepares the data: it writes one row per
# (evaluator x condition) to a temporary long CSV and shells out to
# utils/player_lmm.R, whose text output is folded into the results file. The
# CSV is deleted afterwards. The model, the Satterthwaite-df omnibus and the
# planned PSSP contrasts live in utils/player_lmm.R.
# ===========================================================================
ROBOT_MODES = ['Tele', 'PSSP', 'DoA', 'Random']
# Clean, R-safe column names for each subscale, in GQS-then-GEQ order.
GQS_COLS = ['Anthropomorphism', 'Animacy', 'Likeability',
            'PerceivedIntelligence', 'PerceivedSafety']
GEQ_COLS = ['Flow', 'Competence', 'PositiveAffect', 'NegativeAffect', 'Tension']
R_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        'utils', 'player_lmm.R')


def build_long_rows(rows):
    """One record per (evaluator x condition) — the individual-level data the
    mixed model needs. Keys: group, pid, participant (group-unique id), mode,
    the 10 subscale means and PTL. GQS and PTL are blank for the practice
    conditions (Onsite/Video)."""
    out = []
    for row in rows:
        mode = row[-4]                       # 'mode' from enrich_with_wolf
        group = row[COL_GROUP].strip() if len(row) > COL_GROUP else ''
        pid = row[COL_PID].strip() if len(row) > COL_PID else ''
        if not mode or not group or not pid:
            continue
        rec = {'group': group, 'pid': pid,
               'participant': f'{group}_{pid}', 'mode': mode}
        if mode not in PRACTICE_MODES:
            for name, cols in zip(GQS_COLS, GQS_ITEMS.values()):
                rec[name] = _subscale_mean(row, cols)
            key = (row[COL_TELEOP_GUESS].strip(), row[COL_TELEOP_CONF].strip())
            rec['PTL'] = PTL_MAP.get(key)
        for name, cols in zip(GEQ_COLS, GEQ_ITEMS.values()):
            rec[name] = _subscale_mean(row, cols)
        out.append(rec)
    return out


def export_long_csv(rows, path):
    """Write the individual-level long data to `path` for the R model."""
    fields = (['group', 'pid', 'participant', 'mode']
              + GQS_COLS + GEQ_COLS + ['PTL'])
    with open(path, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
        w.writeheader()
        for rec in build_long_rows(rows):
            w.writerow({k: ('' if rec.get(k) is None else rec[k])
                        for k in fields})
    return path


def run_player_lmm(rows, script=R_SCRIPT):
    """Export the individual-level data to a temporary CSV, run player_lmm.R on
    it, delete the CSV, and return R's text output. Degrades gracefully if
    Rscript or the script is missing."""
    rscript = shutil.which('Rscript')
    if not rscript:
        return ('[R analysis skipped: Rscript not found. Install R and the\n'
                ' lme4/lmerTest/emmeans packages (see player-eval.md §4/§7).]')
    if not os.path.exists(script):
        return f'[R analysis skipped: {script} not found]'
    fd, csv_path = tempfile.mkstemp(prefix='post_survey_long_', suffix='.csv')
    os.close(fd)
    try:
        export_long_csv(rows, csv_path)
        proc = subprocess.run([rscript, script, csv_path],
                              capture_output=True, text=True)
        out = proc.stdout
        if proc.returncode != 0:
            out += f'\n[R exited {proc.returncode}; stderr]\n{proc.stderr}'
        return out
    finally:
        if os.path.exists(csv_path):
            os.remove(csv_path)


def append_extracted_csv(f, header, rows):
    """Dump the extracted/enriched survey data as CSV to the open results file.

    Written directly to `f` (not via stdout) so the full raw data used for this
    analysis is preserved alongside it — a lossless snapshot in case the source
    sheet later changes or is deleted. Cells keep their original text (csv quotes
    any embedded commas/newlines); only the header names are flattened.
    """
    f.write('\n' + '=' * 72 + '\n')
    f.write('EXTRACTED CSV (enriched survey data — kept for safekeeping)\n')
    f.write('=' * 72 + '\n')
    writer = csv.writer(f, lineterminator='\n')
    writer.writerow([c.replace('\n', ' ').strip() for c in header])
    writer.writerows(rows)


def plot_results(output_path='post_survey.pdf',
                 results_path='post_survey_results.txt'):
    """Fetch, enrich, drop teleoperator rows, aggregate subscales, save the
    text analysis to `results_path`, and plot."""
    header, rows = fetch_rows()
    header, rows = enrich_with_wolf(header, rows)
    rows = filter_groups(rows)            # restrict to GROUP_FILTER (if set)
    all_rows = rows                       # full enriched set, before the drop
    rows, dropped = drop_teleoperator_rows(rows)
    data = build_plot_data(rows)

    r_output = run_player_lmm(rows)

    with open(results_path, 'w', encoding='utf-8') as f, \
            redirect_stdout(Tee(sys.stdout, f)):
        scope = ', '.join(map(str, GROUP_FILTER)) if GROUP_FILTER else 'all'
        print(f'Groups analyzed: {scope}\n')
        analyze_teleoperation(rows)
        analyze_subscales(data)
        print(r_output)
        print(f'\nResponses used: {len(rows)} '
              f'(dropped {len(dropped)} teleoperator)')
        for cond in COND_NAMES:
            scales = data.get(cond, {})
            n = max((len(v) for v in scales.values()), default=0)
            print(f'  {cond:<7}: n={n}, subscales={len(scales)}')
        append_extracted_csv(f, header, all_rows)
    print(f'Saved analysis → {results_path}')
    plot(data, output_path=output_path)


def main():
    header, rows = fetch_rows()
    header, rows = enrich_with_wolf(header, rows)
    rows = filter_groups(rows)            # restrict to GROUP_FILTER (if set)
    rows, dropped = drop_teleoperator_rows(rows)

    scope = ', '.join(map(str, GROUP_FILTER)) if GROUP_FILTER else 'all'
    print(f'Groups analyzed: {scope}')
    print(f'Columns: {len(header)}   Responses: {len(rows)} '
          f'(dropped {len(dropped)} teleoperator rows)')

    matched = sum(1 for r in rows if r[-3])  # true_wolf filled in
    print(f'Wolf join: {matched}/{len(rows)} responses matched (group, game)')
    if matched < len(rows):
        unmatched = sorted({(r[COL_GROUP], r[COL_GAME_ID])
                            for r in rows if not r[-3]})
        print(f'  Unmatched (group, game) pairs: {unmatched}')

    print('=' * 70)
    print('HEADER:')
    for i, col in enumerate(header):
        flat = col.replace('\n', ' ').strip()
        print(f'  [{i}] {flat}')

    print('=' * 70)
    print('RESPONSES:')
    for r_idx, row in enumerate(rows):
        print(f'\n--- Response {r_idx + 1} ---')
        for i, col in enumerate(header):
            value = row[i] if i < len(row) else ''
            label = col.replace('\n', ' ').strip()
            print(f'  {label}: {value}')


if __name__ == '__main__':
    plot_results(output_path='post_survey.pdf')
