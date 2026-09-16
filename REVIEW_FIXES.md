# Review fixes

Working log for the `review-fixes` branch. Every change is a separate commit and
every mechanism change is a `Policy` flag that defaults to the published
behaviour, so `results/results.json` stays reproducible while each fix is
measured against it. Drop this file before merging.

## Baseline

`results/results.json` reproduces **bit-exactly** on Python 3.14.6 / NumPy 2.4.3,
against the Python 3.14.7 / NumPy 2.5.3 run reported in the README: the largest
difference over every final metric of every policy in every scenario is 0.0000.
The README's caveat about NumPy and BLAS builds is more conservative than the
code needs.

All comparisons below are against that baseline rerun.

## 0. Latent bugs in the analysis helpers

Behaviour-neutral, verified: every final metric identical after the fix.

| Where | Bug | Fix |
|:--|:--|:--|
| `ci95` | t-critical values were keyed by **sample size** in a sparse table and fell back to `1.96` for any size not in it. One NaN seed dropped n from 20 to 19 and silently narrowed the interval. | df-indexed table for df 1..30, Cornish-Fisher expansion beyond. |
| `quorum_study` | Monte-Carlo latency took `argmax` over an all-False row when the quorum was never reached, scoring that run as latency 1. | Censor non-reaching runs at `L`, report `latency_mc_censored`. Measured: 0.0 at every setting, so the bug was latent. |
| `main` | `config.env` stored only the shared defaults while four scenarios ran, so the recorded `rho`, `attacker` and `forge_outcomes` described none of them. | Added `config.envs` with the resolved env per scenario. |
| `make_figures` | Second private copy of `binom_tail`. | Import the simulation's. |
| `make_figures` | Figure 4 error bars clipped to a fixed `0.8` top with no indication (current max is 0.7902, so it was about to bite). | Shared y axis scaled to the data. |

## 1. The encoding gate is inert, and fixing it makes the attack easier

Measured on the published policy: of 1439 observations that reach the gate,
**1439 pass**, and the three-term SLS gate agrees with a bare `surprise > S0`
test on **100%** of them. The tally branch returns for everything with
`surprise <= S0`, so the gate only ever sees observations already known to be
surprising, and `TH_S * (surprise - S0)` alone decides every case. Payoff bias
and prestige bias do nothing at encoding time; they act only at consolidation,
through the payoff majority test and the credibility weighting of support.

Two fixes, both as flags:

- `bounded_surprise` replaces the unbounded excess with `tanh(surprise/S0 - 1)`
  so the three terms are commensurate. Effect: **exactly none**, `gate_block`
  stays 0 and every metric is identical to `sls_full` to 4 decimal places. The
  problem is the gate's *position*, not its functional form.
- `gate_first` runs the gate before the tally short-circuit. It does block
  (1643 observations), and it makes attack success **worse**.

| policy | acc | drift | ASR S2 | ASR S3 |
|:--|--:|--:|--:|--:|
| `sls_full` | 0.990 | 0.958 | 0.059 | 0.066 |
| `sls_gate_first` | 0.985 | 0.966 | 0.118 | 0.104 |
| `sls_gate_fixed` (both) | 0.986 | 0.963 | 0.104 | 0.090 |

Paired against `sls_full`, `sls_gate_first` costs `asr +0.0594 +- 0.0247`. The
counters say why: tallies collapse from 1657 to 3, and `blocked_by_rival` falls
from 20 to 8. The tally is what feeds the relative condition its evidence, so
gating it removes the defence.

**Conclusion: change the paper, not the code.** The revision should say that
surprise gates encoding while payoff and prestige govern consolidation. The
inert gate is load-bearing.

## 2. Confirmations bypass every check, but the bypass is not what the attack uses

The confirmation branch runs before the gate: any source whose outcome is not
negative adds `cred(src)` to a stored entry's strength, with no gate, no quorum
and no rival test. Strength drives both the attention prior and eviction.

