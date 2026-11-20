#!/usr/bin/env python3
"""Add Cyrillic to Allo's variable masters, one stage at a time.

    .venv/bin/python tools/cyrillic.py --stage ru --preview --weights 400
    .venv/bin/python tools/cyrillic.py --stage ru --write

Where the letters come from:
  * А В Е К М Н О Р С Т Х and а е к о р с х у are the same shapes as Latin, so
    they are components of Allo's own Latin glyphs.
  * Everything else is copied from Inter 4.1, the typeface Allo grew out of
    (see README "Credit"): Inter's Cyrillic is properly drawn, and Allo shares
    Inter's 2048 UPM, cap height and x-height, so the outlines only need a
    small fit to each master's width and height.

Inter has 249 Cyrillic characters, so the later stages are the same copy with
a bigger set. Two known gaps, both deliberate for now:
  * the ROND axis does not move the copied glyphs (same outline at every
    roundness), so Cyrillic stays sharp while Latin rounds;
  * no kern pairs yet.
"""
import argparse
import dataclasses
import functools
import os
import subprocess
import tempfile
from pathlib import Path

import ufoLib2
from fontTools.misc.transform import Transform
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.pointPen import SegmentToPointPen
from fontTools.pens.recordingPen import DecomposingRecordingPen
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.ttLib import TTFont
from ufoLib2.objects import Component, Contour, Point

ROOT = Path(__file__).resolve().parent.parent
MASTERS = ROOT / "sources/masters"
# Inter 4.1's variable font. Fetch it with:
#   curl -L https://github.com/rsms/inter/releases/download/v4.1/Inter-4.1.zip | tar -x -C build
# or point ALLO_INTER at an InterVariable.ttf.
INTER = Path(os.environ.get("ALLO_INTER", ROOT / "build/InterVariable.ttf"))


# ---------------------------------------------------------------- Inter source

@functools.lru_cache(maxsize=None)
def inter_at(wght, opsz):
    """Inter 4.1 instantiated at one weight and optical size."""
    from fontTools.varLib.instancer import instantiateVariableFont
    if not INTER.exists():
        raise SystemExit(f"Inter not found at {INTER}; set ALLO_INTER or drop InterVariable.ttf in build/")
    font = TTFont(INTER)
    axes = {a.axisTag: a for a in font["fvar"].axes}
    opsz = min(max(opsz, axes["opsz"].minValue), axes["opsz"].maxValue)
    return instantiateVariableFont(font, {"wght": wght, "opsz": opsz})


def inter_measures(wght, opsz):
    font = inter_at(wght, opsz)
    gs, cmap = font.getGlyphSet(), font.getBestCmap()

    def box(ch):
        pen = BoundsPen(gs)
        gs[cmap[ord(ch)]].draw(pen)
        return pen.bounds

    return {"cap": box("H")[3], "xh": box("n")[3],
            "adv_H": font["hmtx"][cmap[ord("H")]][0],
            "adv_n": font["hmtx"][cmap[ord("n")]][0]}


def copy_inter(m, chars):
    """Outline, advance and note of each character, fitted to this master."""
    font = inter_at(m.wght, m.opsz)
    gs, cmap = font.getGlyphSet(), font.getBestCmap()
    ref = inter_measures(m.wght, m.opsz)
    fit_u = (m.width("H") / ref["adv_H"], m.cap / ref["cap"])
    # compare like with like: Inter's n ink top overshoots the nominal x-height
    fit_l = (m.width("n") / ref["adv_n"], m.bounds("n")[3] / ref["xh"])
    out = {}
    for code in chars:
        if code not in cmap:
            continue
        name = cmap[code]
        sx, sy = fit_l if code >= 0x0430 else fit_u
        rec = DecomposingRecordingPen(gs)
        gs[name].draw(rec)
        out[code] = (font["hmtx"][name][0] * sx, _split(rec.value), sx, sy)
    return out


def _split(value):
    out, cur = [], []
    for op, pts in value:
        if op == "moveTo":
            if cur:
                out.append(cur)
            cur = [(op, pts)]
        else:
            cur.append((op, pts))
    if cur:
        out.append(cur)
    return out


def fitted(contours, sx, sy, dx=0.0):
    return [[(op, tuple((x * sx + dx, y * sy) for x, y in pts)) for op, pts in c] for c in contours]


# ---------------------------------------------------------------- this master

@dataclasses.dataclass
class Metrics:
    font: ufoLib2.Font
    cap: float
    xh: float
    opsz: float
    wght: float
    rond: float

    def bounds(self, name):
        pen = BoundsPen(self.font)
        self.font[name].draw(pen)
        return pen.bounds

    def width(self, name):
        return self.font[name].width

    def mark(self, base, mark):
        for c in self.font[base].components:
            if c.baseGlyph == mark:
                return c.transformation.dx, c.transformation.dy
        raise LookupError(f"{base} has no {mark}")


