"""Fetch and analyze the pre-survey responses from Google Sheets.

The sheet is shared via link, so it can be read without credentials through
Google's CSV export endpoint. No auth / gspread / pandas required.

Survey logic note: Q4 (played before) and Q5 (like) are only answered by
participants who answered "はい" to Q3 (know the game). Those subgroups are
used as the denominators for Q4/Q5.
"""
import csv
import io
import statistics
import sys
from collections import Counter, defaultdict
from contextlib import redirect_stdout

import requests

# Spreadsheet shared at:
# https://docs.google.com/spreadsheets/d/1K0g4_wznDoP92iV84jE0lLPNkqxhlDix1o9ebAYt8uI/edit
SHEET_ID = '1K0g4_wznDoP92iV84jE0lLPNkqxhlDix1o9ebAYt8uI'
CSV_URL = (
    f'https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv'
)

# Fixed column layout of the survey (see HEADER printed by `main(raw=True)`).
COL_TIMESTAMP = 0
COL_GROUP = 1
COL_PID = 2
COL_GENDER = 3
COL_AGE = 4
COL_KNOW = 5      # Q3: 対話ゲーム「ワードウルフ」を知っていますか？
COL_PLAYED = 6    # Q4: 遊んだことがありますか？
COL_LIKE = 7      # Q5: 好きですか？

YES = 'はい'
# Q5 ordinal options, best → worst (for consistent ordering / scoring).
LIKE_OPTIONS = ['とても好き', '好き', 'どちらとも言えない',
                'あまり好きではない', '好きではない']
# 5 = most positive ... 1 = most negative.
LIKE_SCORE = {opt: 5 - i for i, opt in enumerate(LIKE_OPTIONS)}


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


def cell(row, idx):
    return row[idx].strip() if idx < len(row) else ''


def age_band(age):
    """Return '20s' / '30s' / '40s' / ... for an integer age, or None."""
    try:
        a = int(age)
    except (ValueError, TypeError):
        return None
    return f'{(a // 10) * 10}s'


def pct(n, total):
    return f'{(100 * n / total):.1f}%' if total else 'n/a'


