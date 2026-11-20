#!/usr/bin/env python3
"""Bring Inter's Cyrillic kerning into Allo's masters.

Inter's kerning lives in GPOS as class pairs, so the value for a pair is read by
shaping it with HarfBuzz at the matching weight and optical size: the second
glyph's advance minus its plain advance is exactly the kern Inter applies.
Values are scaled by the same width fit the outlines got, and written into the
UFO's kerning for pairs where at least one side is Cyrillic.

    .venv/bin/python tools/kerning.py            # report
    .venv/bin/python tools/kerning.py --write    # into every master

Needs uharfbuzz: .venv/bin/pip install uharfbuzz
"""
import argparse
import sys
import tempfile
from collections import Counter
from pathlib import Path

import ufoLib2

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cyrillic import MASTERS, STAGES, STATICS, inter_at, measure, name_of  # noqa: E402

# letters on both sides, plus the punctuation that actually meets them
PUNCT = list(".,:;!?()[]{}\"'«»-–—…/")
PAIRS_SIDE = STAGES["ru"]


def kerning_of(tt, pairs, cmap):
    """{pair of characters: kern} as HarfBuzz shapes each pair."""
    import uharfbuzz as hb

    with tempfile.NamedTemporaryFile(suffix=".ttf", delete=False) as fh:
        tt.save(fh.name)
        blob = hb.Blob.from_file_path(fh.name)
    Path(fh.name).unlink()
    face = hb.Face(blob)
    font = hb.Font(face)
    out = {}
    upm = tt["head"].unitsPerEm
    hb.ot_font_set_funcs(font)
    for a, b in pairs:
        buf = hb.Buffer()
        buf.add_str(a + b)
        buf.guess_segment_properties()
        hb.shape(font, buf)
        pos = buf.glyph_positions
        if len(pos) < 2:
            continue
        # HarfBuzz puts the pair adjustment on the first glyph's advance, so
        # read the run's total against the unkerned sum
        plain = tt["hmtx"][cmap[ord(a)]][0] + tt["hmtx"][cmap[ord(b)]][0]
        out[(a, b)] = round(sum(p.x_advance for p in pos) - plain)
    del font
    return out


def pairs():
    letters = [chr(c) for c in PAIRS_SIDE if chr(c).isalpha()]
    out = []
    for a in letters:
        for b in letters + PUNCT:
            out.append((a, b))
        for b in PUNCT:
            out.append((b, a))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--stage", default="ru", choices=sorted(STAGES))
    ap.add_argument("--statics", action="store_true", help="sources/static instead of the masters")
    args = ap.parse_args()
    todo = pairs()
    print(f"{len(todo)} pairs per master")

    total = Counter()
    for path in sorted((STATICS if args.statics else MASTERS).glob("*.ufo")):
        ufo = ufoLib2.Font.open(path)
        m = measure(ufo)
        tt = inter_at(m.wght, m.opsz)
        cmap = tt.getBestCmap()
        sx = m.width("H") / tt["hmtx"][cmap[ord("H")]][0]
        kern = kerning_of(tt, todo, cmap)
        scaled = {(name_of(ord(a)), name_of(ord(b))): round(v * sx)
                  for (a, b), v in kern.items() if abs(v * sx) >= 1}
        print(f"{path.name}: {len(scaled)} pairs, "
              f"range {min(scaled.values(), default=0)}..{max(scaled.values(), default=0)}")
        total.update(scaled.keys())
        if args.write:
            ufo.kerning.update(scaled)
            ufo.save(path, overwrite=True)

    if not args.write:
        print("most frequent pairs:", total.most_common(5))


if __name__ == "__main__":
    main()
