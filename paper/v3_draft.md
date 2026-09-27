# Attention Is All You Need Until You Need Retention (v3 draft)

**Working subtitle:** Social learning strategies for persistent memory, in simulation and in a deployed assistant

*Draft started 2026-09-27. Status of every number is marked: **[sim]** simulation in this repository, **[dep]** measured on the deployed assistant, **[pending]** not yet measured. Nothing marked [pending] may be cited.*

---

## 0. What v3 changes relative to v2

v2 derived a memory lifecycle from social learning strategies (SLS) and checked it in a mechanism-level simulation. v3 keeps that lifecycle and adds a second body of evidence: the same lifecycle running as the write path of a deployed, single-user personal assistant (Hacı, on OpenClaw), measured on its real conversation stream.

The two bodies of evidence disagree in instructive ways, and the disagreement is the paper's new contribution:

1. **Which operators transfer is set by two measurable properties of a deployment**: the size of the source population, and the number of observations about one situation that fall inside a recency window. Neither is a property of the mechanism.
2. **The encoding gate is fragile to the scale of its surprise signal.** It admitted every observation in simulation (1439/1439) and in deployment (109/109), through different proximate causes.
3. **Similarity is a calibration-coupled quantity.** Every threshold that compares two memories — surprise reference, cluster link, belief-cluster threshold, duplicate threshold — is defined on the scale of one similarity measure, and changing the measure silently invalidates all of them together. The simulation cannot show this, because its similarity is cosine between synthetic unit vectors.
4. **Retrieval is denser consolidation evidence than repetition.** A conformist quorum over distinct days starves when one user produces about one memory a day. Counting the days on which a memory was *needed* — retrieved into the prompt — gives the quorum a far denser signal. This is the deployment analogue of the testing effect. **[pending: effect size]**

v3 also corrects two v2 claims that the code did not support (§2.1, §2.2).

---

## 1. Thesis

A retention lifecycle derived from social learning strategies is a family of operators — surprise-gated encoding, payoff and prestige bias, conformist consolidation by quorum, a relative condition against rivals, reconsolidation by use, decay. In a deployed system, each operator has a substrate requirement: prestige bias needs a population of sources with track records; a quorum over distinct recent sources needs enough observations per window to reach it; payoff bias needs an outcome channel. Whether an operator does useful work in a deployment is decided by whether its substrate exists there, and that can be measured before the operator is trusted.

---

## 2. Simulation: what the v2 experiment did and did not show [sim]

All results use `sls_retention_sim.py` with 20 seeds. Every policy sees the same event stream under a seed, so differences are reported as **paired** 95% intervals (v2 reported unpaired intervals; pairing makes several ablations significant and shows three of them to be exact no-ops, ±0.0000).

### 2.1 Correction: payoff and prestige do not gate encoding

v2 states that encoding is gated by surprise, by observed outcomes and by source credibility. In the code, the tally branch returns for every observation with surprise at or below the reference, so the encoding gate only ever sees observations already known to be surprising, and the surprise term alone decides them. Measured: of 1439 observations reaching the gate, 1439 pass, and the three-term gate agrees with a bare surprise test on 100% of them. Payoff and prestige act at **consolidation** (the payoff majority test and the credibility weighting of support).

The inert gate is load-bearing. Running the gate *before* the tally raises attack success from 0.059 to 0.118 under S2 and from 0.066 to 0.104 under S3, because the tally is what feeds the relative condition its evidence (tallies fall from 1657 to 3). Bounding the surprise term changes nothing (0 blocks): the problem is the gate's position, not its functional form.

### 2.2 Correction: the quorum was measured outside the regime the theory describes

The quorum is an absolute credibility count, but what it can reach is set by

    obs_per_window = (1 - p_interact) * WINDOW / n_sit

which is 5.97 at the v2 settings, while Proposition 2 is checked at N = 20. At the v2 density attack success is flat in the quorum and the low attack success at high quorum is bought by forgetting (at k = 6 memory falls from 150 entries to 48 and accuracy to 0.654). At 20 observations per window, with the same relative quorum, attack success falls monotonically and k = 2 holds drifted-situation accuracy at **1.000** with attack success **0.010** (S2) and **0.000** (S3). The mechanism behaves as the propositions predict once the experiment reaches their regime. (Figure 5.)

