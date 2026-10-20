"""Turn eval results into two decision charts and a table.

    python evals/analyze.py

1. Threshold sweep: what the escalation threshold buys (cost, escalation rate, routing outcomes),
   with the single-model baselines as reference lines.
2. Calibration: stated field confidence vs. observed accuracy, per model, from every stored extraction.
Writes docs/evals/threshold_sweep.png, docs/evals/calibration.png and docs/evals/results.md.
"""
from __future__ import annotations

import glob
import json
import re
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
RES, OUT = ROOT / "evals/results", ROOT / "docs/evals"
INTAKE_DIRS = [ROOT / ".local_store/eval_intakes", ROOT / ".local_store/eval_intakes_old"]  # extraction reads do not depend on the rules, so older runs add calibration data


def intake_file(iid):
    return next((d / f"{iid}.json" for d in INTAKE_DIRS if (d / f"{iid}.json").exists()), None)
# Validated default categorical palette (dataviz skill), fixed order; text in ink, not series color.
BLUE, ORANGE = "#2a78d6", "#eb6834"
SURFACE, INK, INK2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e6e5e1"


def load(path):
    rows = [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]
    return [r for r in rows if "error" not in r]


def summarize(rows):
    n = len(rows)
    return {"n": n,
            "routing": sum(r["route_ok"] for r in rows) / n,
            "false_auto": sum(r["false_auto_accept"] for r in rows),
            "false_return": sum(r.get("false_return", False) for r in rows),
            "unneeded_review": sum(r.get("unneeded_review", False) for r in rows),
            "escalation": sum(r["escalated"] for r in rows) / n,
            "cost": sum(r["cost"] or 0 for r in rows) / n,
            "p50": sorted(r["ms"] for r in rows)[n // 2] / 1000}


def style(ax, title):
    ax.set_facecolor(SURFACE)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(GRID)
    ax.tick_params(colors=INK2, labelsize=10)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.set_title(title, loc="left", fontsize=12, color=INK, fontweight="bold", pad=10)


def sweep():
    cfg = {}
    for p in glob.glob(str(RES / "cascade t=*.jsonl")):
        t = float(re.search(r"t=([0-9]+\.[0-9]+)", p).group(1))
        cfg[t] = summarize(load(p))
    base = {k: summarize(load(RES / f"{k}.jsonl")) for k in ("luna only", "sol only") if (RES / f"{k}.jsonl").exists()}
    ts = sorted(cfg)
    fig, axes = plt.subplots(1, 3, figsize=(15, 4.6), facecolor=SURFACE)
    # 1 cost
    ax = axes[0]
    style(ax, "OpenAI cost per claim")
    ax.plot(ts, [cfg[t]["cost"] * 100 for t in ts], color=BLUE, linewidth=2, marker="o", markersize=8,
            markeredgecolor=SURFACE, markeredgewidth=2)
    for k, b in base.items():
        ax.axhline(b["cost"] * 100, color=INK2, linewidth=1, linestyle=(0, (4, 3)))
        ax.annotate(f"{k}: {b['cost']*100:.2f}¢", (ts[0], b["cost"] * 100), xytext=(0, 4), textcoords="offset points",
                    fontsize=9.5, color=INK2)
    ax.set_ylabel("cents per claim", color=INK2)
    ax.set_ylim(0, max([b["cost"] for b in base.values()] + [cfg[t]["cost"] for t in ts]) * 100 * 1.2)
    # 2 escalation
    ax = axes[1]
    style(ax, "Claims escalated to gpt-6.1-sol")
    ax.plot(ts, [cfg[t]["escalation"] * 100 for t in ts], color=BLUE, linewidth=2, marker="o", markersize=8,
            markeredgecolor=SURFACE, markeredgewidth=2)
    for t in ts:
        ax.annotate(f"{cfg[t]['escalation']*100:.0f}%", (t, cfg[t]["escalation"] * 100), xytext=(0, 8),
                    textcoords="offset points", ha="center", fontsize=9.5, color=INK2)
    ax.set_ylabel("% of claims", color=INK2)
    ax.set_ylim(0, 100)
    # 3 routing outcomes
    ax = axes[2]
    style(ax, "Routing errors per 36 runs")
    unsafe = [cfg[t]["false_auto"] + cfg[t]["false_return"] for t in ts]
    extra = [cfg[t]["unneeded_review"] for t in ts]
    ax.plot(ts, unsafe, color=BLUE, linewidth=2, marker="o", markersize=8, markeredgecolor=SURFACE,
            markeredgewidth=2, label="wrong auto-accept or return")
    ax.plot(ts, extra, color=ORANGE, linewidth=2, marker="s", markersize=8, markeredgecolor=SURFACE,
            markeredgewidth=2, label="unneeded review (safe)")
    ax.annotate("wrong accept/return", (ts[-1], unsafe[-1]), xytext=(6, 6), textcoords="offset points", fontsize=9.5, color=INK2)
    ax.annotate("unneeded review", (ts[-1], extra[-1]), xytext=(6, -12), textcoords="offset points", fontsize=9.5, color=INK2)
    ax.set_ylim(-0.3, max(unsafe + extra + [3]) + 1)
    ax.yaxis.get_major_locator().set_params(integer=True)
    ax.legend(frameon=False, fontsize=9.5, loc="upper left", labelcolor=INK2)
    for ax in axes:
        ax.set_xticks(ts)
        ax.set_xlabel("escalate when any field confidence is below", color=INK2)
    fig.suptitle("The escalation threshold barely changes cost or safety between 0.70 and 0.95 (cascade, 18 claims × 2 runs)", x=0.01, ha="left",
                 fontsize=13, color=INK, fontweight="bold")
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    fig.savefig(OUT / "threshold_sweep.png", dpi=180, facecolor=SURFACE)
    return cfg, base


FIELD_MAP = {"insured_id": "insured_id", "patient_last_name": "patient_last_name", "patient_first_name": "patient_first_name",
             "patient_birth_date": "patient_birth_date", "insured_last_name": "insured_last_name",
             "total_charge": "total_charge", "billing_provider_npi": "billing_npi", "federal_tax_id": "federal_tax_id",
             "patient_account_no": "patient_account_no"}


def norm(v):
    return None if v is None else str(v).strip().upper().replace(" ", "").replace(",", "").replace("$", "")


def calibration():
    labels = {l["file"].split("/")[-1]: l["printed"] for l in json.loads((ROOT / "data/labels.json").read_text())}
    pts = defaultdict(list)  # model -> [(confidence, correct)]
    seen = set()
    for p in glob.glob(str(RES / "*.jsonl")) + glob.glob(str(RES / "archive/*/*.jsonl")):
        for r in load(p):
            f = intake_file(r["intake_id"]) if r.get("intake_id") else None
            if f is None or r["intake_id"] in seen:
                continue
            seen.add(r["intake_id"])
            it = json.loads(f.read_text())
            lab = labels.get(it["filename"])
            if not lab:
                continue
            reads = []
            casc = it.get("cascade") or {}
            final_model = casc.get("final_model") or (it.get("usage", {}).get("extraction", {}) or {}).get("model")
            if casc.get("first_pass"):
                reads.append((casc["first_model"], casc["first_pass"]))
            reads.append((final_model, it.get("extraction") or {}))
            for model, x in reads:
                for f, lk in FIELD_MAP.items():
                    want, got = lab.get(lk), (x.get(f) or {})
                    if want is None or got.get("value") is None:
                        continue
                    ok = (f"{float(norm(got['value'])):.2f}" == f"{float(want):.2f}") if f == "total_charge" else norm(got["value"]) == norm(want)
                    pts[model].append((got.get("confidence", 0), ok))
    bins = [0, 0.5, 0.7, 0.85, 0.9, 0.95, 0.99, 1.0001]
    fig, ax = plt.subplots(figsize=(7.5, 5.2), facecolor=SURFACE)
    style(ax, "Stated confidence vs. observed accuracy (per field read)")
    ax.plot([0, 1], [0, 1], color=GRID, linewidth=1.5, linestyle=(0, (4, 3)))
    ax.annotate("perfectly calibrated", (0.22, 0.27), color=INK2, fontsize=9.5, rotation=36)
    stats = {}
    for model, color, marker in (("gpt-6-luna", BLUE, "o"), ("gpt-6.1-sol", ORANGE, "s")):
        xs = pts.get(model, [])
        if not xs:
            continue
        mx, my, ns = [], [], []
        for lo, hi in zip(bins, bins[1:]):
            b = [ok for c, ok in xs if lo <= c < hi]
            if len(b) >= 8:
                mx.append(sum(c for c, ok in xs if lo <= c < hi) / len(b))
                my.append(sum(b) / len(b))
                ns.append(len(b))
        ax.plot(mx, my, color=color, linewidth=2, marker=marker, markersize=8, markeredgecolor=SURFACE,
                markeredgewidth=2, label=model)
        for x_, y_, n_ in zip(mx, my, ns):
            if x_ >= 0.9:
                continue
            ax.annotate(f"n={n_}", (x_, y_), xytext=(-8 if model.endswith("sol") else 6, -14 if model.endswith("sol") else 6),
                        textcoords="offset points", fontsize=8.5, color=INK2)
        hi_conf = [ok for c, ok in xs if c >= 0.9]
        stats[model] = {"reads": len(xs), "accuracy": sum(ok for _, ok in xs) / len(xs),
                        "high_conf_reads": len(hi_conf), "high_conf_errors": sum(1 for ok in hi_conf if not ok)}
    ax.set_xlim(0, 1.02)
    ax.set_ylim(0, 1.02)
    ax.set_xlabel("stated confidence (bin mean)", color=INK2)
    ax.set_ylabel("share of reads that were correct", color=INK2)
    ax.legend(frameon=False, fontsize=10, loc="upper left", labelcolor=INK2)
    ax.text(0.02, 0.80, "Below 0.90, reads are wrong far more often than stated:\na useful trigger to escalate. Above 0.90, a few confident\nerrors remain, so code still verifies against FHIR.",
            transform=ax.transAxes, fontsize=9.5, color=INK2, va="top")
    note = "  ·  ".join(f"{m}: {s['high_conf_errors']} wrong of {s['high_conf_reads']} reads at ≥0.90" for m, s in stats.items())
    fig.text(0.01, 0.01, note, fontsize=9, color=INK2)
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    fig.savefig(OUT / "calibration.png", dpi=180, facecolor=SURFACE)
    return stats


PRICES = {"gpt-6-luna": (0.10, 0.01, 0.50), "gpt-6.1-sol": (2.00, 0.10, 10.00)}  # USD per 1M tokens: input, cached, output


def _cost(u, cached=None):
    pin, pc, po = PRICES[u["model"]]
    c = u["cached_tokens"] if cached is None else cached
    return ((u["input_tokens"] - c) * pin + c * pc + u["output_tokens"] * po) / 1e6


def caching():
    """Prompt caching, honestly: the evals re-run the same scans, so repeats are served almost entirely from cache.
    The first time a scan is seen ('cold') is the production-like case: only the fixed instructions are cacheable."""
    rows = []
    for p in glob.glob(str(RES / "cascade t=*.jsonl")):
        for r in load(p):
            f = intake_file(r["intake_id"])
            if f:
                it = json.loads(f.read_text())
                rows.append((it["created_at"], r["claim_id"], it))
    rows.sort(key=lambda x: x[0])
    first = {}
    for _, cid, it in rows:
        first.setdefault(cid, it)
    groups = {"cold": list(first.values()), "warm": [it for _, cid, it in rows if it is not first[cid]]}
    out = {}
    for name, its in groups.items():
        act = sum(_cost(u) for it in its for u in it["usage"].values())
        raw = sum(_cost(u, 0) for it in its for u in it["usage"].values())
        share = defaultdict(lambda: [0, 0])
        for it in its:
            for k, u in it["usage"].items():
                share[k][0] += u["cached_tokens"]
                share[k][1] += u["input_tokens"]
        out[name] = {"n": len(its), "cents": act / len(its) * 100, "cents_uncached": raw / len(its) * 100,
                     "saving": 1 - act / raw, "share": {k: c / i for k, (c, i) in share.items()}}
    return out


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    cfg, base = sweep()
    stats = calibration()
    lines = ["# Eval results (18 synthetic claims × 2 runs per configuration)", "",
             "| Configuration | Routing | Wrong auto-accepts | Wrong returns | Unneeded reviews | Escalated | ¢ / claim | p50 s |",
             "|---|---|---|---|---|---|---|---|"]
    rows = [(f"cascade, threshold {t:.2f}", s) for t, s in sorted(cfg.items())] + list(base.items())
    for name, s in rows:
        lines.append(f"| {name} | {s['routing']:.0%} | {s['false_auto']} | {s['false_return']} | {s['unneeded_review']} | "
                     f"{s['escalation']:.0%} | {s['cost']*100:.2f} | {s['p50']:.1f} |")
    lines += ["", "## Calibration (all stored extractions)", "", "| Model | Field reads | Accuracy | Reads at ≥0.90 confidence | Wrong at ≥0.90 |",
              "|---|---|---|---|---|"]
    for m, s in stats.items():
        lines.append(f"| {m} | {s['reads']} | {s['accuracy']:.1%} | {s['high_conf_reads']} | {s['high_conf_errors']} |")
    cc = caching()
    lines += ["", "## Prompt caching (cascade runs; cost with the real cached-token counts vs. the same tokens uncached)", "",
              "| Scan seen | Claims | ¢ / claim | ¢ / claim without caching | Saving | Cached share of input |", "|---|---|---|---|---|---|"]
    for name, label in (("cold", "first time (production-like)"), ("warm", "repeat (eval re-runs)")):
        c = cc[name]
        lines.append(f"| {label} | {c['n']} | {c['cents']:.2f} | {c['cents_uncached']:.2f} | {c['saving']:.0%} | "
                     + ", ".join(f"{k} {v:.0%}" for k, v in sorted(c["share"].items())) + " |")
    (OUT / "results.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
