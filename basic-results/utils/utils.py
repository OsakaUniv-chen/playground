"""Shared utilities for the players post-survey analysis: the boxplot figure,
generic (survey-agnostic) statistics helpers, and small I/O helpers.

The statistics functions are pure numpy/stdlib — exact signed-rank distribution,
no scipy dependency — and know nothing about the survey, so they live here while
post_survey.py keeps the survey-specific config and reporting.
"""
import math
from statistics import NormalDist

import numpy as np
import matplotlib as mpl
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Rectangle, FancyBboxPatch

# Match the font used in plot_crowdsourcing_nature.py: Arial sans-serif with
# editable text in the PDF output.
mpl.rcParams.update({
    "font.family":     "sans-serif",
    "font.sans-serif": ["Arial", "Helvetica", "DejaVu Sans", "sans-serif"],
    "mathtext.fontset": "dejavusans",
    "pdf.fonttype":    42,
    "svg.fonttype":    "none",
})

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
GQS_SCALES = ['Anthropo-\nmorphism', 'Animacy', 'Likeability',
               'Perceived\nIntelligence', 'Perceived\nSafety']
GEQ_SCALES = ['Flow', 'Competence', 'Positive\nAffect',
               'Negative\nAffect', 'Tension']
ALL_SCALES  = GQS_SCALES + GEQ_SCALES  # 10 columns (HRI-CUES not measured)

# Each condition: (name, game label, session).
# Onsite/Video are the practice session (no robot head-motion strategy);
# GQS-J was not collected there. The four robot conditions form the main
# session and are played in counterbalanced order (Games 3-6).
CONDITIONS = [
    ('Onsite', 'Game 1',    'Practice'),
    ('Video',  'Game 2',    'Practice'),
    ('Tele',   'Games 3–6', 'Main'),
    ('PSSP',   'Games 3–6', 'Main'),
    ('DoA',    'Games 3–6', 'Main'),
    ('Random', 'Games 3–6', 'Main'),
]
COND_NAMES = [c[0] for c in CONDITIONS]

GROUP_COLORS = {
    'GQS': "#3A6BA8",
    'GEQ': '#2E8B57',
}
GROUP_BG = {
    'GQS': '#F3F7FD',
    'GEQ': '#F1F9F4',
}
# Full questionnaire names shown on the group brackets (keys match the codes
# used for colors above). The brackets span 5 columns each, so there is room
# to write them out in full rather than as "GQS" / "GEQ".
GROUP_LABELS = {
    'GQS': 'Godspeed Questionnaire Series',
    'GEQ': 'Game Experience Questionnaire',
}
# Lighter fill colors for the boxplot boxes (darker GROUP_COLORS are kept
# for the axis labels and brackets).
BOX_COLORS = {
    'GQS': '#B8CDE8',
    'GEQ': '#A8D4B5',
}
# Background for cells where the scale was not measured (GQS in practice).
NA_BG = '#ECECEC'

COND_COLORS = {
    'Onsite': '#F4A9A8',  # soft red
    'Video':  '#F9E08B',  # warm yellow
    'Tele':   '#A8C8E8',  # blue
    'PSSP':   '#A8D8B8',  # green
    'DoA':    '#F7C8A0',  # orange
    'Random': '#C8B4D8',  # purple
}

# Session sidebar styling (color bar + label spanning the session's rows).
SESSION_STYLE = {
    'Practice': ('#C9C9C9', 'Practice'),
    'Main':     ('#6E6E6E', 'Main Session - Robot Conditions'),
}

GROUPS = [
    ('GQS', 0, 4),
    ('GEQ', 5, 9),
]
SCALE_GROUPS = ['GQS'] * 5 + ['GEQ'] * 5
SCALE_COLORS = [BOX_COLORS[g] for g in SCALE_GROUPS]


