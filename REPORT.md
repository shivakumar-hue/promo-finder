# Promo Finder v2 — comparison, ranked picks, and quality review

## Is the new package better?

**Yes.** The comparison archive has a stronger approach than the first version: it classifies ending types, scores hook and ending separately, considers context and pacing, tests cut boundaries, proposes stitched clips, reduces duplicates, and explains the ranked results.

I adopted that approach and tightened it further. Camera-note gaps now create a review flag and a capped score penalty rather than disqualifying a candidate. Structural failures are filtered before ranking. Stitched segments require a shared speaker (the check does not claim that a character is visible), while generic honorific removal and conservative name-token subset matching replace the show-specific `LADY JULIANA` alias. I also added a best-candidate list for every episode, exact episode/time-scoped quote checks, and a regression suite.

The archive's original regression script did not independently verify its packaged run: it looked for `out_v2/promos.json`, which was absent, and searched the first 60 quote characters anywhere in the scripts. The replacement tests verify quotes against the correct episode and time segment. The tests and full-season run now pass.

## What the upgraded run found

- 50 episodes read; 100 scenes; all scene timelines start at zero and connect end-to-start.
- Estimated total duration: **01:42:42.62** (about 1 hour 43 minutes), from the last scene-end time in each episode. Average episode: **02:03.25**. Shortest: Episode 44 at 01:10.50. Longest: Episode 36 at 03:20.75.
- 10,220 sampled candidates were considered; 10,114 passed structural checks; 30 promos were selected and all 30 passed.
- Selected set: 18 continuous, 8 same-episode stitched, and 4 cross-episode promos. The top 30 cover 26 episodes. Four selected promos carry camera-gap review flags.
- The test suite passed **23 checks**, including 8 known cliffhanger/resolved-ending cases, generic speaker-alias cases, CSV/JSON consistency, and verbatim hook/cliffhanger checks tied to the correct episode and time range.

Runtime is based on screenplay timecodes, not final exported episodes. Scores are heuristics; the ranking also applies a repeat-episode penalty, so displayed scores may not be strictly descending.

## Editorial watch-first order

**This is an editorial order and differs from the machine ranking below.** It favors clear hooks, visible escalation, and an open question. It is a watch order, not footage approval.

1. **Episode 1 — 00:46.80–01:14.60 (27.8s, continuous).** Juliana confronts the people who betrayed her; the exchange builds to “How long?” and cuts before the answer. Strong first watch because it quickly establishes personal stakes and leaves a question open.
2. **Episode 41 — 00:57.80–01:24.50 (26.7s, continuous).** Juliana challenges her captors; the cathedral entrance explodes. Strong visual escalation. Confirm the opening line and blast read clearly without surrounding context.
3. **Episodes 42–43 — Ep42 00:16.60–00:28.80 + Ep43 00:10.50–00:30.00 (31.7s, cross-episode).** An “imposter” accusation and arrest order lead to guards recognizing Alexander and a sealed dossier. Test the episode transition; it should feel like one continuous reversal.
4. **Episode 11 — 00:16.80–00:28.90 + 01:21.30–01:39.20 (30.0s, stitched).** Gerald accuses Juliana's husband of stealing a pocket watch; the second segment pays off the search with a reaction as the watch turns up. Strong frame-up premise. Check that the jump is easy to follow.
5. **Episode 28 — 00:15.20–00:47.40 (32.2s, continuous).** Juliana denies poisoning the Duchess and points to the fever breaking; the Duchess begins “They have been—” before the cut. Clear conflict and an interrupted reveal. Check that the final action makes the interruption legible.

## Machine-ranked top ten

The “score” is the raw heuristic score; selection also applies episode-diversity adjustments.

