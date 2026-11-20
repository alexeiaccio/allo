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
a bigger set. Every copied outline then goes through tools/round.py, which cuts
its joins back by the radius and superellipse measured from the master's own H
and n, so the Cyrillic follows ROND the way the Latin does. The corner decision
is made once on a reference Inter instance, so all masters round the same joins
and stay interpolation-compatible.

Known gap for now: no kern pairs.
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
from fontTools.pens.recordingPen import DecomposingRecordingPen, RecordingPen
from round import round_all

from round import corner_flags, round_all
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.ttLib import TTFont
from ufoLib2.objects import Component, Contour, Point

ROOT = Path(__file__).resolve().parent.parent
MASTERS = ROOT / "sources/masters"
STATICS = ROOT / "sources/static"

# the statics are the variable font at Text size, Friendly
STATIC_WEIGHTS = {"Thin": 100, "ExtraLight": 200, "Light": 300, "Regular": 400, "Medium": 500,
                  "SemiBold": 600, "Bold": 700, "ExtraBold": 800, "Black": 900}
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
        raw = DecomposingRecordingPen(gs)
        gs[name].draw(raw)
        rec = RecordingPen()
        for op, pts in quad_to_cubic(raw.value):
            getattr(rec, op)(*pts)
        out[code] = (font["hmtx"][name][0] * sx, _split(rec.value), sx, sy)
    return out


def quad_to_cubic(segs):
    """One cubic for one quadratic, always: Inter's glyf is quadratic, and an
    adaptive converter would give each master a different point count, which
    breaks interpolation. TrueType's implied on-curve points are made explicit."""
    out, cur = [], None
    for op, pts in segs:
        if op in ("moveTo", "lineTo"):
            cur = pts[0]
            out.append((op, pts))
        elif op == "curveTo":
            cur = pts[-1]
            out.append((op, pts))
        elif op == "closePath":
            out.append((op, pts))
        elif op == "qCurveTo":
            controls, end = list(pts[:-1]), pts[-1]
            if end is None:
                raise ValueError("all-quadratic closed contour")
            chain, prev = [], cur
            for i, c in enumerate(controls):
                stop = ((c[0] + controls[i + 1][0]) / 2, (c[1] + controls[i + 1][1]) / 2) \
                    if i + 1 < len(controls) else end
                chain.append((c, stop))
            for c, stop in chain:
                c1 = (prev[0] + 2 / 3 * (c[0] - prev[0]), prev[1] + 2 / 3 * (c[1] - prev[1]))
                c2 = (stop[0] + 2 / 3 * (c[0] - stop[0]), stop[1] + 2 / 3 * (c[1] - stop[1]))
                out.append(("curveTo", (c1, c2, stop)))
                prev = stop
            cur = end
        else:
            raise ValueError(f"unsupported {op}")
    return out


@functools.lru_cache(maxsize=None)
def corner_masks(code):
    """Which joins to round, decided once on the reference instance so every
    master rounds the same ones (at heavy weights some joins sit on the edge of
    the threshold and would flip)."""
    font = inter_at(400, 14)
    gs, cmap = font.getGlyphSet(), font.getBestCmap()
    raw = DecomposingRecordingPen(gs)
    gs[cmap[code]].draw(raw)
    return tuple(tuple(corner_flags(segs)) for segs in _split(quad_to_cubic(raw.value)))


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


def sheared(contours, slant):
    if not slant:
        return contours
    return [[(op, tuple((x + slant * y, y) for x, y in pts)) for op, pts in c] for c in contours]


def _reverse(segs):
    from fontTools.pens.reverseContourPen import ReverseContourPen
    rec = RecordingPen()
    pen = ReverseContourPen(rec)
    for op, pts in segs:
        getattr(pen, op)(*pts)
    return rec.value


def mirror_contours(font, name, adv):
    """The glyph's own contours mirrored left to right. A mirror flips the
    winding, so each contour is reversed back and the fill stays the same."""
    pen = DecomposingRecordingPen(font)
    font[name].draw(pen)
    # mirroring flips the winding of every contour, and since it flips all of
    # them the same way the fill stays right; reversing the points to restore
    # Allo's direction would drop coincident points in some masters and break
    # interpolation, so the flip is left in.
    return [[(op, tuple((adv - x, y) for x, y in pts)) for op, pts in c]
            for c in _split(quad_to_cubic(pen.value))]


def scaled_contours(font, name, sx, sy):
    """The glyph's own contours scaled about the origin, winding kept."""
    pen = DecomposingRecordingPen(font)
    font[name].draw(pen)
    return [[(op, tuple((x * sx, y * sy) for x, y in pts)) for op, pts in c]
            for c in _split(quad_to_cubic(pen.value))]