# ---------------------------------------------------------------------------
# Plot
# ---------------------------------------------------------------------------
def plot(data, output_path='boxplot_players.pdf'):
    """Render the players sub-scale boxplot figure and save as PDF."""
    n_cols = len(ALL_SCALES)   # 10

    # Grid rows: practice rows, a spacer row, then main rows. The spacer
    # creates the visual break between practice and main sessions.
    n_practice = sum(1 for c in CONDITIONS if c[2] == 'Practice')  # 2
    grid_n_rows = len(CONDITIONS) + 1                              # 7 (incl. spacer)
    spacer_grid_row = n_practice                                  # 2
    # Map each condition (in order) to its grid row, skipping the spacer.
    cond_grid_rows = [r for r in range(grid_n_rows) if r != spacer_grid_row]
    last_grid_row = grid_n_rows - 1
    top_grid_row = 0

    height_ratios = [1.0] * grid_n_rows
    height_ratios[spacer_grid_row] = 0.45

    fig, axes = plt.subplots(
        grid_n_rows, n_cols,
        figsize=(16, 6.4),
        sharex=True,
        gridspec_kw={'wspace': 0.10, 'hspace': 0.0,
                     'height_ratios': height_ratios},
    )
    fig.patch.set_facecolor('white')

    # Hide the spacer row entirely.
    for col_j in range(n_cols):
        axes[spacer_grid_row, col_j].set_visible(False)

    # --- Per-cell boxplots ---
    for (cond, game_label, session), grid_row in zip(CONDITIONS, cond_grid_rows):
        for col_j, scale in enumerate(ALL_SCALES):
            ax  = axes[grid_row, col_j]
            grp = SCALE_GROUPS[col_j]

            measured = scale in data[cond]

            ax.set_facecolor(GROUP_BG[grp] if measured else NA_BG)
            ax.grid(True, axis='x', color='white', linewidth=1.0,
                    linestyle='-', zorder=1)
            ax.set_axisbelow(True)

            if measured:
                bp = ax.boxplot(
                    [data[cond][scale]],
                    positions=[0],
                    vert=False,
                    widths=0.65,
                    patch_artist=True,
                    showfliers=True,
                    medianprops =dict(color='black',  linewidth=3.0, solid_capstyle='butt'),
                    whiskerprops=dict(linewidth=1.5,  color='#444444'),
                    capprops    =dict(linewidth=1.5,  color='#444444'),
                    boxprops    =dict(linewidth=0.8,  edgecolor='#333333'),
                    flierprops  =dict(marker='o', markersize=3.5, alpha=0.45,
                                      markerfacecolor='#555555',
                                      markeredgecolor='none', linestyle='none'),
                )
                bp['boxes'][0].set_facecolor(SCALE_COLORS[col_j])
                bp['boxes'][0].set_alpha(1.0)

                cap_half = 0.65 / 2 * 0.5
                for cap in bp['caps']:
                    cap.set_ydata([-cap_half, cap_half])
            else:
                # Not measured: leave the cell empty with a faint marker.
                ax.text(3.0, 0.0, 'not measured', ha='center', va='center',
                        fontsize=8, color='#AAAAAA', style='italic')

            ax.set_yticks([])
            ax.set_ylim(-0.72, 0.72)
            ax.set_xlim(0.5, 5.5)
            ax.set_xticks([1, 2, 3, 4, 5])
            ax.spines[['top', 'right', 'left', 'bottom']].set_visible(False)

            if grid_row < last_grid_row:
                ax.tick_params(axis='x', labelbottom=False, length=0)
            else:
                ax.set_xticklabels(['1', '2', '3', '4', '5'])
                ax.tick_params(axis='x', labelsize=11, length=3, pad=2,
                               color='#999999', labelcolor='#555555')

            if grid_row == top_grid_row:
                ax.set_title(scale, fontsize=14, pad=6,
                             linespacing=1.25, color='#333333', fontweight='bold')

    # --- Lock layout and render so get_position() is accurate ---
    fig.subplots_adjust(left=0.155, right=0.98, top=0.88,
                        bottom=0.14, wspace=0.10, hspace=0.0)
    fig.canvas.draw()

    # --- Session brackets (far left) + condition sidebar ---
    SESSION_X = 0.006
    SESSION_W = 0.018
    SIDEBAR_X = 0.032
    SIDEBAR_GAP = 0.003

    # Session color bars, one per session, spanning that session's rows.
    sessions = {}
    for cond_spec, grid_row in zip(CONDITIONS, cond_grid_rows):
        sessions.setdefault(cond_spec[2], []).append(grid_row)
    for session, rows in sessions.items():
        color, label = SESSION_STYLE[session]
        y_top = axes[min(rows), 0].get_position().y1
        y_bot = axes[max(rows), 0].get_position().y0
        gap = 0.004
        fig.add_artist(FancyBboxPatch(
            (SESSION_X, y_bot + gap / 2), SESSION_W, (y_top - y_bot) - gap,
            boxstyle='round,pad=0,rounding_size=0.004',
            transform=fig.transFigure,
            facecolor=color, edgecolor='none',
            clip_on=False, zorder=4,
        ))
        fig.text(SESSION_X + SESSION_W / 2, (y_top + y_bot) / 2,
                 label, ha='center', va='center', rotation=90,
                 fontsize=12, fontweight='bold', color='white',
                 transform=fig.transFigure, zorder=5)

    # Condition sidebar: name + game-sequence label.
    for (cond, game_label, session), grid_row in zip(CONDITIONS, cond_grid_rows):
        pos = axes[grid_row, 0].get_position()
        sw  = pos.x0 - SIDEBAR_X - SIDEBAR_GAP
        gap = 0.004
        fig.add_artist(Rectangle(
            (SIDEBAR_X, pos.y0 + gap / 2), sw, pos.height - gap,
            transform=fig.transFigure,
            facecolor=COND_COLORS[cond], edgecolor='none',
            clip_on=False, zorder=4,
        ))
        cx = SIDEBAR_X + sw / 2
        fig.text(cx, pos.y0 + pos.height * 0.60, cond,
                 ha='center', va='center',
                 fontsize=15, fontweight='bold', color='#222222',
                 transform=fig.transFigure, zorder=5)
        fig.text(cx, pos.y0 + pos.height * 0.30, game_label,
                 ha='center', va='center',
                 fontsize=10, color='#555555',
                 transform=fig.transFigure, zorder=5)

    # --- Horizontal separator line between practice and main sessions ---
    practice_rows = sessions['Practice']
    main_rows     = sessions['Main']
    y_pb   = axes[max(practice_rows), 0].get_position().y0   # practice bottom
    y_mt   = axes[min(main_rows),     0].get_position().y1   # main top
    y_div  = (y_pb + y_mt) / 2
    x_left = SESSION_X
    x_right = axes[top_grid_row, -1].get_position().x1
    fig.add_artist(Line2D([x_left, x_right], [y_div, y_div],
                          transform=fig.transFigure,
                          color='#BBBBBB', lw=1.2,
                          clip_on=False, zorder=6))

    # --- White vertical separator line between GQS and GEQ ---
    for sep_col in (5,):
        xL    = axes[top_grid_row, sep_col - 1].get_position().x1
        xR    = axes[top_grid_row, sep_col    ].get_position().x0
        x_sep = (xL + xR) / 2
        y_top = axes[top_grid_row, 0].get_position().y1
        y_bot = axes[last_grid_row, 0].get_position().y0
        fig.add_artist(Line2D([x_sep, x_sep], [y_bot, y_top],
                              transform=fig.transFigure,
                              color='white', lw=3.0,
                              clip_on=False, zorder=3))

    # --- Group bracket + label below score ticks ---
    for grp, c0, c1 in GROUPS:
        color  = GROUP_COLORS[grp]
        pos_l  = axes[last_grid_row, c0].get_position()
        pos_r  = axes[last_grid_row, c1].get_position()
        x0, x1 = pos_l.x0, pos_r.x1
        x_mid  = (x0 + x1) / 2
        y0     = pos_l.y0
        y_tick = y0 - 0.030
        y_line = y0 - 0.048
        y_text = y0 - 0.056

        fig.add_artist(Line2D([x0, x1], [y_line, y_line],
                              transform=fig.transFigure, color=color,
                              lw=1.6, clip_on=False))
        for xp in (x0, x1):
            fig.add_artist(Line2D([xp, xp], [y_tick, y_line],
                                  transform=fig.transFigure, color=color,
                                  lw=1.6, clip_on=False))
        fig.text(x_mid, y_text, GROUP_LABELS.get(grp, grp),
                 ha='center', va='top',
                 fontsize=13, fontweight='bold', color=color,
                 transform=fig.transFigure)

    plt.savefig(output_path, bbox_inches='tight')
    print(f"Saved → {output_path}")
    plt.show()


