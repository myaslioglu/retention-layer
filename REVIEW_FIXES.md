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