def analyze(header, rows):
    n = len(rows)
    print('=' * 70)
    print('PRE-SURVEY ANALYSIS')
    print('=' * 70)

    # 1. Total participants ------------------------------------------------
    print(f'\n1. Total participants: {n}')

    # 2. Total groups + group sizes ---------------------------------------
    groups = defaultdict(list)
    for r in rows:
        groups[cell(r, COL_GROUP)].append(r)
    group_ids = sorted(groups, key=lambda g: (len(g), g))
    print(f'\n2. Total groups: {len(groups)}')
    size_dist = Counter(len(v) for v in groups.values())
    for size, cnt in sorted(size_dist.items()):
        print(f'   - {cnt} group(s) of size {size}')

    # 3. Gender ratio ------------------------------------------------------
    gender = Counter(cell(r, COL_GENDER) for r in rows)
    print('\n3. Gender:')
    for g, c in gender.most_common():
        print(f'   - {g or "(blank)"}: {c} ({pct(c, n)})')
    male, female = gender.get('男性', 0), gender.get('女性', 0)
    if female:
        print(f'   - M:F ratio = {male / female:.2f} : 1')

    # 4. Age bands ---------------------------------------------------------
    bands = Counter(age_band(cell(r, COL_AGE)) for r in rows)
    print('\n4. Age bands (participant counts):')
    for band in sorted(b for b in bands if b):
        print(f'   - {band}: {bands[band]} ({pct(bands[band], n)})')
    if bands.get(None):
        print(f'   - unparseable age: {bands[None]}')

    # 5. Age range / mean / SD --------------------------------------------
    ages = []
    for r in rows:
        try:
            ages.append(int(cell(r, COL_AGE)))
        except ValueError:
            pass
    print('\n5. Age:')
    if ages:
        mean = statistics.mean(ages)
        sd = statistics.stdev(ages) if len(ages) > 1 else 0.0
        print(f'   - range: {min(ages)}–{max(ages)}')
        print(f'   - mean ± SD: {mean:.1f} ± {sd:.1f}   (median {statistics.median(ages)})')
    else:
        print('   - no valid ages')

    # 6. Familiarity (Q3 know the game) -----------------------------------
    know_yes = sum(1 for r in rows if cell(r, COL_KNOW) == YES)
    print('\n6. Know the game (Q3 = はい):')
    print(f'   - {know_yes}/{n} ({pct(know_yes, n)})')
    all_know = sum(
        1 for v in groups.values()
        if v and all(cell(r, COL_KNOW) == YES for r in v)
    )
    print(f'   - groups where ALL members know it: '
          f'{all_know}/{len(groups)} ({pct(all_know, len(groups))})')

    # 7. Played before (Q4) -- among those who know the game --------------
    knowers = [r for r in rows if cell(r, COL_KNOW) == YES]
    played_yes = sum(1 for r in knowers if cell(r, COL_PLAYED) == YES)
    print('\n7. Played before (Q4 = はい), among knowers:')
    print(f'   - {played_yes}/{len(knowers)} ({pct(played_yes, len(knowers))})')

    # 8. Liking (Q5) -- among those who know the game ---------------------
    like_counts = Counter(cell(r, COL_LIKE) for r in knowers
                          if cell(r, COL_LIKE))
    n_like = sum(like_counts.values())
    print('\n8. Liking (Q5), among knowers:')
    for opt in LIKE_OPTIONS:
        c = like_counts.get(opt, 0)
        print(f'   - {opt}: {c} ({pct(c, n_like)})')
    other = {k: v for k, v in like_counts.items() if k not in LIKE_OPTIONS}
    for k, v in other.items():
        print(f'   - {k} (unexpected): {v}')
    if n_like:
        scores = [LIKE_SCORE[cell(r, COL_LIKE)] for r in knowers
                  if cell(r, COL_LIKE) in LIKE_SCORE]
        positive = like_counts.get('とても好き', 0) + like_counts.get('好き', 0)
        print(f'   - positive (好き+とても好き): {positive} ({pct(positive, n_like)})')
        print(f'   - median: {statistics.median(scores)}  '
              f'(scored 5=とても好き … 1=好きではない)')
        print(f'   - mean ± SD: {statistics.mean(scores):.2f} ± '
              f'{statistics.stdev(scores) if len(scores) > 1 else 0:.2f} '
              '(ordinal — median/distribution usually preferred)')

    # Data-integrity checks -----------------------------------------------
    print('\n' + '-' * 70)
    print('DATA-QUALITY CHECKS')
    pid_per_group = {g: [cell(r, COL_PID) for r in v]
                     for g, v in groups.items()}
    dup = {g: pids for g, pids in pid_per_group.items()
           if len(pids) != len(set(pids))}
    if dup:
        print('   ! duplicate participant IDs within a group:')
        for g, pids in dup.items():
            print(f'       group {g}: {pids}')
    else:
        print('   - no duplicate participant IDs within groups')
    incomplete = [(cell(r, COL_GROUP), cell(r, COL_PID))
                  for r in rows
                  if not cell(r, COL_GENDER) or not cell(r, COL_AGE)]
    if incomplete:
        print(f'   ! responses missing gender/age: {incomplete}')
    else:
        print('   - all responses have gender + age')


class _Tee:
    """Write to several streams at once (mirror output to a file + console)."""

    def __init__(self, *streams):
        self.streams = streams

    def write(self, data):
        for s in self.streams:
            s.write(data)

    def flush(self):
        for s in self.streams:
            s.flush()


def append_extracted_csv(f, header, rows):
    """Dump the extracted survey data as CSV to the open results file.

    Written directly to `f` (not via stdout) so the full raw data used for this
    analysis is preserved alongside it — a lossless snapshot in case the source
    sheet later changes or is deleted. Cells keep their original text (csv quotes
    any embedded commas/newlines); only the header names are flattened.
    """
    f.write('\n' + '=' * 70 + '\n')
    f.write('EXTRACTED CSV (survey data — kept for safekeeping)\n')
    f.write('=' * 70 + '\n')
    writer = csv.writer(f, lineterminator='\n')
    writer.writerow([c.replace('\n', ' ').strip() for c in header])
    writer.writerows(rows)


def main(raw=False, results_path='pre_survey_results.txt'):
    header, rows = fetch_rows()
    with open(results_path, 'w', encoding='utf-8') as f, \
            redirect_stdout(_Tee(sys.stdout, f)):
        if raw:
            print(f'Columns: {len(header)}   Responses: {len(rows)}')
            for i, col in enumerate(header):
                print(f'  [{i}] {col.replace(chr(10), " ").strip()}')
        analyze(header, rows)
        append_extracted_csv(f, header, rows)
    print(f'Saved analysis → {results_path}')


if __name__ == '__main__':
    main(raw='--raw' in sys.argv)