# ===========================================================================
# Generic statistics (survey-agnostic)
#
# Rank-based within-group tests used by the analysis. Pure numpy/stdlib: the
# exact signed-rank distribution gives exact Wilcoxon p-values and Hodges–
# Lehmann confidence intervals at small n, with a tie/continuity-corrected
# normal fallback. No scipy dependency.
# ===========================================================================
_NORM = NormalDist()


def _norm_sf(x):
    return _NORM.cdf(-x)


def _norm_isf(p):
    return -_NORM.inv_cdf(p)


def _rankdata(a):
    """Ranks with ties averaged (like scipy.stats.rankdata, 'average')."""
    a = np.asarray(a, float)
    order = np.argsort(a, kind='mergesort')
    ranks = np.empty(len(a), float)
    ranks[order] = np.arange(1, len(a) + 1, dtype=float)
    sa = a[order]
    i = 0
    while i < len(a):
        j = i
        while j + 1 < len(a) and sa[j + 1] == sa[i]:
            j += 1
        if j > i:
            ranks[order[i:j + 1]] = (i + 1 + j + 1) / 2.0
        i = j + 1
    return ranks


def _signed_rank_counts(n):
    """counts[s] = number of subsets of {1..n} summing to s (s = 0..n(n+1)/2).
    Summed over s this is 2**n: the exact null distribution of the signed-rank
    statistic W+ (used for exact p-values and Hodges–Lehmann CIs)."""
    M = n * (n + 1) // 2
    counts = np.zeros(M + 1)
    counts[0] = 1.0
    for i in range(1, n + 1):
        prev = counts.copy()
        counts[i:] = prev[i:] + prev[:M + 1 - i]
    return counts


