#!/usr/bin/env python3
"""Is the Cyrillic consistent across all of Allo's masters?

Variable interpolation needs every master to describe a glyph with the same
contours, the same points in the same order, and the same components. This
checks each stage character against every master and prints what differs.

    .venv/bin/python tools/check_cyrillic.py
    .venv/bin/python tools/check_cyrillic.py --stage slav
"""
import argparse
import sys
from pathlib import Path

import ufoLib2

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cyrillic import MASTERS, STAGES, name_of  # noqa: E402


def signature(font, name):
    if name not in font:
        return "missing"
    g = font[name]
    # smooth is UI metadata and component offsets interpolate on their own,
    # so neither takes part in the structure check
    contours = tuple(tuple(p.type for p in c.points) for c in g.contours)
    return contours, tuple(c.baseGlyph for c in g.components)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", default="ext", choices=sorted(STAGES))
    args = ap.parse_args()
    chars = STAGES[args.stage]

    masters = [ufoLib2.Font.open(p) for p in sorted(MASTERS.glob("*.ufo"))]
    print(f"{len(masters)} masters, {len(chars)} characters, stage {args.stage}")

    problems, unicode_bad, points = 0, 0, []
    for code in chars:
        name = name_of(code)
        sigs = {}
        for font in masters:
            sig = signature(font, name)
            sigs.setdefault(str(sig), []).append(Path(font.path).name)
        if "missing" in sigs:
            problems += 1
            print(f"  {name}: missing in {len(sigs['missing'])} masters")
        elif len(sigs) > 1:
            problems += 1
            for sig, names in sigs.items():
                print(f"  {name}: {len(names)} masters with {sig[:120]}")
        if not any(code in font[name].unicodes for font in masters if name in font):
            unicode_bad += 1
            print(f"  {name}: no U+{code:04X} unicode")
        counts = {sum(len(c.points) for c in font[name].contours)
                  for font in masters if name in font}
        points.append((name, min(counts), max(counts)))

    print(f"\n{'ok' if not problems and not unicode_bad else 'FAIL'}: "
          f"{problems} inconsistent glyphs, {unicode_bad} glyphs without unicode")
    widths = [p[2] - p[1] for p in points if p[1] != p[2]]
    print(f"point counts per glyph identical across masters: {not widths}")
    if widths:
        print(f"  {len(widths)} glyphs differ, e.g. {[p for p in points if p[1] != p[2]][:5]}")
    return 1 if problems or unicode_bad else 0


if __name__ == "__main__":
    sys.exit(main())