A credibility bar on that path (`confirm_gate`) blocks 257 confirmations and
changes **nothing**: `asr -0.0000 +- 0.0081` paired. The bar is self-certified
away. `rep_add` credits the source from its own reported outcome at the top of
`observe`, before the bar is tested, so a single forged success lifts a fresh
identity from 0.50 to 0.667 and past `CHI_MIN = 0.6`.

A track-record requirement cannot be forged in one event. `confirm_min_history=2`
blocks 541 confirmations — and still does not move attack success
(`asr -0.0106 +- 0.0122`, CI includes 0). Honest and adversarial confirmations
are removed in the same proportion.

The hole is real and worth closing as hardening, but it does not carry the
attack at these settings. Reported as such rather than as a headline fix.

## 3. A track record gives sybil immunity and makes farming easier

`quorum_min_history=2` lets a source count toward a quorum only once it has been
observed more than once.

| policy | acc | drift | ASR sybil | ASR farmed |
|:--|--:|--:|--:|--:|
| `sls_full` | 0.990 | 0.958 | 0.059 | 0.066 |
| `sls_quorum_hist2` | 0.981 | 0.867 | **0.000** | **0.120** |

Paired: `asr -0.0588 +- 0.0210`, `acc_drifted -0.0911 +- 0.0230`, both
significant. Sybil injection becomes impossible, because a fresh identity per
injection can never reach a second observation. Reputation farming becomes
*easier*, because the five farmed identities repeat while 30% of honest
observations come from one-shot newcomers and are now discarded.

This is the sharpest result of the review: a distinct-identity quorum is only as
strong as the cost of an identity, and the cheapest way to impose that cost —
demanding a track record — is exactly what a farm already has.

## 4. The quorum works; the published configuration hides it

What a quorum can ever reach is set by how many observations about one situation
fall inside a recency window:

    obs_per_window = (1 - p_interact) * WINDOW / n_sit

It does not depend on `T`. The published settings give **5.97**, while
`quorum_study` checks Proposition 2 at **N = 20**. The simulation never enters
the regime the proposition describes, which is why the sweep read as a flat
attack success with accuracy collapsing above k = 3: at k = 6 the rule needs 8
distinct honest sources inside a window that delivers about 3.4, so the store
starves (`mem` 150 -> 48, `acc_all` 0.654) and the low attack success is bought
by forgetting everything rather than by filtering.

Re-running the same relative quorums at `n_sit = 60`, which gives exactly 20
observations per window, gives the behaviour the propositions predict.

| relative k | 6.0 obs/window: acc / drift / ASR | 20.0 obs/window: acc / drift / ASR |
|:--|:--|:--|
| 0 | 0.989 / 0.945 / 0.053 | 0.991 / 0.940 / 0.021 |
| 1 | 0.993 / 0.989 / 0.059 | 0.995 / 1.000 / 0.029 |
| 2 | 0.990 / 0.958 / 0.059 | **0.997 / 1.000 / 0.010** |
| 3 | 0.972 / 0.850 / 0.073 | 0.979 / 0.906 / 0.002 |
| 4 | 0.912 / 0.641 / 0.038 | 0.853 / 0.528 / 0.002 |

(S2, fresh identities with forged outcomes. Under S3 the dense run reaches
attack success 0.000 at k = 2 while holding drift accuracy at 1.000.)

Attack success now falls monotonically in the quorum instead of staying flat,
and the accuracy cost arrives three steps later. The mechanism does what the
paper claims; the experiment was run where it could not show it. Report the
quorum in units of `obs_per_window`, and run the headline sweep at a density
that reaches the theory's regime.

## 6 and 7. Capacity pressure, which the published cap never applies

`acc_unknown` is exactly 1.0000 for 15 of 21 policies, so the metric carries no
information and most ablations cannot separate on it. The cause is the same in
both cases: the SLS store peaks near 135 against a cap of 150, `evicted` is 0
for every SLS policy, and `sls_fifo_eviction` was therefore identical to
`sls_full` in all four scenarios.

At `cap = 60` the store fills and the eviction rule matters a great deal
(S2, forged outcomes):