# chi-square survival function via the regularized incomplete gamma function
# (Numerical Recipes gser/gcf), used for the Friedman p-value.
def _gser(a, x):
    if x <= 0:
        return 0.0
    gln = math.lgamma(a)
    ap, s, delta = a, 1.0 / a, 1.0 / a
    for _ in range(1000):
        ap += 1.0
        delta *= x / ap
        s += delta
        if abs(delta) < abs(s) * 1e-14:
            break
    return s * math.exp(-x + a * math.log(x) - gln)


def _gcf(a, x):
    gln = math.lgamma(a)
    tiny = 1e-300
    b, c, d = x + 1.0 - a, 1.0 / tiny, 1.0 / (x + 1.0 - a)
    h = d
    for i in range(1, 1000):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        if abs(d) < tiny:
            d = tiny
        c = b + an / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-14:
            break
    return math.exp(-x + a * math.log(x) - gln) * h


def _chi2_sf(x, k):
    """P(chi-square with k df > x)."""
    if x <= 0:
        return 1.0
    a, hx = k / 2.0, x / 2.0
    return 1.0 - _gser(a, hx) if hx < a + 1.0 else _gcf(a, hx)


def wilcoxon_paired(a, b):
    """Two-sided Wilcoxon signed-rank on paired arrays a, b. Returns dict with
    n (non-zero diffs), V (sum of positive ranks), r (matched-pairs
    rank-biserial; +ve ⇒ a > b), p, method — or None if no non-zero diffs.
    Uses the exact distribution when there are no ties in |d| and n ≤ 30,
    else a tie/continuity-corrected normal approximation."""
    d = np.asarray(a, float) - np.asarray(b, float)
    d = d[d != 0]
    n = len(d)
    if n == 0:
        return None
    absd = np.abs(d)
    ranks = _rankdata(absd)
    t_plus = float(ranks[d > 0].sum())
    t_minus = float(ranks[d < 0].sum())
    r_rb = (t_plus - t_minus) / (n * (n + 1) / 2.0)
    if len(np.unique(absd)) == n and n <= 30:
        counts = _signed_rank_counts(n)
        total = counts.sum()
        t = int(round(t_plus))
        p = min(1.0, 2.0 * min(counts[:t + 1].sum(), counts[t:].sum()) / total)
        method = 'exact'
    else:
        mu = n * (n + 1) / 4.0
        _, tc = np.unique(absd, return_counts=True)
        var = n * (n + 1) * (2 * n + 1) / 24.0 - np.sum(tc ** 3 - tc) / 48.0
        if var <= 0:
            return dict(n=n, V=t_plus, r=r_rb, p=1.0, method='degenerate')
        z = t_plus - mu
        z = (z - math.copysign(0.5, z)) / math.sqrt(var)  # continuity corr.
        p = min(1.0, 2.0 * _norm_sf(abs(z)))
        method = 'normal'
    return dict(n=n, V=t_plus, r=r_rb, p=p, method=method)


