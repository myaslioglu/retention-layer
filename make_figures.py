#!/usr/bin/env python3
"""
Figures for the revised Retention Layer paper.

Writes SVG sources and, when Chrome or Chromium is available, renders them to PNG at
twice their size with the headless browser.
Usage: python3 make_figures.py results.json output_dir
Set CHROME to the browser binary if it is not found automatically.
"""
import json
import math
import os
import shutil
import subprocess
import time
import sys
from pathlib import Path

INK, INK2, MUTED, GRID, AXIS, SURF = "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7", "#ffffff"
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"]      # categorical slots 1-4, fixed order
RAMP = ["#86b6ef", "#3987e5", "#1c5cab", "#0d366b"]        # ordinal blue ramp for rho
ACCENT, ACCENT_FILL, NEUTRAL_FILL = "#2a78d6", "#eef4fc", "#f6f6f4"
FONT = "Helvetica Neue, Helvetica, Arial, sans-serif"
MAC_CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

LABELS = {
    "sls_adaptive_quorum": "SLS, conflict-scaled quorum",
    "sls_full": "SLS, fixed quorum",
    "trust_gated": "Trust-gated",
    "surprise_gated": "Surprise-gated",
    "v1_ungated": "First version (ungated)",
}


def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def rich(s):
    """Escape text and turn _{...} and ^{...} into subscript and superscript spans."""
    out, i = [], 0
    while i < len(s):
        if s[i] in "_^" and i + 1 < len(s) and s[i + 1] == "{":
            j = s.index("}", i)
            dy = "0.3em" if s[i] == "_" else "-0.45em"
            back = "-0.3em" if s[i] == "_" else "0.45em"
            out.append(f'<tspan dy="{dy}" font-size="72%">{esc(s[i + 2:j])}</tspan><tspan dy="{back}">​</tspan>')
            i = j + 1
        else:
            k = i
            while k < len(s) and not (s[k] in "_^" and k + 1 < len(s) and s[k + 1] == "{"):
                k += 1
            out.append(esc(s[i:k]))
            i = k
    return "".join(out)


class Svg:
    def __init__(self, w, h):
        self.w, self.h, self.parts, self.defs = w, h, [], []

    def add(self, s):
        self.parts.append(s)

    def text(self, x, y, s, size=15, fill=INK2, anchor="start", weight="normal", style="normal", rotate=None):
        tr = f' transform="rotate({rotate} {x:.1f} {y:.1f})"' if rotate is not None else ""
        self.add(f'<text x="{x:.1f}" y="{y:.1f}" font-family="{FONT}" font-size="{size}" fill="{fill}" '
                 f'text-anchor="{anchor}" font-weight="{weight}" font-style="{style}"{tr}>{rich(s)}</text>')

    def line(self, x1, y1, x2, y2, stroke=GRID, width=1.0):
        self.add(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="{stroke}" '
                 f'stroke-width="{width}" stroke-linecap="round"/>')

    def polyline(self, pts, stroke, width=2.0, clip=None):
        d = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in pts)
        c = f' clip-path="url(#{clip})"' if clip else ""
        self.add(f'<path d="{d}" fill="none" stroke="{stroke}" stroke-width="{width}" '
                 f'stroke-linejoin="round" stroke-linecap="round"{c}/>')

    def dot(self, x, y, fill, r=4.5):
        self.add(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r}" fill="{fill}" stroke="{SURF}" stroke-width="2"/>')

    def rect(self, x, y, w, h, fill="none", stroke=AXIS, width=1.2, rx=8):
        self.add(f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="{rx}" fill="{fill}" '
                 f'stroke="{stroke}" stroke-width="{width}"/>')

    def arrow(self, d, stroke=INK2, width=1.6):
        self.add(f'<path d="{d}" fill="none" stroke="{stroke}" stroke-width="{width}" stroke-linejoin="round" '
                 f'marker-end="url(#arrow)"/>')

    def clip(self, cid, x, y, w, h):
        self.defs.append(f'<clipPath id="{cid}"><rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}"/></clipPath>')

    def save(self, path):
        defs = ('<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
                f'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="{INK2}"/></marker>'
                + "".join(self.defs) + "</defs>")
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w}" height="{self.h}" '
               f'viewBox="0 0 {self.w} {self.h}">{defs}<rect width="100%" height="100%" fill="{SURF}"/>'
               + "".join(self.parts) + "</svg>")
        Path(path).write_text(svg, encoding="utf-8")


