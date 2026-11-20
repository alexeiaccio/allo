#!/usr/bin/env python3
"""How far Allo's Latin has moved from Inter's, master by master.

Writes build/latin-metrics.md: a summary per master (advance ratios, which are
the same scale the Cyrillic copy uses) and the full per-character table.
Inter has no ROND axis, so each roundness master is compared with the Inter
instance of the same weight and optical size.

    .venv/bin/python tools/latin_metrics.py
"""
import statistics
import sys
from pathlib import Path

import ufoLib2
from fontTools.pens.boundsPen import BoundsPen

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cyrillic import MASTERS, inter_at  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "documentation/latin-metrics.md"
LATIN = list(range(0x41, 0x5B)) + list(range(0x61, 0x7B)) + list(range(0x30, 0x3A))
COLUMNS = ("char name adv_allo adv_inter d_adv d_adv_pct lsb_allo lsb_inter d_lsb "
           "rsb_allo rsb_inter d_rsb ink_w_allo ink_w_inter d_ink_w ink_h_allo ink_h_inter d_ink_h").split()


def axes(path):
    vals = {}
    for part in Path(path).name.replace("Allo-", "").replace(".ufo", "").split("-"):
        for tag in ("opsz", "wght", "ROND"):
            if part.startswith(tag):
                vals[tag] = float(part[len(tag):])
    return vals["opsz"], vals["wght"], vals["ROND"]


def ink(font, name):
    pen = BoundsPen(font)
    font[name].draw(pen)
    return pen.bounds


def row(master, opsz, wght, rond, ch, allo_font, allo_glyph, inter_font):
    gs, cmap = inter_font.getGlyphSet(), inter_font.getBestCmap()
    iname = cmap[ord(ch)]
    ab, ib = ink(allo_font, allo_glyph.name), ink(gs, iname)
    adv_a, adv_i = allo_glyph.width, inter_font["hmtx"][iname][0]
    lsb_a, lsb_i = ab[0], ib[0]
    rsb_a, rsb_i = adv_a - ab[2], adv_i - ib[2]
    iw_a, iw_i = ab[2] - ab[0], ib[2] - ib[0]
    ih_a, ih_i = ab[3] - ab[1], ib[3] - ib[1]
    return [master, opsz, wght, rond, ch, allo_glyph.name, adv_a, adv_i, adv_a - adv_i,
            round(100 * (adv_a - adv_i) / adv_i, 2) if adv_i else 0,
            round(lsb_a, 1), round(lsb_i, 1), round(lsb_a - lsb_i, 1),
            round(rsb_a, 1), round(rsb_i, 1), round(rsb_a - rsb_i, 1),
            round(iw_a, 1), round(iw_i, 1), round(iw_a - iw_i, 1),
            round(ih_a, 1), round(ih_i, 1), round(ih_a - ih_i, 1)]


def main():
    rows, summary = [], []
    for path in sorted(MASTERS.glob("*.ufo")):
        opsz, wght, rond = axes(path)
        allo = ufoLib2.Font.open(path)
        inter = inter_at(wght, opsz)
        cmap = inter.getBestCmap()
        by_char = {g.unicodes[0]: g for g in allo if g.unicodes}
        here = [row(path.name, opsz, wght, rond, chr(c), allo, by_char[c], inter)
                for c in LATIN if c in by_char and c in cmap]
        rows += here
        pct = [r[9] for r in here]
        dlsb = [r[12] for r in here]
        dink = [r[18] for r in here]
        summary.append((path.name, opsz, wght, rond, len(here),
                        statistics.mean(pct), statistics.median(pct), min(pct), max(pct),
                        statistics.mean(dlsb), statistics.mean(dink)))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Allo Latin vs Inter 4.1", "",
             "Inter has no ROND axis, so each roundness master is compared with the Inter",
             "instance of the same wght and opsz (opsz clamps to Inter's 14-32).", "",
             "## Summary per master", "",
             "| master | opsz | wght | ROND | chars | mean adv % | median adv % | min % | max % | mean Δlsb | mean Δink width |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for s in summary:
        lines.append(f"| {s[0]} | {s[1]:g} | {s[2]:g} | {s[3]:g} | {s[4]} | "
                     f"{s[5]:+.2f} % | {s[6]:+.2f} % | {s[7]:+.2f} % | {s[8]:+.2f} % | "
                     f"{s[9]:+.1f} | {s[10]:+.1f} |")
    lines += ["", "## Per character", "",
              "| " + " | ".join(["master", "opsz", "wght", "ROND"] + COLUMNS) + " |",
              "|" + "---|" * (4 + len(COLUMNS))]
    for r in rows:
        lines.append("| " + " | ".join(str(v) for v in r) + " |")
    OUT.write_text("\n".join(lines) + "\n")

    print(f"{len(rows)} rows, {len(summary)} masters -> {OUT.relative_to(ROOT)}")
    for s in summary[:4]:
        print(f"  {s[0]}: mean adv {s[5]:+.2f} %, median {s[6]:+.2f} %, mean Δlsb {s[9]:+.1f}")


if __name__ == "__main__":
    main()