def polygon(points):
    """A sharp closed contour; round_all then gives it Allo's corners."""
    return [("moveTo", (points[0],))] + [("lineTo", (p,)) for p in points[1:]] + [("closePath", ())]


def fitted(contours, sx, sy, dx=0.0):
    return [[(op, tuple((x * sx + dx, y * sy) for x, y in pts)) for op, pts in c] for c in contours]


# ---------------------------------------------------------------- this master

def crossings(font, name, at):
    """Where the line y = at crosses the glyph outline."""
    from fontTools.misc.bezierTools import calcCubicParameters, solveCubic
    pen = DecomposingRecordingPen(font)
    font[name].draw(pen)
    hits, start, last = [], None, None
    for op, pts in pen.value:
        if op == "moveTo":
            start = last = pts[0]
            continue
        if op in ("closePath", "endPath"):
            pts = (start,)
        if op != "curveTo":
            (x0, y0), (x1, y1) = last, pts[-1]
            pts = ((x0 + (x1 - x0) / 3, y0 + (y1 - y0) / 3),
                   (x0 + 2 * (x1 - x0) / 3, y0 + 2 * (y1 - y0) / 3), pts[-1])
        a, b, c, d = calcCubicParameters(last, *pts)
        for t in solveCubic(a[1], b[1], c[1], d[1] - at):
            if 0 <= t < 1:
                hits.append(((a[0] * t + b[0]) * t + c[0]) * t + d[0])
        last = pts[-1]
    return sorted(hits)


def slant(font):
    """Skew of an oblique master, measured on I's left edge; 0 for uprights.
    Allo's obliques are slanted uprights, not a separate italic drawing, so the
    Cyrillic is slanted by the same amount instead of coming from Inter's italic."""
    if "Oblique" not in Path(font.path).name:
        return 0.0
    cap = font.info.capHeight
    low, high = crossings(font, "I", 0.05 * cap)[0], crossings(font, "I", 0.95 * cap)[0]
    return (high - low) / (0.9 * cap)


def corner(font, name="H"):
    """(radius, k) of a drawn corner: how far it is cut off both edges, and
    where the control points sit inside the join (H's bottom-left corner)."""
    pts = font[name].contours[0].points
    low, left = min(p.y for p in pts), min(p.x for p in pts)
    for i, p in enumerate(pts):
        if p.type != "curve" or p.y != low:
            continue
        c1, c2 = pts[i - 2], pts[i - 1]
        if c1.x == left and c1.x < c2.x:
            r = p.x - c1.x
            if r <= 0:
                return 0.0, 0.5523
            return r, (r - (c1.y - p.y)) / r
    return 0.0, 0.5523


@dataclasses.dataclass
class Metrics:
    font: ufoLib2.Font
    cap: float
    xh: float
    r_u: float
    r_l: float
    k: float
    slant: float
    opsz: float
    wght: float
    rond: float

    def bounds(self, name):
        pen = BoundsPen(self.font)
        self.font[name].draw(pen)
        return pen.bounds

    def width(self, name):
        return self.font[name].width

    def stem(self, name="H", at=None):
        """Width of a stem, measured where the outline clearly crosses it."""
        at = at if at is not None else self.cap * 0.22
        hits = crossings(self.font, name, at)
        return hits[1] - hits[0]

    def lsb(self, name="H"):
        return min(p.x for c in self.font[name].contours for p in c.points)

    def mark(self, base, mark):
        """Where a combining mark sits in a finished Latin glyph. The oblique
        statics carry no components (their accents are drawn in), so their
        offset comes from the upright variable master of the same weight,
        slanted along with everything else."""
        for c in self.font[base].components:
            if c.baseGlyph == mark:
                return c.transformation.dx, c.transformation.dy
        nearest = min((100, 400, 580, 900), key=lambda w: abs(w - self.wght))
        ref = ufoLib2.Font.open(MASTERS / f"Allo-opsz14-wght{nearest}-ROND50.ufo")
        for c in ref[base].components:
            if c.baseGlyph == mark:
                return c.transformation.dx + self.slant * c.transformation.dy, c.transformation.dy
        raise LookupError(f"{base} has no {mark}")