def measure(font):
    vals = {}
    for b in Path(font.path).name.replace("Allo-", "").replace(".ufo", "").split("-"):
        for tag in ("opsz", "wght", "ROND"):
            if b.startswith(tag):
                vals[tag] = float(b[len(tag):])
    return Metrics(font, font.info.capHeight, font.info.xHeight,
                   vals["opsz"], vals["wght"], vals["ROND"])


# ---------------------------------------------------------------- letter set
# Same shape as Latin: Allo's own glyph, so Cyrillic matches the family exactly.
# Lowercase к is not here: Allo's Latin k carries its arm above the x-height,
# while Cyrillic к keeps it on the line, so that one comes from Inter.
SAME_AS_LATIN = {
    0x0410: "A", 0x0412: "B", 0x0415: "E", 0x041A: "K", 0x041C: "M", 0x041D: "H",
    0x041E: "O", 0x0420: "P", 0x0421: "C", 0x0422: "T", 0x0425: "X",
    0x0430: "a", 0x0435: "e", 0x043E: "o", 0x0440: "p", 0x0441: "c",
    0x0443: "y", 0x0445: "x",
}
MARKED = {0x0401: ("E", "Edieresis"), 0x0451: ("e", "edieresis")}

RU = [0x0401] + list(range(0x0410, 0x0430)) + list(range(0x0430, 0x0450)) + [0x0451]
SLAV = RU + [0x0400, 0x0402, 0x0403, 0x0404, 0x0405, 0x0406, 0x0407, 0x0408, 0x0409, 0x040A,
             0x040B, 0x040C, 0x040D, 0x040E, 0x040F, 0x0450, 0x0452, 0x0453, 0x0454, 0x0455,
             0x0456, 0x0457, 0x0458, 0x0459, 0x045A, 0x045B, 0x045C, 0x045D, 0x045E, 0x045F,
             0x0490, 0x0491]
EXT = SLAV + [c for c in sorted(inter_at(400, 14).getBestCmap())
              if 0x0400 <= c <= 0x052F and c not in SLAV
              and c not in SAME_AS_LATIN and c not in MARKED]
STAGES = {"ru": RU, "slav": SLAV, "ext": EXT}


def name_of(code):
    return f"uni{code:04X}"


def build(m, chars):
    """Returns {glyph name: (advance, [contours], [(base, dx, dy)])}."""
    g = {}
    todo = []
    for code in chars:
        name = name_of(code)
        if code in SAME_AS_LATIN:
            lat = SAME_AS_LATIN[code]
            g[name] = (m.width(lat), [], [(lat, 0, 0)])
        elif code in MARKED:
            base, done = MARKED[code]
            dx, dy = m.mark(done, "dieresiscomb")
            g[name] = (m.width(base), [], [(base, 0, 0), ("dieresiscomb", dx, dy)])
        else:
            todo.append(code)
    for code, (adv, contours, sx, sy) in copy_inter(m, todo).items():
        g[name_of(code)] = (round(adv), fitted(contours, sx, sy), [])
    return g


# ---------------------------------------------------------------- writing

class _ContourPen:
    """Point pen that collects into a ufoLib2 contour."""

    def __init__(self, contour):
        self.contour = contour

    def beginPath(self, **kwargs):
        self.contour.clear()

    def addPoint(self, pt, segmentType=None, smooth=False, name=None, **kwargs):
        self.contour.append(Point(pt[0], pt[1], type=segmentType, smooth=smooth, name=name))

    def endPath(self):
        pass


def to_contour(segs):
    c = Contour()
    pen = SegmentToPointPen(_ContourPen(c))
    for op, pts in segs:
        getattr(pen, op)(*pts)
    return c


def apply(font, glyphs):
    added = []
    for name, (adv, contours, components) in glyphs.items():
        if name in font:
            continue
        g = font.newGlyph(name)
        g.width = adv
        g.unicodes = [int(name[3:], 16)]
        for segs in contours:
            g.contours.append(to_contour(segs))
        for base, dx, dy in components:
            g.components.append(Component(base, transformation=Transform(1, 0, 0, 1, dx, dy)))
        added.append(name)
    return added


UPPER = [name_of(c) for c in range(0x0410, 0x0430)] + ["uni0401"]
LOWER = [name_of(c) for c in range(0x0430, 0x0450)] + ["uni0451"]


# ---------------------------------------------------------------- preview