| Rank | Promo segments | Type | Score | Hook → ending |
|---:|---|---|---:|---|
| 1 | Ep1 00:46.80–01:14.60 | Continuous | 0.792 | Betrayal confrontation → unanswered “How long?” |
| 2 | Ep28 00:15.20–00:47.40 | Continuous | 0.778 | Poisoning denial → interrupted accusation |
| 3 | Ep42 00:16.60–00:28.80 + Ep43 00:10.50–00:30.00 | Cross-episode | 0.754 | “Arrest this imposter!” → sealed dossier / power reversal |
| 4 | Ep36 01:57.00–02:40.00 | Continuous | 0.737 | Truth revealed → wedding deadline in two days |
| 5 | Ep11 00:16.80–00:28.90 + 01:21.30–01:39.20 | Stitched | 0.730 | Pocket-watch theft accusation → watch found |
| 6 | Ep27 00:44.20–01:02.00 + 02:08.50–02:30.50 | Stitched | 0.714 | Husband's identity question → Juliana dragged away |
| 7 | Ep46 01:16.80–01:36.80 | Continuous | 0.701 | Juliana challenges the church → High Priest invalidates the marriage |
| 8 | Ep41 00:57.80–01:24.50 | Continuous | 0.696 | Chained Juliana challenges her captors → explosion |
| 9 | Ep34 00:00.00–00:19.40 + 01:33.80–01:55.20 | Stitched | 0.689 | Witchcraft/poisoning accusation → question about the patients' illnesses |
| 10 | Ep5 00:10.50–00:25.00 + 00:41.50–00:57.80 | Stitched | 0.686 | Juliana asserts she is a wife → her mother's ledger is thrown into the courtyard |

## Finale coverage outside the top 30

None of Episodes 47–50 made the launch shortlist. The new `best_per_episode.csv` and `.json` preserve the strongest structurally valid candidate for every episode, so editors can review the finale rather than losing it at the top-30 cutoff.

| Episode | Best candidate | Type | Score |
|---:|---|---|---:|
| 47 | 01:14.80–01:44.80 | Continuous | 0.585 |
| 48 | 00:20.00–01:01.00 | Continuous | 0.524 |
| 49 | 00:15.50–00:33.20 + 01:31.40–01:43.80 | Stitched | 0.499 |
| 50 | 00:26.50–00:43.00 + 00:59.80–01:20.00 | Stitched | 0.513 |

This is a coverage fallback, not a finale-aware ranking boost. These four still need editor review.

## Where this version can still pick badly

- **A structurally valid candidate can still be weak.** The tool cannot see acting, framing, actual footage, sound, or whether the performance lands in three seconds.
- **Context is scored, not understood.** Episode 36's deadline and Episode 46's marriage challenge may depend on prior plot information. The `standalone` score is only a proxy.
- **A shared speaker does not prove a coherent stitch.** The alias guard removes some unrelated pairs, but an editor must confirm the story connection and transition.
- **Camera notes are imperfect coverage evidence.** Gaps are flags, not proof the footage is missing. Inspect the actual episode.
- **The search is sampled.** It chooses strong cut-ins per four-second bucket and tests representative durations/endings; a rare beat may fall outside the sample.
- **Coverage is still uneven.** 26 of 50 episodes appear in the top 30. Use `best_per_episode.csv` to review the strongest candidate from every episode, especially those missing from the launch shortlist.
- **No learned calibration yet.** Editor accept/reject decisions and performance data from released promos should guide the next scoring revision.
- **Show independence is unproven.** The word lists are generic but were only evaluated on this series; another show and genre are still needed.

## Recommended weekly operating loop

1. Run the parser on every episode in the new batch.
2. Review `episode_coverage.csv` and `data_qa.json` for missing episodes, timing problems, and scene gaps.
3. Watch the machine-ranked candidates and the per-episode fallback for shows missing from the top 30.
4. Accept, reject, or adjust each candidate; save corrected in/out points and one reason.
5. After publishing, compare editor decisions with qualified views and episode starts, then tune the weights with real examples.

## Files

`promo_finder.py` is the reusable tool; `test_promo_finder.py` contains the regression and output-integrity checks. `promos.json` is the source of truth and `promos.csv` is generated by reading it back. `best_per_episode.csv/.json` preserve one candidate for each episode. `episode_coverage.csv/.json` include runtime and shortlist coverage; `data_qa.json` lists source-timing warnings. See `README.md` for run instructions.
