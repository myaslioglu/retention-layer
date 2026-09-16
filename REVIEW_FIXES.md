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