def preview(font, names, out, per_row=12, gap=110):
    """Compile the master to OTF and draw the glyphs into a PNG."""
    from ufo2ft import compileOTF

    tt = compileOTF(font, removeOverlaps=False, useProductionNames=False)
    gs = tt.getGlyphSet()
    scale, row_h = 0.075, 2700
    rows, row, x = [], [], 260
    for i, name in enumerate(names):
        pen = BoundsPen(gs)
        gs[name].draw(pen)
        bounds = pen.bounds or (0, 0, 0, 0)
        adv = int(tt["hmtx"][name][0])
        row.append((name, x - bounds[0], x + adv))
        x += adv + gap
        if len(row) == per_row or i == len(names) - 1:
            rows.append(row)
            row, x = [], 0
    width = (max(p[2] for r in rows for p in r) + 200) * scale
    height = len(rows) * row_h * scale
    parts = []
    for ri, r in enumerate(rows):
        base = ri * row_h + 2000
        for name, dx, _ in r:
            spen = SVGPathPen(gs)
            gs[name].draw(spen)
            parts.append(f'<g transform="translate({dx * scale:.2f},{base * scale:.2f}) '
                         f'scale({scale},{-scale})"><path d="{spen.getCommands()}" fill="#111"/></g>')
        parts.append(f'<line x1="0" y1="{base * scale:.2f}" x2="{width:.0f}" y2="{base * scale:.2f}" '
                     f'stroke="#f9a" stroke-width="0.6"/>')
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" height="{height:.0f}" '
           f'viewBox="0 0 {width:.0f} {height:.0f}">'
           f'<rect width="100%" height="100%" fill="white"/>' + "".join(parts) + "</svg>")
    with tempfile.NamedTemporaryFile("w", suffix=".svg", delete=False) as fh:
        fh.write(svg)
        tmp = fh.name
    subprocess.run(["rsvg-convert", "-w", str(int(width * 2)), "-o", str(out), tmp], check=True)
    Path(tmp).unlink()
    return out


def font_names(path):
    return ufoLib2.Font.open(path).keys()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", default="ru", choices=sorted(STAGES))
    ap.add_argument("--write", action="store_true", help="save into every master")
    ap.add_argument("--refresh", action="store_true", help="delete the stage's glyphs first")
    ap.add_argument("--out", default="documentation/latin-metrics.md")
    ap.add_argument("--preview", action="store_true", help="render one master to PNG")
    ap.add_argument("--master", default="Allo-opsz14-wght400-ROND50.ufo")
    ap.add_argument("--weights", default="", help="comma list, e.g. 100,400,900")
    ap.add_argument("--text", default="", help="render this string instead of the alphabet")
    ap.add_argument("--features", action="store_true", help="add the cyrl languagesystem and classes")
    args = ap.parse_args()
    chars = STAGES[args.stage]

    if args.text:
        font = ufoLib2.Font.open(MASTERS / args.master)
        apply(font, build(measure(font), chars))
        from ufo2ft import compileOTF
        cmap = compileOTF(font, removeOverlaps=False, useProductionNames=False).getBestCmap()
        missing = sorted({ch for ch in args.text if ord(ch) not in cmap and ch != " "})
        if missing:
            raise SystemExit(f"not in the font: {''.join(missing)}")
        out = ROOT / "build" / "text.png"
        out.parent.mkdir(parents=True, exist_ok=True)
        print(preview(font, [cmap[ord(ch)] for ch in args.text], out, per_row=len(args.text), gap=0))
    if args.features:
        import unicodedata
        caps = [name_of(c) for c in chars if unicodedata.category(chr(c)) == "Lu"]
        every = [name_of(c) for c in chars]
        for path in sorted(MASTERS.glob("*.ufo")):
            fea = path / "features.fea"
            text = fea.read_text()
            if "languagesystem cyrl" not in text:
                text = text.replace("languagesystem latn dflt;",
                                    "languagesystem latn dflt;\nlanguagesystem cyrl dflt;")
            for cls, names in (("@CAPS = [", caps), ("@NOT_SPACE = [", every), ("@NOT_BRACKET = [", every)):
                i = text.index(cls) + len(cls)
                end = text.index("];", i)
                have = set(text[i:end].split())
                add = [n for n in names if n not in have]
                if add:
                    text = text[:end].rstrip() + " " + " ".join(add) + " " + text[end:]
            fea.write_text(text)
            print(f"{path.name}: features patched")
    if args.preview:
        for w in ([w.strip() for w in args.weights.split(",") if w.strip()] or ["400"]):
            master = args.master.replace("wght400", f"wght{w}")
            font = ufoLib2.Font.open(MASTERS / master)
            apply(font, build(measure(font), chars))
            out = ROOT / "build" / f"cyr-{master.replace('.ufo', '')}.png"
            out.parent.mkdir(parents=True, exist_ok=True)
            print(preview(font, [n for n in UPPER if n in font], out))
            print(preview(font, [n for n in LOWER if n in font], out.with_name(out.stem + "-lower.png")))
    if args.write:
        for path in sorted(MASTERS.glob("*.ufo")):
            font = ufoLib2.Font.open(path)
            if args.refresh:
                for code in chars:
                    if name_of(code) in font:
                        del font[name_of(code)]
            added = apply(font, build(measure(font), chars))
            if added:
                font.save(path, overwrite=True)
            print(f"{path.name}: +{len(added)}")


if __name__ == "__main__":
    main()