class Panel:
    def __init__(self, svg, x, y, w, h, xdom, ydom, ylog=False, cid=None):
        self.s, self.x, self.y, self.w, self.h = svg, x, y, w, h
        self.xdom, self.ydom, self.ylog, self.cid = xdom, ydom, ylog, cid
        if cid:
            svg.clip(cid, x - 6, y - 6, w + 12, h + 12)

    def X(self, v):
        return self.x + (v - self.xdom[0]) / (self.xdom[1] - self.xdom[0]) * self.w

    def Y(self, v):
        if self.ylog:
            lo, hi = math.log10(self.ydom[0]), math.log10(self.ydom[1])
            v = math.log10(max(v, self.ydom[0] * 1e-3))
        else:
            lo, hi = self.ydom
        return self.y + self.h - (v - lo) / (hi - lo) * self.h

    def axes(self, xticks, yticks, xfmt, yfmt, xlabel, ylabel, title):
        s = self.s
        for v in yticks:
            yy = self.Y(v)
            s.line(self.x, yy, self.x + self.w, yy, GRID)
            s.text(self.x - 9, yy + 5, yfmt(v), 14, MUTED, "end")
        s.line(self.x, self.y + self.h, self.x + self.w, self.y + self.h, AXIS, 1.2)
        for v in xticks:
            s.text(self.X(v), self.y + self.h + 22, xfmt(v), 14, MUTED, "middle")
        s.text(self.x + self.w / 2, self.y + self.h + 48, xlabel, 15, INK2, "middle")
        s.text(self.x - 58, self.y + self.h / 2, ylabel, 15, INK2, "middle", rotate=-90)
        s.text(self.x - 58, self.y - 16, title, 16, INK, "start", "600")


# ---------------------------------------------------------------------------
# Figure 1: architecture and lifecycle
# ---------------------------------------------------------------------------
def box(s, x, y, w, h, title, lines, accent=False, tag=None):
    s.rect(x, y, w, h, ACCENT_FILL if accent else NEUTRAL_FILL, ACCENT if accent else AXIS, 1.6 if accent else 1.2)
    s.text(x + 14, y + 25, title, 16, INK, "start", "600")
    for k, ln in enumerate(lines):
        s.text(x + 14, y + 48 + 21 * k, ln, 14.5, INK2)
    if tag:
        s.text(x + w - 12, y + 23, tag, 13, MUTED, "end", style="italic")


def fig1(path):
    s = Svg(1000, 600)
    s.text(30, 32, "Memory-bearing block, during session t", 17, INK, "start", "600")
    s.text(640, 32, "Retention lifecycle, after session t", 17, INK, "start", "600")

    s.arrow("M190,50 V70")
    s.text(200, 64, "X_{t}^{(ℓ)}", 14, MUTED)
    box(s, 40, 72, 300, 74, "Self-attention", ["Z = X + MHA(LN(X))"])
    s.arrow("M190,146 V174")
    box(s, 40, 176, 300, 118, "Retention read (Eq. 3)",
        ["null slot k_{0} lets the read abstain", "strength prior β log s_{i}",
         "Y = Z + g · W_{O} Σ α_{i} W_{V} v_{i}"], accent=True, tag="attention")
    s.arrow("M190,294 V322")
    box(s, 40, 324, 300, 74, "Feed-forward", ["X′ = Y + FFN(LN(Y))"])
    s.arrow("M190,398 V525 H636")
    s.text(202, 462, "outputs b̂ and their outcomes", 14, MUTED)

    box(s, 392, 196, 206, 150, "Retention store M",
        ["slots (k_{i}, v_{i}, s_{i}, S_{i})", "bounded size m ≤ m_{max}", "forgetting:", "s_{i} ← (1 − λ) s_{i}"],
        accent=True)
    s.arrow("M392,236 H344")
    s.text(368, 226, "read", 13, MUTED, "middle")

    box(s, 640, 62, 330, 74, "Observations O_{t}", ["(c, b, σ, y): situation, behaviour,", "source, outcome"])
    s.arrow("M805,136 V160")
    box(s, 640, 162, 330, 74, "Encoding gate (7a)", ["surprise υ, payoff π, credibility χ_{σ}"], tag="retention")
    s.arrow("M805,236 V260")
    box(s, 640, 262, 330, 74, "Episodic buffer B", ["support Q_{W}(e): distinct, recent sources"])
    s.arrow("M805,336 V360")
    box(s, 640, 362, 330, 74, "Relative quorum consolidation (7b)",
        ["Q_{W}(e) ≥ k_{0} + γ Δ(e), n^{+} ≥ n^{−}", "and Q_{W}(e) above every rival"])
    s.arrow("M640,399 H620 V326 H602")
    s.text(612, 374, "consolidate", 13, MUTED, "end")

    s.arrow("M640,99 H495 V192")
    s.text(565, 90, "confirmations, tallies", 13, MUTED, "middle")

    box(s, 640, 480, 330, 90, "Reconsolidation (8)", ["s_{i} ← s_{i} exp(η ᾱ_{i} ψ_{i})", "credibility of supporters updated"],
        accent=False, tag="motivation")
    s.arrow("M640,510 H495 V350")
    s.text(560, 500, "revise strengths", 13, MUTED, "middle")
    s.save(path)


