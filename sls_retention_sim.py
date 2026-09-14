#!/usr/bin/env python3
"""
Mechanism-level proof of concept for a Retention Layer whose memory lifecycle is
governed by social learning strategies (SLS).

This is a controlled simulation, not a language-model experiment. Situations are
key vectors, behaviours are template embeddings, and the "base model" is a frozen
prior over templates that stands in for pretrained weights. The simulation checks
whether the read, write, consolidation, reconsolidation and forgetting operators
specified in the paper behave as intended under capacity pressure, noisy
demonstrators, world drift and memory-poisoning attacks.

Usage
  python3 sls_retention_sim.py --seeds 20 --out results.json      # full run
  python3 sls_retention_sim.py --quick                            # smoke test
Requires only NumPy.
"""
import argparse
import json
import math
import time
from dataclasses import asdict, dataclass, replace
from multiprocessing import get_context

import numpy as np

# ---------------------------------------------------------------------------
# Constants shared by the corrected-architecture variants (paper, Section 4)
# ---------------------------------------------------------------------------
TAU = 0.10          # key temperature (plays the role of sqrt(d_k))
NULL_LOGIT = 6.0    # logit of the null slot k_0 ("abstain"), about cosine 0.6 / TAU
BETA = 1.0          # weight of the retention-strength prior  beta * log s_i
LAMBDA = 10.0       # scale of the memory read in the template scores
DELTA_K = 0.70      # key-similarity threshold for "same situation"
ETA = 0.5           # reconsolidation step size
DECAY = 2e-4        # per-step multiplicative strength decay
S0 = 1.5            # surprise reference (nats) used by the gates
TH_S, TH_P, TH_C = 2.0, 2.0, 2.0   # SLS encoding-gate weights: surprise, payoff, credibility
CHI_MIN = 0.6       # credibility threshold of the trust-only baseline
CHI_UNIFORM = 0.7   # constant credibility used when credibility tracking is ablated
FARM_IDS = 5        # identities controlled by a reputation-farming attacker
WINDOW = 1500       # recency window (steps) within which a source counts as support


@dataclass(frozen=True)
class Env:
    d: int = 64
    n_sit: int = 200
    n_tpl: int = 50
    frac_known: float = 0.5
    n_honest: int = 100
    frac_newcomer: float = 0.3      # share of honest observations made by first-time identities
    n_targets: int = 20
    rho: float = 0.3                # adversarial share of observations on target situations
    attacker: str = "sybil"         # "sybil": fresh identity per injection; "farmed": FARM_IDS reputable identities
    forge_outcomes: bool = False    # attacker can fake positive outcomes for its demonstrations
    p_outcome: float = 0.7          # probability that a demonstration's outcome is observed
    T: int = 6000
    p_interact: float = 0.2         # share of steps where the model must reproduce a behaviour
    p_feedback: float = 0.7         # probability that a reproduction receives outcome feedback
    key_noise: float = 0.35
    drift_frac: float = 0.2         # share of known, non-target situations whose correct behaviour changes
    drift_at: int = 3000
    eval_every: int = 250


@dataclass(frozen=True)
class Policy:
    name: str
    gate: str = "sls"               # "none" | "surprise" | "trust" | "sls"
    use_buffer: bool = True         # two tiers: episodic buffer -> retention store
    quorum: float = 2.0             # credibility-weighted support needed for consolidation (0 = immediate)
    quorum_gamma: float = 0.0       # conflict scaling of the quorum: k = quorum + gamma * conflict
    use_cred: bool = True
    use_payoff: bool = True
    reconsolidate: bool = True
    null_slot: bool = True
    strength_prior: bool = True
    eviction: str = "strength"      # "strength" | "fifo"
    decay: bool = True
    confirm: bool = True            # record support for stored templates and for the model's current behaviour
    relative: bool = True           # consolidate only when support exceeds every rival template's support
    cap: int = 150
    buf_cap: int = 400


def unit(v, axis=-1):
    return v / np.maximum(np.linalg.norm(v, axis=axis, keepdims=True), 1e-12)


def softmax(z, axis=-1):
    z = z - z.max(axis=axis, keepdims=True)
    e = np.exp(z)
    return e / e.sum(axis=axis, keepdims=True)


def log_softmax(z):
    z = z - z.max()
    return z - math.log(np.exp(z).sum())