def hodges_lehmann(a, b, alpha=0.05):
    """Hodges–Lehmann median of paired differences (a − b) plus a (1−alpha) CI
    from the signed-rank distribution (order statistics of the Walsh averages).
    ci_ok is False when n is too small to bound the interval at this alpha."""
    d = np.asarray(a, float) - np.asarray(b, float)
    n = len(d)
    if n == 0:
        return None
    walsh = np.sort(np.array([(d[i] + d[j]) / 2.0
                              for i in range(n) for j in range(i, n)]))
    M = len(walsh)
    hl = float(np.median(walsh))
    if n <= 30:
        cum = np.cumsum(_signed_rank_counts(n))
        target = alpha / 2.0 * cum[-1]
        c = -1
        for k in range(M + 1):
            if cum[k] <= target:
                c = k
            else:
                break
    else:
        mu = n * (n + 1) / 4.0
        sd = math.sqrt(n * (n + 1) * (2 * n + 1) / 24.0)
        c = int(math.floor(mu - _norm_isf(alpha / 2.0) * sd))
    if c < 0:
        return dict(hl=hl, lo=float(walsh[0]), hi=float(walsh[-1]),
                    n=n, ci_ok=False)
    return dict(hl=hl, lo=float(walsh[c]), hi=float(walsh[M - 1 - c]),
                n=n, ci_ok=True)


def friedman_test(group_data, scale, modes):
    """Tie-corrected Friedman across `modes` for one scale, on the groups
    present in EVERY mode (complete blocks). Returns a dict with chi2/df/p/W/n,
    or {'insufficient': True, ...} when there are too few complete blocks."""
    md = group_data.get(scale)
    if not md:
        return None
    sets = [set(md.get(m, {})) for m in modes]
    if any(len(s) == 0 for s in sets):
        return dict(insufficient=True, n=0, k=len(modes))
    blocks = sorted(set.intersection(*sets), key=lambda g: (len(g), g))
    n, k = len(blocks), len(modes)
    if n < 3 or k < 2:
        return dict(insufficient=True, n=n, k=k)
    mat = np.array([[md[m][g] for m in modes] for g in blocks])
    ranks = np.vstack([_rankdata(row) for row in mat])
    Rj = ranks.sum(axis=0)
    chi2 = 12.0 / (n * k * (k + 1)) * np.sum(Rj ** 2) - 3.0 * n * (k + 1)
    ties = 0.0
    for row in mat:
        _, tc = np.unique(row, return_counts=True)
        ties += np.sum(tc ** 3 - tc)
    correction = 1.0 - ties / (n * (k ** 3 - k))
    if correction > 0:
        chi2 /= correction
    df = k - 1
    return dict(chi2=float(chi2), df=df, p=_chi2_sf(chi2, df),
                W=float(chi2 / (n * (k - 1))), n=n, k=k)


def _holm(pvals):
    m = len(pvals)
    order = sorted(range(m), key=lambda i: pvals[i])
    adj, running = [0.0] * m, 0.0
    for rank, i in enumerate(order):
        running = max(running, (m - rank) * pvals[i])
        adj[i] = min(1.0, running)
    return adj


def holm_adjust(pvals):
    """Holm-adjust a list that may contain None for skipped contrasts."""
    idx = [i for i, p in enumerate(pvals) if p is not None]
    out = [None] * len(pvals)
    if idx:
        for i, v in zip(idx, _holm([pvals[i] for i in idx])):
            out[i] = v
    return out


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------
class Tee:
    """Write to several streams at once (used to mirror analysis output to a
    file while still printing it to the console)."""

    def __init__(self, *streams):
        self.streams = streams

    def write(self, data):
        for s in self.streams:
            s.write(data)

    def flush(self):
        for s in self.streams:
            s.flush()