# ---------------------------------------------------------------------------
# Figure 2: quorum trade-off (Proposition 2)
# ---------------------------------------------------------------------------
def binom_tail(n, p, k):
    if k <= 0:
        return 1.0
    if k > n:
        return 0.0
    return sum(math.comb(n, j) * p ** j * (1 - p) ** (n - j) for j in range(k, n + 1))


def fig2(res, path):
    qt = res["quorum_theory"]
    N, q, ca, ch = qt["N"], qt["q"], qt["chi_adv"], qt["chi_hon"]
    rhos = [0.1, 0.2, 0.3, 0.4]
    s = Svg(1000, 480)
    x = 90
    s.text(x - 58, 32, "Adversarial share of observations", 15, INK2)
    x += 210
    for c, rho in zip(RAMP, rhos):
        s.line(x, 27, x + 26, 27, c, 3)
        s.text(x + 32, 32, f"ρ = {rho:g}", 15, INK2)
        x += 104
    s.text(x + 14, 32, "Lines: exact.  Dots: Monte Carlo.", 14, MUTED)

    left = Panel(s, 100, 100, 350, 290, (0, 6.2), (1e-5, 1), ylog=True, cid="cl")
    left.axes([0, 1, 2, 3, 4, 5, 6], [1, 1e-1, 1e-2, 1e-3, 1e-4, 1e-5], lambda v: f"{v:g}",
              lambda v: {1: "1", 0.1: "0.1", 0.01: "0.01"}.get(v, "10^{" + str(int(round(math.log10(v)))) + "}"),
              "Quorum k (credibility units)", "P(false template consolidated)",
              "(a) False template consolidated within N = 20")
    right = Panel(s, 600, 100, 310, 290, (0, 6.2), (0, 18), cid="cr")
    right.axes([0, 1, 2, 3, 4, 5, 6], [0, 3, 6, 9, 12, 15, 18], lambda v: f"{v:g}", lambda v: f"{v:g}",
               "Quorum k (credibility units)", "Observations until consolidation",
               "(b) Delay before the true template")
    grid = [i / 100 for i in range(50, 621)]
    for c, rho in zip(RAMP, rhos):
        s.polyline([(left.X(k), left.Y(binom_tail(N, rho, math.ceil(k / ca - 1e-9)))) for k in grid], c, 2.2, clip="cl")
        s.polyline([(right.X(k), right.Y(math.ceil(k / ch - 1e-9) / ((1 - rho) * q))) for k in grid], c, 2.2, clip="cr")
        for r in qt["rows"]:
            if abs(r["rho"] - rho) < 1e-9:
                if r["p_adv_mc"] >= 1e-5:
                    s.dot(left.X(r["quorum"]), left.Y(r["p_adv_mc"]), c)
                s.dot(right.X(r["quorum"]), right.Y(r["latency_mc"]), c)
        y_end = math.ceil(6.2 / ch - 1e-9) / ((1 - rho) * q)
        s.text(right.X(6.2) + 8, right.Y(y_end) + 5, f"ρ = {rho:g}", 14, INK2)
    s.save(path)


# ---------------------------------------------------------------------------
# Figure 3: simulation dynamics
# ---------------------------------------------------------------------------
def pct(v):
    return f"{int(round(v * 100))}%"


def fig3(res, path, panels, policies):
    main = res["summary"]["main"]
    s = Svg(1000, 760)
    x = 42
    for c, p in zip(SERIES, policies):
        s.line(x, 27, x + 26, 27, c, 3)
        s.text(x + 32, 32, LABELS[p], 15, INK2)
        x += 32 + 8.3 * len(LABELS[p]) + 30
    T = res["config"]["env"]["T"]
    drift = res["config"]["env"]["drift_at"]
    coords = [(100, 100), (600, 100), (100, 450), (600, 450)]
    for idx, ((scen, metric, title, ydom, yticks), (px, py)) in enumerate(zip(panels, coords)):
        P = Panel(s, px, py, 350, 220, (0, T), ydom, cid=f"p{idx}")
        P.axes([0, 1500, 3000, 4500, 6000], yticks, lambda v: f"{int(v):,}", pct, "Step", "Share of situations", title)
        s.line(P.X(drift), py, P.X(drift), py + 220, AXIS, 1.2)
        s.text(P.X(drift) + 6, py + 16, "drift", 13, MUTED)
        for c, p in zip(SERIES, policies):
            ser = main[scen][p]["series"]
            pts = [(P.X(t), P.Y(v)) for t, v in zip(ser["t"], ser[metric]) if v == v]
            s.polyline(pts, c, 2.2, clip=f"p{idx}")
    s.save(path)