def measure(font):
    vals = {}
    for b in Path(font.path).name.replace("Allo-", "").replace(".ufo", "").split("-"):
        for tag in ("opsz", "wght", "ROND"):
            if b.startswith(tag):
                vals[tag] = float(b[len(tag):])
    if "wght" not in vals:                   # static: named by style, Text size
        style = Path(font.path).name.replace("Allo-", "").replace("Oblique", "").replace(".ufo", "")
        vals = {"wght": STATIC_WEIGHTS[style or "Regular"], "opsz": 14, "ROND": 50}
    r_u, k = corner(font, "H")
    r_l, _ = corner(font, "n")
    return Metrics(font, font.info.capHeight, font.info.xHeight, r_u, r_l, k, slant(font),
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
# И is N mirrored: taking Allo's own N keeps its diagonal-to-stem joins, which
# Inter draws with a small ledge that reads as a step after rounding.
MIRRORED = {0x0418: "N"}
MIRRORED_MARKED = {0x0419: ("N", "Ibreve")}   # the same N plus a breve

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


def _three(m, sx_ratio):
    sx_ratio = sx_ratio
    sy_ratio = m.xh / m.cap
    return (round(m.width("three") * sx_ratio),
            scaled_contours(m.font, "three", sx_ratio, sy_ratio))


def _crossings_of(contours, at):
    f = ufoLib2.Font()
    g = f.newGlyph("probe")
    g.width = 1000
    for s in contours:
        g.contours.append(to_contour(s))
    return crossings(f, "probe", at)


def build(m, chars):
    """Returns {glyph name: (advance, [contours], [(base, dx, dy)])}."""
    g = {}
    todo = []
    for code in chars:
        name = name_of(code)
        if code in SAME_AS_LATIN:
            lat = SAME_AS_LATIN[code]
            g[name] = (m.width(lat), [], [(lat, 0, 0)])
        elif code in MIRRORED:
            lat = MIRRORED[code]
            adv = m.width(lat)
            g[name] = (adv, mirror_contours(m.font, lat, adv), [])
        elif code == 0x043B:                      # л: one outline, slanted left side
            u, S, C = m.lsb("H"), m.stem("H"), m.cap
            adv = m.width("A")
            xr = adv - u
            lean = 0.16 * (xr - u)
            g[name] = (adv, round_all([polygon([
                (u, 0), (u + lean, C), (xr, C), (xr, 0),
                (xr - S, 0), (xr - S, C - S), (u + lean + S, C - S), (u + S, 0),
            ])], m.r_u, m.k), [])
        elif code == 0x0437:                      # з: Allo's three at x-height
            adv, contours = _three(m, 0.95)
            g[name] = (adv, contours, [])
        elif code == 0x044D:                      # э: Allo's c mirrored, plus a bar
            adv = m.width("c")
            u = m.lsb("n")
            hits = crossings(m.font, "o", m.xh / 2)
            S = hits[1] - hits[0]                  # the round letters' stroke weight
            bar = polygon([(u, m.xh / 2 + S / 2), (0.52 * adv, m.xh / 2 + S / 2),
                           (0.52 * adv, m.xh / 2 - S / 2), (u, m.xh / 2 - S / 2)])
            g[name] = (adv, mirror_contours(m.font, "c", adv) + round_all([bar], m.r_l, m.k), [])
        elif code in MIRRORED_MARKED:
            lat, done = MIRRORED_MARKED[code]
            adv = m.width(lat)
            dx, dy = m.mark(done, "brevecomb")
            bw = m.bounds("brevecomb")
            g[name] = (adv, mirror_contours(m.font, lat, adv),
                       [("brevecomb", adv / 2 - (dx + (bw[0] + bw[2]) / 2), dy)])
        elif code in MARKED:
            base, done = MARKED[code]
            dx, dy = m.mark(done, "dieresiscomb")
            g[name] = (m.width(base), [], [(base, 0, 0), ("dieresiscomb", dx, dy)])
        else:
            todo.append(code)
    for code, (adv, contours, sx, sy) in copy_inter(m, todo).items():
        r = m.r_u if code < 0x0430 else m.r_l
        shape = sheared(fitted(contours, sx, sy), m.slant)
        g[name_of(code)] = (round(adv), round_all(shape, r, m.k, corner_masks(code)), [])
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
    """Points for one contour. A contour that closes with a curve comes back
    from the segment pen with a duplicate of its first point at the end; that
    zero-length segment is what puts a cusp at a bowl terminal, so the duplicate
    is dropped and the closing curve becomes the first point's incoming handles."""
    c = Contour()
    pen = SegmentToPointPen(_ContourPen(c))
    for op, pts in segs:
        getattr(pen, op)(*pts)
    points = c.points
    if (len(points) > 3 and points[-1].type == "curve"
            and abs(points[-1].x - points[0].x) < 1e-6
            and abs(points[-1].y - points[0].y) < 1e-6):
        points.pop()
        points[0].type = "curve"
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
    ap.add_argument("--statics", action="store_true", help="work on sources/static instead of the masters")
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
        for path in sorted((STATICS if args.statics else MASTERS).glob("*.ufo")):
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
        folder = STATICS if args.statics else MASTERS
        for path in sorted(folder.glob("*.ufo")):
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
