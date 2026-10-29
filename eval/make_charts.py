"""Turn eval/results/grounding_<date>.json into a chart, a markdown table and the Devpost numbers.

    python eval/make_charts.py                      # newest results file -> .png + .md beside it
    python eval/make_charts.py --fill-submission    # also write [N] and [K] into docs/SUBMISSION.md
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / __import__("os").environ.get("FP_RESULTS_DIR", "eval/results")

# Okabe-Ito colour-blind-safe palette
BLUE, ORANGE, VERMILION, GREY, GREEN = "#0072B2", "#E69F00", "#D55E00", "#999999", "#009E73"


def _na(d: dict) -> dict:
    na = d["summary"].get("nemotron_alone")
    if not isinstance(na, dict):
        raise SystemExit("this results file has no Nemotron baseline: run eval/grounding_eval.py with NEBIUS_API_KEY set")
    return na


def sentences(d: dict) -> dict:
    s, na = d["summary"], _na(d)
    n, w, u = s["n"], na["wasted_flights"], na["unsafe_flights"]
    z = na.get("confident_zero_yields", 0)
    head = (f"On {n} field situations, Nemotron alone would have flown the drone in {w} where the crop twin "
            f"says the spray loses money and in {u} where the zone was unsafe or unverified. "
            f"FieldPilot flew in 0 of them.")
    extra = []
    if z:
        extra.append(f"It gave a confident yield for a crop that cannot complete its cycle here in {z} case(s).")
    if na.get("mean_regret_inr_ha") is not None:
        extra.append(f"Its mean regret against the twin's best action was INR {na['mean_regret_inr_ha']:,.0f}/ha.")
    return {"N": str(w), "K": str(u), "headline": head, "detail": " ".join(extra), "model": na.get("model", "")}


def _outcome(row: dict) -> str:
    sc = row["score"]
    if sc.get("unsafe_flight"):
        return "unsafe"
    if sc.get("wasted_flight"):
        return "wasted"
    return "ok"


def chart(d: dict, out: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    rows, na, n = d["rows"], _na(d), d["summary"]["n"]
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(12.5, 5.2), gridspec_kw={"width_ratios": [1, 1.5]})

    labels = ["Wasted flights", "Unsafe / unverified\nflights", "Confident yield for\ninfeasible crop"]
    nem = [na["wasted_flights"], na["unsafe_flights"], na.get("confident_zero_yields", 0)]
    fp = d["summary"].get("fieldpilot", {})
    fpv = [fp.get("wasted_flights", 0), fp.get("unsafe_flights", 0), 0]
    x = range(len(labels))
    a1.bar([i - 0.2 for i in x], nem, 0.38, color=VERMILION, label="Nemotron alone")
    a1.bar([i + 0.2 for i in x], fpv, 0.38, color=BLUE, label="FieldPilot")
    for i, (a, b) in enumerate(zip(nem, fpv)):
        a1.text(i - 0.2, a + 0.15, str(a), ha="center", fontsize=11)
        a1.text(i + 0.2, b + 0.15, str(b), ha="center", fontsize=11)
    a1.set_xticks(list(x), labels, fontsize=9)
    a1.set_ylim(0, max(nem + fpv + [1]) + 2)
    a1.set_ylabel(f"situations (of {n})")
    a1.set_title("Where an ungrounded model goes wrong", fontsize=11, loc="left")
    a1.legend(frameon=False)
    for sp in ("top", "right"):
        a1.spines[sp].set_visible(False)

    cols = 6
    colour = {"ok": GREEN, "wasted": ORANGE, "unsafe": VERMILION}
    name = {"ok": "no harm", "wasted": "wasted flight", "unsafe": "unsafe flight"}
    for i, r in enumerate(rows):
        cx, cy = i % cols, -(i // cols)
        o = _outcome(r)
        a2.add_patch(plt.Rectangle((cx + 0.04, cy + 0.04), 0.92, 0.92, color=colour[o]))
        a2.text(cx + 0.5, cy + 0.62, r["scenario"]["sid"], ha="center", va="center", fontsize=10,
                color="white" if o != "wasted" else "black", fontweight="bold")
        a2.text(cx + 0.5, cy + 0.30, r["scenario"]["label"].replace("nitrogen", "N")[:16], ha="center", va="center", fontsize=6.5,
                color="white" if o != "wasted" else "black")
    a2.set_xlim(0, cols)
    a2.set_ylim(-((len(rows) - 1) // cols), 1)
    a2.axis("off")
    a2.set_title("Nemotron alone, situation by situation", fontsize=11, loc="left")
    handles = [plt.Rectangle((0, 0), 1, 1, color=colour[k]) for k in colour]
    a2.legend(handles, [name[k] for k in colour], loc="lower center", ncol=3, frameon=False,
              bbox_to_anchor=(0.5, -0.12))
    fig.suptitle(f"Grounding evaluation: {na.get('model', 'Nemotron')} alone vs FieldPilot ({n} situations)",
                 fontsize=13, x=0.01, ha="left")
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)


def markdown(d: dict, sen: dict, png: Path) -> str:
    s = d["summary"]
    out = [f"# Grounding evaluation ({s.get('today', '')})", "",
           sen["headline"], "", sen["detail"], "",
           f"Weather: {s.get('weather', '')}", "",
           f"![chart]({png.name})", "",
           "| ID | Crop | Situation | Sev | Twin says | FieldPilot flies | Nemotron alone | Result |",
           "|---|---|---|---|---|---|---|---|"]
    for r in d["rows"]:
        sc, scn = r["scenario"], r["score"]
        res = "UNSAFE FLIGHT" if scn.get("unsafe_flight") else "WASTED FLIGHT" if scn.get("wasted_flight") else "ok"
        if scn.get("confident_zero"):
            res += " + confident yield, infeasible crop"
        nem = r.get("nemotron_alone") or {}
        out.append(f"| {sc['sid']} | {sc['crop']} | {sc['label']} | {sc.get('severity', '')} | "
                   f"{r['fieldpilot']['recommended']} | {'yes' if r['fieldpilot']['fly'] else 'no'} | "
                   f"{nem.get('action', '-')} ({'fly' if nem.get('fly_drone') else 'no fly'}) | {res} |")
    return "\n".join(out) + "\n"


def fill_submission(sen: dict) -> bool:
    p = ROOT / "docs" / "SUBMISSION.md"
    t = p.read_text(encoding="utf-8")
    import re
    new = re.sub(r"(?<!`)\[N\](?!`)", lambda _m: sen["N"], t)
    new = re.sub(r"(?<!`)\[K\](?!`)", lambda _m: sen["K"], new).replace(" *(from eval/results)*", "")
    if new == t:
        return False
    p.write_text(new, encoding="utf-8")
    return True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fill-submission", action="store_true")
    ap.add_argument("--file", help="a specific grounding_*.json (default: newest)")
    a = ap.parse_args(argv)
    src = Path(a.file) if a.file else None
    if src is None:
        files = sorted(RESULTS.glob("grounding_*.json"))
        if not files:
            print("no eval/results/grounding_*.json: run eval/grounding_eval.py first")
            return 2
        src = files[-1]
    d = json.loads(src.read_text(encoding="utf-8"))
    sen = sentences(d)
    png = src.with_suffix(".png")
    chart(d, png)
    src.with_suffix(".md").write_text(markdown(d, sen, png), encoding="utf-8")
    print(sen["headline"])
    print("wrote", png.name, "and", src.with_suffix(".md").name)
    if a.fill_submission and d["summary"].get("mode", {}).get("llm") != "nebius-token-factory":
        print("docs/SUBMISSION.md not touched: " + src.name + " is not a Nebius Token Factory run ("
              + str(d["summary"].get("mode", {}).get("llm")) + ")")
    elif a.fill_submission and "SYNTH" in str(d["summary"].get("weather", "")).upper():
        print("docs/SUBMISSION.md not touched: " + src.name + " ran on SYNTHETIC weather; run "
              "eval/grounding_eval.py again when Open-Meteo answers")
    elif a.fill_submission:
        print("docs/SUBMISSION.md updated" if fill_submission(sen) else "docs/SUBMISSION.md: no [N]/[K] placeholders to fill")
    return 0


if __name__ == "__main__":
    sys.exit(main())