# ---------------------------------------------------------------------------
# Figure 4: attack success as the adversarial share grows
# ---------------------------------------------------------------------------
def fig4(res, path, policies):
    rho_sum = res["summary"]["rho"]
    s = Svg(1000, 470)
    x = 42
    for c, p in zip(SERIES, policies):
        s.line(x, 27, x + 26, 27, c, 3)
        s.text(x + 32, 32, LABELS[p], 15, INK2)
        x += 32 + 8.3 * len(LABELS[p]) + 30
    rhos = [0.1, 0.3, 0.5, 0.7]
    panels = [("sybil_forged", "(a) S2: fresh identities, forged outcomes", 100),
              ("farmed_forged", "(b) S3: reputable identities, forged outcomes", 600)]
    for idx, (scen, title, px) in enumerate(panels):
        P = Panel(s, px, 100, 350, 260, (0.0, 0.8), (0, 0.8), cid=f"q{idx}")
        P.axes(rhos, [0, 0.2, 0.4, 0.6, 0.8], lambda v: f"{v:g}", pct,
               "Adversarial share of observations, ρ", "Attack success rate", title)
        for j, (c, p) in enumerate(zip(SERIES, policies)):
            off = (j - 1.5) * 0.012
            pts = []
            for rho in rhos:
                m, h = rho_sum[f"{scen}_rho{rho:g}"][p]["asr"]["final"]
                X = P.X(rho + off)
                s.line(X, P.Y(max(m - h, 0.0)), X, P.Y(min(m + h, 0.8)), c, 1.4)
                pts.append((X, P.Y(m)))
            s.polyline(pts, c, 2.0, clip=f"q{idx}")
            for X, Y in pts:
                s.dot(X, Y, c, 4.5)
    s.save(path)


def find_chrome():
    """Browser for PNG export: $CHROME, then Google Chrome on macOS, then Chrome or Chromium on PATH."""
    if os.environ.get("CHROME"):
        return os.environ["CHROME"]
    if Path(MAC_CHROME).exists():
        return MAC_CHROME
    names = ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser"]
    return next(filter(None, map(shutil.which, names)), None)


def render(chrome, svg_path, png_path, w, h, profile):
    """Screenshot the SVG with headless Chrome; stop Chrome once the PNG has been written."""
    png_path = Path(png_path)
    if png_path.exists():
        png_path.unlink()
    proc = subprocess.Popen(
        [chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--force-device-scale-factor=2",
         "--use-mock-keychain", "--password-store=basic", "--no-first-run", "--no-default-browser-check",
         "--disable-extensions", "--disable-sync", "--disable-background-networking",
         f"--user-data-dir={profile}", f"--window-size={w},{h}", f"--screenshot={png_path}",
         Path(svg_path).resolve().as_uri()],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline, last = time.time() + 120, -1
    while time.time() < deadline:
        if png_path.exists():
            size = png_path.stat().st_size
            if size > 0 and (size == last or proc.poll() is not None):
                break
            last = size
        elif proc.poll() is not None:
            break
        time.sleep(0.5)
    if proc.poll() is None:
        proc.kill()
        proc.wait()
    if not png_path.exists():
        raise RuntimeError(f"rendering failed for {svg_path}")


PANELS = [
    ("clean", "acc_drifted", "(a) S0: drifted situations, accuracy", (0, 1), [0, 0.25, 0.5, 0.75, 1]),
    ("clean", "acc_unknown", "(b) S0: unknown situations, accuracy", (0, 1), [0, 0.25, 0.5, 0.75, 1]),
    ("farmed_forged", "acc_all", "(c) S3: all situations, accuracy", (0, 1), [0, 0.25, 0.5, 0.75, 1]),
    ("farmed_forged", "asr", "(d) S3: attack success rate", (0, 0.5), [0, 0.1, 0.2, 0.3, 0.4, 0.5]),
]
POLICIES = ["sls_adaptive_quorum", "sls_full", "trust_gated", "v1_ungated"]


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    res = json.load(open(sys.argv[1]))
    out = Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    profile = out / ".chrome-profile"
    fig1(out / "fig1_architecture.svg")
    fig2(res, out / "fig2_quorum.svg")
    fig3(res, out / "fig3_dynamics.svg", PANELS, POLICIES)
    fig4(res, out / "fig4_majority.svg", POLICIES)
    sizes = {"fig1_architecture": (1000, 600), "fig2_quorum": (1000, 480), "fig3_dynamics": (1000, 760),
             "fig4_majority": (1000, 470)}
    chrome = find_chrome()
    if chrome is None:
        print("SVG files written; PNG export skipped because Chrome or Chromium was not found (set CHROME).")
        return
    for name, (w, h) in sizes.items():
        render(chrome, out / f"{name}.svg", out / f"{name}.png", w, h, profile)
        print("rendered", out / f"{name}.png")


if __name__ == "__main__":
    main()
