# Paper 6 — Project Instructions

This repo is the workspace for **Paper 6**, the capstone paper of Zhichao Chen's PhD (Osaka University, final year). Paper 6 unifies two prior research fields into one real-world HRI study and serves as the **thesis backbone**.

Repo: `OsakaUniv-chen/paper6` (HTTPS remote; no SSH keys on this machine).

## The PhD in one view
The work spans **two fields**; Paper 6 is the bridge.
- **Field B — Policy learning (decide/act):** Papers 1–2 — IRL→RL android receptionist, field-deployed in a real shopping mall.
- **Field A — Anticipatory perception (perceive/attend):** Papers 3–5 — PSSP predicts future sound sources to drive anticipatory robot head motion in multi-party HRI.

**Paper 6 thesis:** one autonomous social robot that *perceives anticipatorily* (Field A) and *acts via a learned policy* (Field B), validated in a **real-world field run**. **No new algorithm — the contribution is integration.**

## Repo map
- `thesis-prospectus.md` — dissertation direction: title, keywords, key problem, structure + each paper's role.
- `paper6-overview.md` — **start here.** Control document: PhD central line, narrative arc, Paper 6 positioning, open questions.
- `publications/publications-summary.md` — thin index: two-field framing + one-line-per-paper table.
- `publications/paperN-*.md` — per-paper file (background → objective → task → method → evaluation → result → conclusion → relevance to Paper 6).
- `publications/` — source PDFs for Papers 1–4 (read-only references).
- `integration/` (planned) — unifying argument, narrative, figures.
- `field-run/` (planned) — real-world scenario design, protocol, logistics.
- `writing/` (planned) — manuscript drafts.

## Self-contained
All prior-work information Paper 6 needs is captured **inside this repo** (`publications/`). Do **not** depend on external sibling folders; if something from prior work is missing, distill it into a doc under `publications/` rather than referencing an outside path.

## Working norms
- Paper 6 is **integration, not invention** — connect existing results; do not invent methods or fabricate data.
- Keep planning docs **compact**: core info only, no experiment minutiae in overview files.
- Treat unknowns explicitly as `TODO`; do not fill them with assumptions.
- Source PDFs are read-only; do not edit them.
- Commit/push only when asked. Remote uses HTTPS; if a large push fails with HTTP 400, `http.postBuffer` and `http.version HTTP/1.1` are already set locally.