| policy | acc | acc_unknown | drift | ASR | evicted | dropped |
|:--|--:|--:|--:|--:|--:|--:|
| `sls_full` (cap 150) | 0.990 | 1.0000 | 0.958 | 0.059 | 0 | 0 |
| `sls_cap60` (strength) | 0.712 | 0.618 | 0.127 | 0.016 | 54 | 193 |
| `sls_cap60_fifo` | 0.693 | 0.506 | 0.454 | 0.112 | 261 | 0 |

Strength eviction under pressure reproduces the trust-gated failure mode: 193
promotions are refused outright because the weakest stored entry is already
stronger, so drift adaptation dies (0.127). FIFO keeps adapting (0.454) and pays
for it in attack success.

A second caveat for Table 3: the three baselines all run saturated at `mem` 150
with heavy eviction (v1 evicts 4656, surprise-gated 1596) while no SLS policy
ever fills. Part of the reported gap is that SLS stores less, not only that it
stores better. The `pressure` experiment re-runs the ablation set at `cap = 60`
so the comparison can be made at equal pressure.

## 5. The runs were paired all along and were reported as independent

Under a given seed every policy sees the same pre-drawn event stream, and
`pool.map` preserves job order, so entry *i* of each policy's list is the same
seed. The summaries nevertheless reported unpaired intervals, which carry the
between-seed variance that the design already removes.

Pairing changes what the ablation table can say. `sls_no_reconsolidation` reads
0.083 +- 0.026 against 0.059 +- 0.021 unpaired — overlapping, inconclusive — and
`asr +0.0244 +- 0.0134` paired, which is significant. It also makes the vacuous
ablations unmistakable: `sls_no_payoff`, `sls_fifo_eviction` and
`sls_bounded_gate` come out at exactly `+-0.0000` on every metric, zero variance,
because they are bit-identical to `sls_full` at these settings.

`paired.diffs[exp][scenario][policy][metric]` in the results file holds the mean
difference and its 95% half-width against `PAIRED_REF`.

## 8. Farming saturates, sybil injection does not

Consolidation counts each source once, so an adversary holding *n* identities of
credibility chi can never push a false template past `n * chi` support, however
long it keeps injecting. Any quorum above that ceiling blocks the farm outright,
at a latency cost that grows only linearly in the ceiling. A sybil adversary with
free identities has no ceiling at all, which is why the two attacks need
different defences — and why the track-record requirement in section 3 helps
against one and hurts against the other.

The simulation already showed the ceiling without naming it: the farm holds
`FARM_IDS = 5` identities that reach credibility about 0.767, so its support
saturates near 3.8, and at k = 6 farmed attack success is 0.009. `farm_study()`
now states the ceiling, the quorum that blocks it and the honest latency that
quorum costs.

## 9. Scale the quorum by rival support, not by log-odds

The published conflict scaling raises the quorum by the log-odds gap between the
demonstrated template and the model's current top choice. Replacing it with a
margin over the best rival — `k_eff = quorum + gamma * rival_support` — asks for
the thing conformist transmission is actually about: more support than the
competition, by a margin, rather than a bare majority.

| policy | acc | drift | ASR S2 | paired acc vs `sls_full` |
|:--|--:|--:|--:|:--|
| `sls_full` | 0.996 | 0.958 | 0.059 | — |
| `sls_adaptive_quorum` (published) | 0.992 | 0.910 | 0.071 | **-0.0041 +- 0.0019** |
| `sls_margin_quorum` | **0.998** | **0.978** | 0.076 | **+0.0021 +- 0.0010** |

The margin rule is significantly better than `sls_full` on accuracy and reaches
0.978 on drifted situations against 0.958, with no significant attack-success
cost (`+0.0169 +- 0.0191`, CI includes 0). The published conflict-scaled policy
is significantly *worse* than `sls_full` on accuracy and reaches only 0.910 on
drifted situations. If one of the two adaptive rules is to appear in the paper,
it should be this one.

## What the paper should change