# ---------------------------------------------------------------------------
# World: situations, templates, demonstrators, attacker, drift
# ---------------------------------------------------------------------------
class World:
    def __init__(self, env: Env, seed: int):
        rng = np.random.default_rng(seed)
        self.env = env
        d, C, A, T = env.d, env.n_sit, env.n_tpl, env.T
        self.Z = unit(rng.standard_normal((C, d)))
        self.E = unit(rng.standard_normal((A, d)))
        self.b_pre = rng.integers(0, A, C)

        self.targets = np.zeros(C, bool)
        self.targets[rng.choice(C, env.n_targets, replace=False)] = True
        self.known = np.zeros(C, bool)
        self.known[rng.choice(C, int(round(env.frac_known * C)), replace=False)] = True
        pool = np.where(self.known & ~self.targets)[0]
        self.drifted = np.zeros(C, bool)
        n_drift = int(round(env.drift_frac * pool.size))
        if n_drift:
            self.drifted[rng.choice(pool, n_drift, replace=False)] = True

        self.b_post = self.b_pre.copy()
        idx = np.where(self.drifted)[0]
        self.b_post[idx] = (self.b_pre[idx] + rng.integers(1, A, idx.size)) % A
        self.attack = self.b_post.copy()
        idx = np.where(self.targets)[0]
        self.attack[idx] = (self.b_post[idx] + rng.integers(1, A, idx.size)) % A

        # Frozen base model: confident on known situations (pre-drift truth), uniform elsewhere.
        self.base_logp = np.full((C, A), -math.log(A))
        kn = np.where(self.known)[0]
        self.base_logp[kn] = math.log(0.1 / (A - 1))
        self.base_logp[kn, self.b_pre[kn]] = math.log(0.9)

        self.q = rng.beta(8, 2, env.n_honest)                     # demonstrator accuracy
        self.q_hat = np.clip(self.q + rng.normal(0, 0.1, env.n_honest), 0.05, 0.95)

        # Pre-drawn event stream: identical for every policy under the same seed.
        self.sit = rng.integers(0, C, T)
        self.is_inter = rng.random(T) < env.p_interact
        self.noise = rng.standard_normal((T, d)) * (env.key_noise / math.sqrt(d))
        self.u_adv = rng.random(T)
        self.u_new = rng.random(T)
        self.src_pick = rng.integers(0, env.n_honest, T)
        self.new_q = rng.beta(8, 2, T)
        self.u_corr = rng.random(T)
        self.wrong_off = rng.integers(1, A, T)
        self.u_out = rng.random(T)
        self.u_fb = rng.random(T)
        self.farm_pick = rng.integers(0, FARM_IDS, T)
        self.eval_noise = rng.standard_normal((T // env.eval_every, C, d)) * (env.key_noise / math.sqrt(d))

    def truth(self, t):
        return self.b_post if t >= self.env.drift_at else self.b_pre

    def rep_prior(self, src):
        """Beta pseudo-counts: established honest identities carry a noisy track record."""
        if src < self.env.n_honest:
            qh = self.q_hat[src]
            return [1 + 4 * qh, 1 + 4 * (1 - qh)]
        if 900_000 <= src < 900_000 + FARM_IDS:
            return [1 + 4 * 0.9, 1 + 4 * 0.1]
        return [1.0, 1.0]

    def event(self, t):
        env = self.env
        c = int(self.sit[t])
        x = unit(self.Z[c] + self.noise[t])
        tr = int(self.truth(t)[c])
        if self.is_inter[t]:
            return ("int", c, x, tr, bool(self.u_fb[t] < env.p_feedback))
        seen = self.u_out[t] < env.p_outcome
        if self.targets[c] and self.u_adv[t] < env.rho:
            tpl = int(self.attack[c])
            src = 1_000_000 + t if env.attacker == "sybil" else 900_000 + int(self.farm_pick[t])
            y = (1 if env.forge_outcomes else -1) if seen else 0
            return ("obs", c, x, tpl, src, y)
        if self.u_new[t] < env.frac_newcomer:
            src, q = 2_000_000 + t, self.new_q[t]
        else:
            src = int(self.src_pick[t])
            q = self.q[src]
        ok = self.u_corr[t] < q
        tpl = tr if ok else int((tr + self.wrong_off[t]) % env.n_tpl)
        y = (1 if ok else -1) if seen else 0
        return ("obs", c, x, tpl, src, y)


# ---------------------------------------------------------------------------
# Retention memory: read (attention/reproduction), gated write (retention),
# consolidation, reconsolidation (motivation) and forgetting
# ---------------------------------------------------------------------------
class RetentionMemory:
    def __init__(self, pol: Policy, world: World):
        self.p, self.w = pol, world
        d = world.env.d
        self.K = np.zeros((pol.cap, d))
        self.V = np.zeros(pol.cap, dtype=int)
        self.S = np.ones(pol.cap)
        self.age = np.zeros(pol.cap)
        self.sup = [None] * pol.cap                  # per slot: {source: last time it supported the slot}
        self.m = 0
        B = pol.buf_cap
        self.bK = np.zeros((B, d))
        self.bV = np.full(B, -1)
        self.bC = np.zeros(B, dtype=int)
        self.bN = np.zeros(B)
        self.bT = np.zeros(B)
        self.bSup = [None] * B
        self.bTally = np.zeros(B, bool)              # entries that only count support for the current behaviour
        self.bPay = np.zeros(B)
        self.bPayN = np.zeros(B)
        self.rep = {}
        self.stats = dict(obs=0, gate_pass=0, buf_insert=0, store_insert=0, store_merge=0,
                          evicted=0, dropped=0, promoted=0, confirmations=0, tallies=0, blocked_by_rival=0)

    # -- credibility (prestige earned by success) --------------------------
    def _rep(self, src):
        ab = self.rep.get(src)
        if ab is None:
            ab = self.rep[src] = self.w.rep_prior(src)
        return ab

    def cred(self, src):
        if not self.p.use_cred:
            return CHI_UNIFORM
        ab = self._rep(src)
        return ab[0] / (ab[0] + ab[1])

    def rep_add(self, src, positive, amount):
        self._rep(src)[0 if positive else 1] += amount

    def support(self, sup, t):
        """Credibility-weighted count of distinct sources seen within the recency window."""
        return sum(self.cred(s) for s, ts in sup.items() if t - ts <= WINDOW) if sup else 0.0

    # -- read ----------------------------------------------------------------
    def _attend(self, X):
        m = self.m
        L = X @ self.K[:m].T / TAU
        if self.p.strength_prior:
            L = L + BETA * np.log(np.maximum(self.S[:m], 1e-12))
        if self.p.null_slot:
            L = np.concatenate([np.full((X.shape[0], 1), NULL_LOGIT), L], axis=1)
            return softmax(L, axis=1)[:, 1:]
        return softmax(L, axis=1)

    def scores(self, cs, X):
        base = self.w.base_logp[cs]
        if self.m == 0:
            return base, None
        att = self._attend(X)
        R = att @ self.w.E[self.V[:self.m]]
        return base + LAMBDA * (R @ self.w.E.T), att

    # -- write path ------------------------------------------------------------
    def observe(self, c, x, tpl, src, y, t):
        p, st = self.p, self.stats
        st["obs"] += 1
        if y != 0:                                   # vicarious outcome -> demonstrator reputation
            self.rep_add(src, y > 0, 1.0)
        if p.gate == "none":                         # v1: append everything
            st["gate_pass"] += 1
            self.store(x, tpl, {src: t}, 1.0, t, merge=False)
            return
        if p.confirm and self.m:
            sims = self.K[:self.m] @ x
            match = np.nonzero((self.V[:self.m] == tpl) & (sims > DELTA_K))[0]
            if match.size:                           # confirmation of a stored template
                i = int(match[np.argmax(sims[match])])
                if y < 0:
                    self.S[i] *= math.exp(-ETA)      # observed failure weakens the template
                else:
                    if src not in self.sup[i]:
                        self.S[i] += self.cred(src)  # a new independent source adds its credibility
                    self.sup[i][src] = t
                st["confirmations"] += 1
                return
        sc, _ = self.scores(np.array([c]), x[None, :])
        surprise = -log_softmax(sc[0])[tpl]
        if p.confirm and surprise <= S0:             # the model already behaves this way: tally the support
            if p.relative:
                self.buffer_add(c, x, tpl, src, y, t, tally=True)
            st["tallies"] += 1
            return
        chi = self.cred(src)
        pay = 0.5 if (y == 0 or not p.use_payoff) else (1.0 if y > 0 else 0.0)
        if p.gate == "surprise":
            ok = surprise > S0
        elif p.gate == "trust":
            ok = chi >= CHI_MIN
        else:
            ok = TH_S * (surprise - S0) + TH_P * (pay - 0.5) + TH_C * (chi - 0.5) > 0
        if not ok:
            return
        st["gate_pass"] += 1
        if not p.use_buffer:
            self.store(x, tpl, {src: t}, chi if p.gate == "trust" else 1.0, t)
            return
        i = self.buffer_add(c, x, tpl, src, y, t)
        self.try_promote(i, t)

    def buffer_add(self, c, x, tpl, src, y, t, tally=False):
        valid = self.bV >= 0
        idx = None
        if valid.any():
            sims = self.bK @ x
            cand = np.nonzero(valid & (self.bV == tpl) & (sims > DELTA_K))[0]
            if cand.size:
                idx = int(cand[np.argmax(sims[cand])])
        if idx is None:
            free = np.nonzero(~valid)[0]
            idx = int(free[0]) if free.size else int(np.argmin(self.bT))   # evict least recently updated
            self.bK[idx] = x
            self.bV[idx] = tpl
            self.bC[idx] = c
            self.bN[idx] = 0
            self.bSup[idx] = {}
            self.bTally[idx] = tally
            self.bPay[idx] = 0.0
            self.bPayN[idx] = 0.0
            self.stats["buf_insert"] += 1
        else:
            self.bK[idx] = unit(self.bK[idx] * self.bN[idx] + x)
            if not tally:
                self.bTally[idx] = False             # a surprising observation makes the entry a candidate
        self.bN[idx] += 1
        self.bT[idx] = t
        self.bSup[idx][src] = t
        if y != 0 and self.p.use_payoff:
            self.bPay[idx] += 1.0 if y > 0 else 0.0
            self.bPayN[idx] += 1.0
        return idx

    def conflict(self, c, x, tpl):
        """Log-odds by which template tpl is dispreferred to the current top choice (0 if it is the top)."""
        sc, _ = self.scores(np.array([c]), x[None, :])
        lp = log_softmax(sc[0])
        return max(0.0, float(lp.max() - lp[tpl]))

    def rival_support(self, i, t):
        """Largest windowed support of any other template recorded for the same situation."""
        x, tpl, best = self.bK[i], self.bV[i], 0.0
        valid = self.bV >= 0
        valid[i] = False
        for j in np.nonzero(valid & (self.bV != tpl) & (self.bK @ x > DELTA_K))[0]:
            best = max(best, self.support(self.bSup[j], t))
        if self.m:
            for j in np.nonzero((self.V[:self.m] != tpl) & (self.K[:self.m] @ x > DELTA_K))[0]:
                best = max(best, self.support(self.sup[j], t))
        return best

    def try_promote(self, i, t):
        if self.bTally[i]:
            return
        sup = self.bSup[i]
        q = self.support(sup, t)
        k_eff = self.p.quorum
        if self.p.quorum_gamma > 0:
            k_eff += self.p.quorum_gamma * self.conflict(int(self.bC[i]), self.bK[i], int(self.bV[i]))
        if q + 1e-9 < k_eff:
            return
        if self.p.use_payoff and self.bPayN[i] > 0 and self.bPay[i] / self.bPayN[i] < 0.5:
            return
        if self.p.relative and q <= self.rival_support(i, t):
            self.stats["blocked_by_rival"] += 1
            return
        self.store(self.bK[i].copy(), int(self.bV[i]), dict(sup), max(q, 1e-3), t, distinct=True)
        self.stats["promoted"] += 1
        self.bV[i] = -1
        self.bSup[i] = None

    def store(self, x, tpl, sup, s0, t, merge=True, distinct=False):
        p, m = self.p, self.m
        if merge and m:
            sims = self.K[:m] @ x
            cand = np.nonzero((self.V[:m] == tpl) & (sims > DELTA_K))[0]
            if cand.size:
                i = int(cand[np.argmax(sims[cand])])
                if distinct:                         # each source counts once
                    self.S[i] += sum(self.cred(s) for s in sup if s not in self.sup[i])
                else:
                    self.S[i] += s0
                self.K[i] = unit(self.K[i] + 0.5 * x)
                for s, ts in sup.items():
                    self.sup[i][s] = max(ts, self.sup[i].get(s, ts))
                self.stats["store_merge"] += 1
                return
        if m < p.cap:
            i = m
            self.m += 1
        else:
            if p.eviction == "fifo":
                i = int(np.argmin(self.age[:m]))
            else:
                i = int(np.argmin(self.S[:m]))
                if s0 <= self.S[i]:
                    self.stats["dropped"] += 1
                    return
            self.stats["evicted"] += 1
        self.K[i], self.V[i], self.S[i], self.age[i], self.sup[i] = x, tpl, s0, t, dict(sup)
        self.stats["store_insert"] += 1

    # -- reproduction + motivation (reconsolidation) ----------------------------
    def interact(self, c, x, truth, fb, t):
        sc, att = self.scores(np.array([c]), x[None, :])
        pred = int(np.argmax(sc[0]))
        correct = pred == truth
        if not (fb and self.p.reconsolidate and att is not None):
            return correct
        y = 1.0 if correct else -1.0
        m = self.m
        agree = self.V[:m] == pred
        signed = np.where(agree, y, -1.0 if y > 0 else 0.0)
        delta = ETA * att[0] * signed
        self.S[:m] *= np.exp(delta)
        for i in np.nonzero(np.abs(delta) > 1e-3)[0]:
            share = abs(delta[i]) / ETA / max(len(self.sup[i]), 1)
            for s in self.sup[i]:
                self.rep_add(s, delta[i] > 0, share)
        return correct

    def tick(self):
        if self.p.decay and self.m:
            self.S[:self.m] *= 1.0 - DECAY

    def evaluate(self, t, k):
        w = self.w
        X = unit(w.Z + w.eval_noise[k])
        sc, _ = self.scores(np.arange(w.env.n_sit), X)
        pred = sc.argmax(1)
        corr = pred == w.truth(t)
        stable = w.known & ~w.drifted & ~w.targets
        unknown = ~w.known & ~w.targets
        hit = pred == w.attack
        tk, tu = w.targets & w.known, w.targets & ~w.known
        nan = float("nan")
        return dict(
            t=t, acc_all=float(corr.mean()), acc_known_stable=float(corr[stable].mean()),
            acc_unknown=float(corr[unknown].mean()),
            acc_drifted=float(corr[w.drifted].mean()) if w.drifted.any() else nan,
            acc_target=float(corr[w.targets].mean()), asr=float(hit[w.targets].mean()),
            asr_known_targets=float(hit[tk].mean()) if tk.any() else nan,
            asr_unknown_targets=float(hit[tu].mean()) if tu.any() else nan,
            mem=int(self.m))


def run_policy(world, pol):
    mem = RetentionMemory(pol, world)
    env = world.env
    curve, k, ok_n, n = [], 0, 0, 0
    for t in range(env.T):
        ev = world.event(t)
        if ev[0] == "obs":
            mem.observe(ev[1], ev[2], ev[3], ev[4], ev[5], t)
        else:
            ok_n += mem.interact(ev[1], ev[2], ev[3], ev[4], t)
            n += 1
        mem.tick()
        if (t + 1) % env.eval_every == 0 and k < world.eval_noise.shape[0]:
            curve.append(mem.evaluate(t + 1, k))
            k += 1
    return dict(curve=curve, stats=mem.stats, online_acc=ok_n / max(n, 1))


# ---------------------------------------------------------------------------
# Experiment definitions
# ---------------------------------------------------------------------------
def main_policies():
    return [
        Policy("v1_ungated", gate="none", use_buffer=False, quorum=0.0, use_cred=False, use_payoff=False,
               reconsolidate=False, null_slot=False, strength_prior=False, eviction="fifo", decay=False,
               confirm=False, relative=False),
        Policy("surprise_gated", gate="surprise", use_buffer=False, confirm=False, relative=False),
        Policy("trust_gated", gate="trust", use_buffer=False, confirm=False, relative=False),
        Policy("sls_full"),
        Policy("sls_adaptive_quorum", quorum=1.0, quorum_gamma=0.25),
        Policy("sls_no_quorum", quorum=0.0),
        Policy("sls_no_relative", relative=False),
        Policy("sls_no_confirmation", confirm=False, relative=False),
        Policy("sls_no_credibility", use_cred=False),
        Policy("sls_no_payoff", use_payoff=False),
        Policy("sls_no_reconsolidation", reconsolidate=False),
        Policy("sls_fifo_eviction", eviction="fifo"),
        Policy("sls_no_null_slot", null_slot=False),
    ]


SCENARIOS = {
    "clean": dict(rho=0.0),
    "sybil": dict(rho=0.3, attacker="sybil", forge_outcomes=False),
    "sybil_forged": dict(rho=0.3, attacker="sybil", forge_outcomes=True),
    "farmed_forged": dict(rho=0.3, attacker="farmed", forge_outcomes=True),
}


def job(args):
    exp, scen, env_kw, seed, pols = args
    world = World(Env(**env_kw), seed)
    return exp, scen, seed, {p.name: run_policy(world, p) for p in pols}


def ci95(vals):
    v = np.asarray([x for x in vals if not (isinstance(x, float) and math.isnan(x))], float)
    if v.size == 0:
        return float("nan"), float("nan")
    tcrit = {2: 12.71, 3: 4.30, 5: 2.78, 10: 2.26, 20: 2.093}.get(v.size, 1.96)
    return float(v.mean()), (float(tcrit * v.std(ddof=1) / math.sqrt(v.size)) if v.size > 1 else 0.0)


METRICS = ["acc_all", "acc_known_stable", "acc_unknown", "acc_drifted", "acc_target", "asr",
           "asr_known_targets", "asr_unknown_targets"]
STATS = ["gate_pass", "store_insert", "store_merge", "promoted", "evicted", "dropped", "confirmations", "tallies",
         "blocked_by_rival"]


def summarize(runs, T):
    """runs: per-seed results for one policy -> final (t > T-1000) and time-averaged metrics with 95% CIs."""
    out = {}
    for kname in METRICS:
        fin = [np.nanmean([e[kname] for e in r["curve"] if e["t"] > T - 1000]) for r in runs]
        auc = [np.nanmean([e[kname] for e in r["curve"]]) for r in runs]
        out[kname] = dict(final=ci95(fin), mean_over_time=ci95(auc))
    out["online_acc"] = ci95([r["online_acc"] for r in runs])
    for s in STATS:
        out[s] = ci95([r["stats"][s] for r in runs])
    out["mem_final"] = ci95([r["curve"][-1]["mem"] for r in runs])
    tgrid = [e["t"] for e in runs[0]["curve"]]
    out["series"] = {kname: [float(np.nanmean([r["curve"][i][kname] for r in runs])) for i in range(len(tgrid))]
                     for kname in ["acc_all", "acc_unknown", "acc_drifted", "asr"]}
    out["series"]["t"] = tgrid
    return out


# ---------------------------------------------------------------------------
# Proposition 2: quorum consolidation, exact expressions vs Monte Carlo
# ---------------------------------------------------------------------------
def binom_tail(n, p, k):
    """P[Bin(n, p) >= k]."""
    if k <= 0:
        return 1.0
    if k > n:
        return 0.0
    return float(sum(math.comb(n, j) * p ** j * (1 - p) ** (n - j) for j in range(k, n + 1)))


def kl_bern(a, b):
    a = min(max(a, 1e-12), 1 - 1e-12)
    return a * math.log(a / b) + (1 - a) * math.log((1 - a) / (1 - b))


def quorum_study(seed=0, trials=200_000, N=20, q=0.8, chi_adv=0.5, chi_hon=0.8):
    rng = np.random.default_rng(seed)
    rows = []
    for rho in [0.1, 0.2, 0.3, 0.4]:
        for kq in [0.5, 1, 2, 3, 4, 5, 6]:
            ka = math.ceil(kq / chi_adv - 1e-9)
            kh = math.ceil(kq / chi_hon - 1e-9)
            p_adv = binom_tail(N, rho, ka)
            chern = math.exp(-N * kl_bern(ka / N, rho)) if ka / N > rho else 1.0
            lat = kh / ((1 - rho) * q)
            adv = rng.random((trials, N)) < rho
            p_adv_mc = float(((adv.sum(1) * chi_adv) >= kq - 1e-9).mean())
            L = 300
            hc = (rng.random((4_000, L)) >= rho) & (rng.random((4_000, L)) < q)
            reach = np.cumsum(hc, axis=1) * chi_hon >= kq - 1e-9
            lat_mc = float((reach.argmax(1) + 1).mean())
            rows.append(dict(rho=rho, quorum=kq, adv_needed=ka, honest_needed=kh,
                             p_adv_exact=p_adv, p_adv_mc=p_adv_mc, chernoff_bound=chern,
                             latency_exact=lat, latency_mc=lat_mc))
    return dict(N=N, q=q, chi_adv=chi_adv, chi_hon=chi_hon, rows=rows)


def race_study(seed=1, trials=5_000, q=0.8, chi_adv=0.5, chi_hon=0.8, L=1500):
    """Proposition 2(c): probability that the false template reaches the quorum first."""
    rng = np.random.default_rng(seed)
    rows = []
    for rho in [0.2, 0.3, 0.4, 0.5, 0.6]:
        u = rng.random((trials, L))
        adv = u < rho
        hon = (~adv) & (rng.random((trials, L)) < q)
        ca, ch = np.cumsum(adv, 1) * chi_adv, np.cumsum(hon, 1) * chi_hon
        for kq in [1, 2, 4, 8, 16]:
            ta = np.where((ca >= kq - 1e-9).any(1), (ca >= kq - 1e-9).argmax(1), L)
            tb = np.where((ch >= kq - 1e-9).any(1), (ch >= kq - 1e-9).argmax(1), L)
            ever = ((ca >= kq - 1e-9) & (ca > ch + 1e-9)).any(1)     # relative consolidation, unbounded window
            rows.append(dict(rho=rho, quorum=kq, p_false_first=float((ta < tb).mean()),
                             p_false_ever_consolidated_relative=float(ever.mean()),
                             honest_rate_higher=bool(chi_adv * rho < chi_hon * (1 - rho) * q)))
    return dict(q=q, chi_adv=chi_adv, chi_hon=chi_hon, rows=rows)


# ---------------------------------------------------------------------------
# Proposition 1: a Retention block with empty memory (or zero output projection)
# is exactly the base pre-LN Transformer block
# ---------------------------------------------------------------------------
def reduction_check(seed=0, n=7, d=16, h=4, m=5):
    rng = np.random.default_rng(seed)

    def ln(X, g, b):
        mu, sd = X.mean(-1, keepdims=True), X.std(-1, keepdims=True) + 1e-5
        return g * (X - mu) / sd + b

    def mha(X, W):
        Q, K, V = X @ W["q"], X @ W["k"], X @ W["v"]
        dk = d // h
        split = lambda Y: Y.reshape(n, h, dk).transpose(1, 0, 2)
        A = softmax(split(Q) @ split(K).transpose(0, 2, 1) / math.sqrt(dk) + np.triu(np.full((n, n), -1e9), 1), -1)
        return (A @ split(V)).transpose(1, 0, 2).reshape(n, d) @ W["o"]

    def ffn(X, W):
        return np.maximum(0, X @ W["1"]) @ W["2"]

    def mem_read(X, M, W, null_key):
        Q = X @ W["mq"]
        keys = np.vstack([null_key[None, :], M @ W["mk"]])
        vals = np.vstack([np.zeros((1, d)), M @ W["mv"]])
        A = softmax(Q @ keys.T / math.sqrt(d), -1)
        gate = 1 / (1 + np.exp(-(X @ W["g"])))
        return gate[:, None] * ((A @ vals) @ W["mo"])

    W = {k: rng.standard_normal((d, d)) / math.sqrt(d) for k in ["q", "k", "v", "o", "mq", "mk", "mv", "mo"]}
    W["1"], W["2"] = rng.standard_normal((d, 4 * d)) / math.sqrt(d), rng.standard_normal((4 * d, d)) / math.sqrt(d)
    W["g"] = rng.standard_normal(d)
    g1, b1, g2, b2, g3, b3 = [rng.standard_normal(d) for _ in range(6)]
    null_key = rng.standard_normal(d)
    X = rng.standard_normal((n, d))

    Zb = X + mha(ln(X, g1, b1), W)
    base = Zb + ffn(ln(Zb, g3, b3), W)

    def ret_block(M, Wm):
        Z = X + mha(ln(X, g1, b1), Wm)
        Y = Z + mem_read(ln(Z, g2, b2), M, Wm, null_key)
        return Y + ffn(ln(Y, g3, b3), Wm)

    empty = ret_block(np.zeros((0, d)), W)
    zero_out = ret_block(rng.standard_normal((m, d)), dict(W, mo=np.zeros((d, d))))
    nonzero = ret_block(rng.standard_normal((m, d)), W)
    return dict(max_abs_diff_empty_memory=float(np.abs(empty - base).max()),
                max_abs_diff_zero_output_projection=float(np.abs(zero_out - base).max()),
                max_abs_diff_nonempty_memory=float(np.abs(nonzero - base).max()))


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=20)
    ap.add_argument("--T", type=int, default=6000)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--out", default="results.json")
    ap.add_argument("--quick", action="store_true")
    a = ap.parse_args()
    if a.quick:
        a.seeds, a.T = 2, 2000

    base_env = dict(T=a.T, drift_at=a.T // 2)
    jobs = []
    for scen, kw in SCENARIOS.items():
        for s in range(a.seeds):
            jobs.append(("main", scen, dict(base_env, **kw), 1000 + s, main_policies()))
    quorum_pols = [replace(Policy("sls_full"), name=f"sls_q{kq:g}", quorum=float(kq)) for kq in [0, 1, 2, 3, 4, 6]]
    for scen in ["clean", "sybil_forged", "farmed_forged"]:
        for s in range(a.seeds):
            jobs.append(("quorum", scen, dict(base_env, **SCENARIOS[scen]), 1000 + s, quorum_pols))
    rho_pols = [p for p in main_policies() if p.name in ("v1_ungated", "trust_gated", "sls_full", "sls_adaptive_quorum")]
    for scen in ["sybil_forged", "farmed_forged"]:
        for rho in [0.1, 0.3, 0.5, 0.7]:
            for s in range(a.seeds):
                jobs.append(("rho", f"{scen}_rho{rho:g}", dict(base_env, **dict(SCENARIOS[scen], rho=rho)), 1000 + s, rho_pols))

    t0 = time.time()
    with get_context("spawn").Pool(a.workers) as pool:
        results = pool.map(job, jobs, chunksize=1)
    elapsed = time.time() - t0

    grouped = {}
    for exp, scen, seed, res in results:
        for pname, r in res.items():
            grouped.setdefault(exp, {}).setdefault(scen, {}).setdefault(pname, []).append(r)
    summary = {exp: {scen: {p: summarize(runs, a.T) for p, runs in d2.items()} for scen, d2 in d1.items()}
               for exp, d1 in grouped.items()}

    out = dict(
        config=dict(env=asdict(Env(**base_env)), seeds=a.seeds, elapsed_sec=elapsed,
                    constants=dict(TAU=TAU, NULL_LOGIT=NULL_LOGIT, BETA=BETA, LAMBDA=LAMBDA, DELTA_K=DELTA_K,
                                   ETA=ETA, DECAY=DECAY, S0=S0, TH_S=TH_S, TH_P=TH_P, TH_C=TH_C,
                                   CHI_MIN=CHI_MIN, CHI_UNIFORM=CHI_UNIFORM, FARM_IDS=FARM_IDS, WINDOW=WINDOW),
                    policies=[asdict(p) for p in main_policies()], scenarios=SCENARIOS),
        reduction_check=reduction_check(),
        quorum_theory=quorum_study(),
        race_theory=race_study(),
        summary=summary,
    )
    with open(a.out, "w") as f:
        json.dump(out, f, indent=1)

    print(f"elapsed {elapsed:.1f}s | reduction check: {out['reduction_check']}")
    cols = [("acc_all", "all"), ("acc_unknown", "unk"), ("acc_drifted", "drift"), ("acc_known_stable", "known"),
            ("asr", "ASR"), ("asr_known_targets", "ASR_k"), ("asr_unknown_targets", "ASR_u")]
    for scen in SCENARIOS:
        print(f"\n== main / {scen}: final (t > T-1000), mean over {a.seeds} seeds; unkAUC = unknown acc averaged over time")
        print(f"{'policy':24s} " + " ".join(f"{h:>6s}" for _, h in cols) + f" {'unkAUC':>6s} {'online':>6s} {'ins':>5s} {'prom':>5s} {'block':>5s}")
        for p, sm in summary["main"][scen].items():
            print(f"{p:24s} " + " ".join(f"{sm[k]['final'][0]:6.3f}" for k, _ in cols)
                  + f" {sm['acc_unknown']['mean_over_time'][0]:6.3f} {sm['online_acc'][0]:6.3f}"
                  + f" {sm['store_insert'][0]:5.0f} {sm['promoted'][0]:5.0f} {sm['blocked_by_rival'][0]:5.0f}")
    for scen, d2 in summary["quorum"].items():
        print(f"\n== quorum sweep / {scen}")
        for p, sm in d2.items():
            print(f"{p:10s} acc_all={sm['acc_all']['final'][0]:.3f} unkAUC={sm['acc_unknown']['mean_over_time'][0]:.3f} "
                  f"drift={sm['acc_drifted']['final'][0]:.3f} ASR={sm['asr']['final'][0]:.3f}")
    print("\n== rho sweep: (acc_all, ASR) final")
    for scen, d2 in summary["rho"].items():
        print(scen, {p: (round(sm["acc_all"]["final"][0], 3), round(sm["asr"]["final"][0], 3)) for p, sm in d2.items()})
    print("\n== race study (Proposition 2c): P(false template first)")
    for r in out["race_theory"]["rows"]:
        print(r)


if __name__ == "__main__":
    main()
