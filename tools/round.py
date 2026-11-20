#!/usr/bin/env python3
"""Round the corners of a contour the way Allo draws its own letters.

Allo has no sharp joins: every corner is cut back by a radius r and bridged by
a superellipse whose control points sit k*r inside the join (H's corner is the
reference). This module takes fontTools segment-pen commands and returns the
same contour with every corner treated that way, which is how Inter's Cyrillic
outlines are brought into Allo's drawing.

The point count depends only on how many corners the contour has, never on r,
so the same glyph in different masters stays interpolation-compatible even
where one master is sharp (ROND 0).

    .venv/bin/python tools/round.py        # self-check on a square and an L
"""
import math

MIN_TURN = math.radians(35)      # ignore joins this shallow: they read as curves
MIN_RADIUS = 1.0                 # keep a point structure even where r is 0


def _unit(a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    d = math.hypot(dx, dy) or 1.0
    return dx / d, dy / d


def nodes(segs):
    """Contour as nodes: on-curve point plus the two cubic handles around it."""
    out = []
    for op, pts in segs:
        if op == "moveTo":
            out.append({"p": pts[0], "in": None, "out": None})
        elif op == "lineTo":
            out.append({"p": pts[0], "in": None, "out": None})
        elif op == "curveTo":
            c1, c2, p = pts
            out[-1]["out"] = c1
            out.append({"p": p, "in": c2, "out": None})
        elif op == "closePath":
            pass
        else:
            raise ValueError(f"unsupported {op}")
    return out


def segments(ns):
    """Back to segment commands. Starts at the first node, which depends only on
    the contour's structure, never on the radius: otherwise one master would
    close with a line and another with a curve, and the point counts would
    disagree across masters."""
    def emit(a, b, out):
        if a["out"] is not None or b["in"] is not None:
            c1 = a["out"] if a["out"] is not None else a["p"]
            c2 = b["in"] if b["in"] is not None else b["p"]
            out.append(("curveTo", (tuple(c1), tuple(c2), tuple(b["p"]))))
        else:
            out.append(("lineTo", (tuple(b["p"]),)))

    out = [("moveTo", (tuple(ns[0]["p"]),))]
    for a, b in zip(ns, ns[1:]):
        emit(a, b, out)
    if ns[-1]["out"] is not None or ns[0]["in"] is not None:
        emit(ns[-1], ns[0], out)
    out.append(("closePath", ()))
    return out


def corner_radius(ns, i):
    """How far this vertex can be cut back without eating its neighbours. Stops
    just short of half an edge: exactly half lets the two cut points of facing
    corners coincide, and a zero-length segment gets dropped by the point pen,
    which would leave the masters with different point counts."""
    a, v, b = ns[i - 1]["p"], ns[i]["p"], ns[(i + 1) % len(ns)]["p"]
    return min(math.dist(a, v), math.dist(v, b)) * 0.49


def _cubic(seg, t):
    p0, c1, c2, p3 = seg
    mt = 1 - t
    return (mt ** 3 * p0[0] + 3 * mt * mt * t * c1[0] + 3 * mt * t * t * c2[0] + t ** 3 * p3[0],
            mt ** 3 * p0[1] + 3 * mt * mt * t * c1[1] + 3 * mt * t * t * c2[1] + t ** 3 * p3[1])


def _tangent(seg, t):
    p0, c1, c2, p3 = seg
    mt = 1 - t
    dx = 3 * mt * mt * (c1[0] - p0[0]) + 6 * mt * t * (c2[0] - c1[0]) + 3 * t * t * (p3[0] - c2[0])
    dy = 3 * mt * mt * (c1[1] - p0[1]) + 6 * mt * t * (c2[1] - c1[1]) + 3 * t * t * (p3[1] - c2[1])
    d = math.hypot(dx, dy) or 1.0
    return dx / d, dy / d


def _seg(a, b):
    """The segment from node a to node b as a cubic and whether it is a line."""
    is_line = a["out"] is None and b["in"] is None
    c1 = a["out"] if a["out"] is not None else ((2 * a["p"][0] + b["p"][0]) / 3,
                                                (2 * a["p"][1] + b["p"][1]) / 3)
    c2 = b["in"] if b["in"] is not None else ((a["p"][0] + 2 * b["p"][0]) / 3,
                                              (a["p"][1] + 2 * b["p"][1]) / 3)
    return (a["p"], c1, c2, b["p"]), is_line


def _length(seg, is_line, samples=24):
    if is_line:
        return math.dist(seg[0], seg[3])
    pts = [_cubic(seg, i / samples) for i in range(samples + 1)]
    return sum(math.dist(pts[i], pts[i + 1]) for i in range(samples))


def _cut(seg, is_line, dist, from_end, samples=24):
    """Point at arc distance dist from one end of the segment, and its tangent."""
    if is_line:
        p0, _, _, p3 = seg
        d = _unit(p0, p3) if not from_end else _unit(p3, p0)
        base = p3 if from_end else p0
        return (base[0] + d[0] * dist, base[1] + d[1] * dist), (_unit(p3, p0) if from_end else _unit(p0, p3))
    order = range(samples - 1, -1, -1) if from_end else range(1, samples + 1)
    prev_t = 1.0 if from_end else 0.0
    acc = 0.0
    for i in order:
        t = i / samples
        step = math.dist(_cubic(seg, prev_t), _cubic(seg, t))
        if acc + step >= dist:
            point = _cubic(seg, t)
            tangent = _tangent(seg, t)
            return point, (tangent if not from_end else (-tangent[0], -tangent[1]))
        acc += step
        prev_t = t
    end = seg[3] if from_end else seg[0]
    tangent = _tangent(seg, 1.0 if from_end else 0.0)
    return end, (tangent if not from_end else (-tangent[0], -tangent[1]))


def _area(ns):
    a = 0.0
    for i, node in enumerate(ns):
        j = (i + 1) % len(ns)
        a += node["p"][0] * ns[j]["p"][1] - ns[j]["p"][0] * node["p"][1]
    return a / 2


def corner_flags(segs, min_turn=MIN_TURN):
    """One flag per node: round this join? Measured on the real tangents, and
    only where the join is convex for the region its contour encloses. Allo
    leaves the inner joins sharp (b, B, n) and rounds the outside corners and
    the corners of a counter, so a cut that would eat ink never happens."""
    ns = nodes(segs)
    sign = 1.0 if _area(ns) > 0 else -1.0
    flags = []
    for i, node in enumerate(ns):
        seg_in, _ = _seg(ns[i - 1], node)
        seg_out, _ = _seg(node, ns[(i + 1) % len(ns)])
        d_in = _tangent(seg_in, 1.0)
        d_out = _tangent(seg_out, 0.0)
        turn = math.acos(max(-1.0, min(1.0, d_in[0] * d_out[0] + d_in[1] * d_out[1])))
        cross = d_in[0] * d_out[1] - d_in[1] * d_out[0]
        flags.append(turn >= min_turn and cross * sign > 0)
    return flags


def round_contour(segs, r, k, flags=None, min_turn=MIN_TURN):
    """Cut every corner back by r and bridge it with a k-superellipse. With
    `flags`, the decision comes from outside, so every master rounds the same
    joins even where its own outline sits on the edge of the threshold."""
    ns = nodes(segs)
    if flags is None:
        flags = corner_flags(segs, min_turn)
    if len(flags) != len(ns):
        raise ValueError(f"corner mask has {len(flags)} flags for {len(ns)} nodes")
    out = []
    n = len(ns)
    for i, node in enumerate(ns):
        if not flags[i]:
            out.append(dict(node))
            continue
        prev, nxt = ns[i - 1], ns[(i + 1) % n]
        seg_in, line_in = _seg(prev, node)
        seg_out, line_out = _seg(node, nxt)
        len_in, len_out = _length(seg_in, line_in), _length(seg_out, line_out)
        radius = min(max(r, MIN_RADIUS), 0.45 * min(len_in, len_out))
        start, dir_in = _cut(seg_in, line_in, radius, from_end=True)
        end, dir_out = _cut(seg_out, line_out, radius, from_end=False)
        # the incoming segment now ends at the cut point, keeping its tangent
        h_in = node["in"]
        if h_in is not None:
            keep = min(math.dist(h_in, node["p"]), max(0.0, len_in - radius))
            h_in = (start[0] - dir_in[0] * keep, start[1] - dir_in[1] * keep)
        h_out = node["out"]
        if h_out is not None:
            keep = min(math.dist(h_out, node["p"]), max(0.0, len_out - radius))
            h_out = (end[0] + dir_out[0] * keep, end[1] + dir_out[1] * keep)
        before = {"p": start, "in": h_in,
                  "out": (start[0] + dir_in[0] * k * radius, start[1] + dir_in[1] * k * radius)}
        after = {"p": end, "in": (end[0] - dir_out[0] * k * radius, end[1] - dir_out[1] * k * radius),
                 "out": h_out}
        out += [before, after]
    return segments(out)


def round_all(contours, r, k, masks=None):
    if masks is None:
        return [round_contour(segs, r, k) for segs in contours]
    if len(masks) != len(contours):
        raise ValueError(f"mask has {len(masks)} contours for {len(contours)}")
    return [round_contour(segs, r, k, flags) for segs, flags in zip(contours, masks)]


def _selfcheck():
    """A square gets four rounded corners; a straight line gets none."""
    square = [("moveTo", ((0, 0),)), ("lineTo", ((1000, 0),)), ("lineTo", ((1000, 1000),)),
              ("lineTo", ((0, 1000),)), ("closePath", ())]
    got = round_contour(square, 100, 0.7387)
    curves = [s for s in got if s[0] == "curveTo"]
    assert len(curves) == 4, f"expected 4 corners, got {len(curves)}"
    on = [s for s in got if s[0] in ("moveTo", "lineTo", "curveTo")]
    assert len(on) == 8, f"expected 8 on-curve points, got {len(on)}"
    # a corner is cut back by exactly r along both edges
    pts = [s[1][-1] for s in on]
    assert (100.0, 0.0) in pts and (900.0, 0.0) in pts, pts
    # sharp master: same structure, no visible rounding
    sharp = round_contour(square, 0.0, 0.7387)
    assert len(sharp) == len(got), "point structure must not depend on the radius"
    # a shallow join stays a point instead of becoming a rounded corner
    shallow = [("moveTo", ((0, 0),)), ("lineTo", ((1000, 0),)), ("lineTo", ((2000, 20),)),
               ("lineTo", ((2000, 1000),)), ("lineTo", ((0, 1000),)), ("closePath", ())]
    got = round_contour(shallow, 100, 0.7387)
    on = [s[1][-1] for s in got if s[0] in ("moveTo", "lineTo", "curveTo")]
    assert (1000.0, 0.0) in on, on
    assert len([s for s in got if s[0] == "curveTo"]) == 4, "four 90-degree joins round, the shallow one does not"
    print("round.py self-check ok")


if __name__ == "__main__":
    _selfcheck()