| # | Finding | Action |
|:--|:--|:--|
| 1 | Payoff and prestige do nothing at encoding; fixing that makes the attack easier | Reword: surprise gates encoding, payoff and prestige govern consolidation. Do not change the code. |
| 2 | Confirmations bypass the gate, quorum and rival test | Close it as hardening; do not claim an attack-success benefit, there is none at these settings. |
| 3 | A track record gives sybil immunity and helps the farm | New result worth stating: a distinct-identity quorum is only as strong as the cost of an identity. |
| 4 | The sweep ran at 5.97 observations per window against a theory checked at 20 | Re-run the headline sweep at the theory's density. The mechanism looks far better there. |
| 5 | Runs are paired; intervals were unpaired | Report paired differences. Several ablations become significant. |
| 6 | `acc_unknown` pinned at 1.0000 for 15 of 21 policies | Report the ablation set under capacity pressure as well. |
| 7 | Eviction never fires at cap 150 | Either drop `sls_fifo_eviction` or run it at a cap that fills. Under pressure it is the largest effect in the table. |
| 8 | Farm support saturates at `n * chi` | State the ceiling; it is a clean result the current theory section omits. |
| 9 | Conflict-scaled quorum is worse than the fixed quorum | Replace it with the rival-support margin. |

Two further caveats found along the way, neither in the original list:

- The three baselines all run saturated at `mem` 150 with heavy eviction while no
  SLS policy ever fills, so part of the reported gap in Table 3 is that SLS
  stores less, not only that it stores better.
- `support()` reads credibility at query time, so a farm that earns reputation
  with forged outcomes retroactively raises the weight of injections it made
  earlier. If that is intended it should be said; if not, credibility should be
  captured when the observation is recorded.

## Reproducing

    python3 sls_retention_sim.py --seeds 20 --out runs/after.json
    python3 make_figures.py runs/after.json figures

All four published figures regenerate byte-identical from this branch.

## Follow-ups

### Figure 5: the density result now has a picture

`fig5_density.svg` puts the two quorum sweeps side by side. Panel (a): attack
success is flat in the quorum at 5.97 observations per situation per window and
falls monotonically at 20.0. Panel (b): up to k = 3 both densities pay almost the
same cost on drifted situations, so in the regime the propositions describe the
reduction is close to free. PNG export needs Chrome, which was not available
here, so it ships as SVG only.

### The headline quorum moves to k = 1

The sweep already showed k = 1 dominating k = 2; the full table confirms it.

| Policy | S0 acc | S0 drifted | S2 ASR | S3 ASR |
|:--|--:|--:|--:|--:|
| SLS, fixed quorum, k = 2 | 0.996 | 0.958 | 0.059 | 0.066 |
| SLS, fixed quorum, **k = 1** | **0.999** | **0.989** | 0.059 | **0.042** |

Better on three columns, identical on the fourth. `--headline-quorum` now
defaults to 1 and applies to every policy that does not name its own quorum; the
three baselines do not use a quorum and are unchanged, which is itself a check
that the knob only reaches what it should. `results/results.json` still
reproduces exactly with `--headline-quorum 2`.

### Freezing credibility at record time: no measurable effect

`support()` reads credibility when the support is read, not when it was recorded,
so a farm that earns reputation with forged outcomes retroactively raises the
weight of its earlier injections. `cred_frozen` stores credibility with each
support record instead.

Paired against `sls_full`: `asr +0.0025 +- 0.0041` under S2, `+0.0000 +- 0.0019`
under S3, `acc_all -0.0003 +- 0.0005`. Nothing significant. The farm starts from
a prior credibility of about 0.767, so there is little left to gain
retroactively, and the recency window bounds how far back it could reach.

This is the third code smell in this review that measurement clears: the ungated
confirmation path, the retroactive credibility read and the unbounded gate term
are all real, and none of them carries the attack at these settings. Worth saying
in the paper — the lifecycle is more robust than a reading of the code suggests.
The flags stay so the claim can be re-checked when parameters change.