### 2.3 Other simulation results carried into v3

| Finding | Evidence |
|:--|:--|
| A track-record requirement gives sybil immunity and helps reputation farming | `quorum_min_history=2`: S2 attack success 0.059 → **0.000**; S3 0.066 → **0.120**; drifted accuracy −0.0911 ± 0.0230. Honest newcomers are one-shot too, so 30% of honest evidence is discarded while five farmed identities keep full weight. |
| Farm support saturates; sybil support does not | A farm of *n* identities cannot exceed *n·χ* support; any quorum above that blocks it outright. At k = 6 (> 5 × 0.767) farmed attack success is 0.009. |
| A rival-margin quorum beats the conflict-scaled one | `k_eff = quorum + γ·rival_support`: accuracy +0.0021 ± 0.0010 paired, drifted 0.978, against the conflict-scaled rule's −0.0041 ± 0.0019 and 0.910. |
| The headline quorum should be k = 1 | Dominates k = 2 on every column: 0.999 / 0.989 / 0.059 / 0.042 against 0.996 / 0.958 / 0.059 / 0.066. |
| v2 ablations were partly vacuous | `acc_unknown` = 1.0000 for 15 of 21 policies; eviction never fires at cap 150. At cap 60, 17 of 18 ablations separate on `acc_unknown`, and FIFO eviction becomes the largest effect (drifted +0.3275 ± 0.0508, attack success +0.0963 ± 0.0264). |
| Three code smells carry no measurable attack | Ungated confirmations, retroactive credibility and the unbounded gate term are all real; none changes attack success significantly at these settings (e.g. frozen credibility: +0.0025 ± 0.0041). |

---

## 3. Deployment: the lifecycle as a live write path [dep]

### 3.1 System

A single-user personal assistant. Every conversation turn is written to an episodic store; a write gate stamps each memory **core** (protected from decay for years) or **buffer** (normal importance-banded decay). Memories are never deleted by the gate; the tier only decides decay protection.

Mapping of the lifecycle:

| Operator | Deployment substrate |
|:--|:--|
| Surprise | 1 − max similarity to core memories |
| Prestige bias | Beta pseudo-count ledger per source; sources are *roles* (user, assistant, tool, web, document), not identities |
| Payoff bias | Three outcome channels the system already recorded: resolution of its own predictions, the user's tone feedback, logged mistakes |
| Quorum | Distinct **days** on which a topic recurs (one user, so days stand in for sources) |
| Relative condition | A cluster must out-support similar rival clusters |
| Reconsolidation | Retrieval into the prompt (§4), outcome channels |
| Decay | Importance bands; core protected |

Corpus at first measurement: 109 memories over 116 days, **0.94 memories per day**.

### 3.2 The gate admitted everything: similarity on the wrong scale

With token and character Jaccard as the similarity, the median similarity of a Turkish conversational turn to the core was 0.088. Surprise was confined to 0.733–1.000 against a reference of 0.55: **0 of 109** memories could fall below it. Live counters: 109 passes, 0 confirmations, 0 rejections. The confirmation and rejection paths were unreachable.

The same stream measured with sentence-embedding cosine (all-MiniLM-L6-v2) spreads surprise over 0.136–1.000 (median 0.385). With recalibrated thresholds, confirmations rise from 0 to 56 and rejections from 0 to 6 while the number of core memories stays at 37 against 36 — **the gate starts deciding without changing how much it keeps**.

Same symptom as §2.1 through a different mechanism: in simulation the tally removes everything the gate could reject; in deployment the similarity scale prevents anything from being tallied or rejected. An encoding gate whose inputs never leave one region of their range is not a gate.

### 3.3 Similarity is calibration-coupled

Switching the measure invalidated every threshold defined on it, together:

- the surprise reference (0.55 on Jaccard → 0.40 on cosine);
- the cluster link (0.10 on Jaccard collapses every memory into one cluster on cosine; 0.72 is the equivalent);
- the individual-importance threshold, which had to move once clusters became real (a cluster-level threshold made one important memory promote its whole neighbourhood: core 36 → 80);
- the belief-cluster threshold (§5): 0.45 was the *median* pairwise cosine of this corpus, so most memories fell into one cluster.

The embedding model compresses Turkish into a narrow cone: pairwise cosine median 0.445, 95th percentile 0.680. Every cosine threshold in the system has to be read against that distribution, not against the nominal [−1, 1] range. Centring the distribution nearly doubles separation (0.292 → 0.512 on q99 − median) but introduces a corpus-dependent constant; it is not deployed. **[pending: whether centring improves cluster quality]**

An engineering consequence worth stating in the paper: a persisted gate must record the *version* of its calibration. The deployed gate restored stored thresholds whenever the similarity backend matched, so a recalibration would have been silently ignored on load.

### 3.4 Live traffic under the gate: 16–27 September 2026

70 new memories over 8 distinct days (7 old memories decayed in the period).

| Measure | Value |
|:--|:--|
| Decisions | 38 confirm, 30 pass, 2 reject |
| Surprise median | 0.381 (backfill: 0.385) |
| Importance, max / p90 | 0.75 / 0.56 |
| Core | 9 of 70 |
| Core reached by confirming an existing core cluster | **8 of 9** |
| Clusters spanning ≥ 2 days | 14 of 136 |
| … of which anchored on a content-free template | 2 ("Hazırlasana" — *prepare it* — on 5 days; the system's *no-reply* sentinel on 3) |
| Consolidated anchors lost to decay | 5 of 63 |

Four failure modes, each a lifecycle operator without its substrate or with a wrong boundary:

1. **The individual-importance shortcut was dead.** Its threshold (0.80) had been set on seeded memories with hand-assigned importance; no live memory reached it. Retention was decided by the quorum alone.
2. **The quorum counted templates, not content.** Recurrence over days is faked by utterances with no content. A floor of three content words removes the templates without excluding short durable facts: a stated drink preference or the football team the user supports each take exactly three content words in Turkish, and a floor of four would have excluded both.
3. **Confirmations duplicated into core.** A memory similar to a core cluster inherited core itself — the tally/encode distinction of the lifecycle was not implemented. Eight of nine live core memories entered this way, including two copies of the no-reply sentinel.
4. **Consolidation marked the wrong memory.** Quorum promotion recorded the cluster's anchor in the gate's own list but left the anchor memory in buffer, where decay deleted five of them. The topic stayed "consolidated" in the gate with nothing retained.

### 3.5 Corrected lifecycle and counterfactual replay

Corrections deployed on 27 September 2026:

- A **confirmation reinforces** the anchor of the cluster it confirms and does not itself become core — unless it is a *new fact inside a known topic*: surprise above a restatement threshold (0.15) and individually important.
- **Quorum consolidates the anchor**, and if the anchor's memory has decayed, representation passes to the newest member.
- A memory needs **three content words** to add a day to a quorum; a content-free anchor is replaced by the first contentful member.
- The importance threshold is set on the **live** distribution (0.60, about the top 13%).
- System sentinels are never stored.
- The trust ledger is **fed** by the three outcome channels. Before, nothing updated it; it sat at its priors. The assistant's credibility is now 0.383 (1 of 30 predictions confirmed, 26 logged mistakes, +4.7 tone valence), which halves the weight of assistant-sourced memories.

Replaying the full stream (182 memories, chronological, empty gate) through the old and corrected gates:

| | Old gate | Corrected gate |
|:--|--:|--:|
| Live memories made core | 12 of 70 | 4 of 70 |
| … content | "*Ee hacı*" (a filler greeting), 2× no-reply sentinel, "*doesn't matter, pick one, go on*", development chatter | a family fact, two model-context decisions, a context-window explanation |
| Sentinels stored | 3 | 0 |
| Confirmations reinforcing an anchor instead of duplicating | 0 | 25 |
| Low-content memories refused a quorum day | — | 22 |

**[pending: the corrected gate on live traffic after 27 September — `gate_report.py`]**

---

## 4. Retrieval as consolidation evidence [dep, pending]

The quorum asks *did the user say this on two different days?* With one user at about one memory a day, the answer is rarely yes (14 of 136 clusters in eleven days, two of them templates). The deployment offers a denser signal: every turn retrieves memories into the prompt. A memory retrieved on two different days was *needed* twice. The corrected system logs each retrieval that is actually injected (post-reply retrievals are not counted) and, in a nightly pass, promotes memories retrieved on at least two distinct days to core; content-free memories are excluded and nothing is demoted.

This is the testing effect as a consolidation rule: retrieval, not re-exposure, is the event that makes a memory durable.

**[pending]** retrieval events per day; distinct-day retrieval counts; promotions by retrieval versus by quorum; overlap between the two. Measure from `cognitive_state/recall_log.json` and `core_reason` in the memory metadata.

---

## 5. Sleep consolidation: a quorum at the semantic level [dep]

The assistant also has a semantic layer that synthesises *beliefs* about the user from clusters of episodes. It had held a single belief since 13 September. Three causes, each a lifecycle boundary:

- **No caller.** The only caller was a dreaming loop that had stopped in May.
- **Starvation by construction.** Clustering stopped after the six most central clusters, and synthesis skipped clusters already processed; once those six were done, no new cluster could ever be considered.
- **Identity by position.** Clusters were keyed by memory index, which decay shifts.

Two further calibration errors surfaced on the first real run:

- Episodes were embedded with their timestamp-and-role header, which every episode shares; together with a threshold at the corpus median, this put 82 episodes spanning 37 days into one cluster.
- Paraphrased beliefs were stored twice. Paraphrase pairs sit at cosine 0.922, the nearest distinct pair at 0.864, and every statement begins with the same subject, so the baseline is high (median 0.79). A 0.90 threshold separates them.

The corrected layer runs nightly and requires a cluster's episodes to span at least **two distinct days**, which is the paper's quorum applied at the semantic level: one session's remark is an anecdote, not a belief. It runs on a local 4B model only, so the nightly maintenance stays free of paid tokens.

First run: 5 candidate clusters, 4 multi-day; 3 processed in about 25 seconds; 5 new beliefs, 1 merged into an existing one; 6 in total. One reaches the confidence and evidence thresholds for use in the prompt ("*asks the assistant for summaries and write-ups on complex projects*", confidence 0.646, 3 episodes).

**[pending]** belief growth over nights; the fraction that reaches the prompt threshold; manual audit of belief accuracy.

---

## 6. Limitations

- **One user, one deployment, small N.** 182 memories. Every deployment threshold in §3–§5 was set on this corpus. Traffic after 27 September is the natural held-out set, and every [pending] item is to be measured on it, not on the calibration data.
- **Sources are roles, not identities.** Prestige bias has a population of two; the simulation's sybil and farming results have no deployment counterpart yet. A multi-agent deployment (the assistant already delegates to sub-agents) would provide one.
- **Outcome channels are sparse and asymmetric.** All three speak about the assistant; nothing in the deployment speaks about the user's reliability, which is appropriate but leaves the user's ledger entry at its prior.
- **The retrieval rule has a feedback risk.** A memory retrieved because it is core is more likely to be retrieved again. Promotion only flows from buffer to core, so the loop cannot demote, but it can entrench. **[pending: check whether promoted memories were retrieved for relevance or for status]**

---

## 7. Pending measurements

| Item | How |
|:--|:--|
| Corrected gate on live traffic | `python3 haci_cognitive/gate_report.py` (boundary: 27 Sep upgrade) |
| Old gate period for comparison | `python3 haci_cognitive/gate_report.py --since cognitive_state/gate_baseline_20260916.json` |
| Retrieval consolidation | `cognitive_state/recall_log.json`; `core_reason == "recall"` in `retention_state.json` |
| Belief growth | `cognitive_state/beliefs.json`; nightly log `memory/nightly_maintenance.log`, section *uyku konsolidasyonu* |
| Simulation with the corrected gate position and k = 1 | `python3 sls_retention_sim.py --seeds 20` on this branch |

Suggested minimum before submission: 30 or more post-upgrade memories over at least four distinct days, so that the day-based rules in §3.5, §4 and §5 have had a chance to fire.
