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
FLAT = 1.5                       # leave this much of an edge between two cuts


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


def _param_at(seg, is_line, dist, from_end, samples=32):
    """Parameter at which the segment has covered `dist` from one end."""
    if is_line:
        total = math.dist(seg[0], seg[3]) or 1.0
        t = min(1.0, dist / total)
        return 1.0 - t if from_end else t
    order = range(samples - 1, -1, -1) if from_end else range(1, samples + 1)
    prev_t, acc = (1.0 if from_end else 0.0), 0.0
    for i in order:
        t = i / samples
        step = math.dist(_cubic(seg, prev_t), _cubic(seg, t))
        if acc + step >= dist:
            frac = (dist - acc) / step if step else 0.0
            return prev_t + (t - prev_t) * frac
        acc += step
        prev_t = t
    return 1.0 if from_end else 0.0


def _split(seg, t):
    """de Casteljau: the two exact halves of a cubic at t."""
    p0, c1, c2, p3 = seg
    a = ((p0[0] + (c1[0] - p0[0]) * t, p0[1] + (c1[1] - p0[1]) * t))
    b = ((c1[0] + (c2[0] - c1[0]) * t, c1[1] + (c2[1] - c1[1]) * t))
    c = ((c2[0] + (p3[0] - c2[0]) * t, c2[1] + (p3[1] - c2[1]) * t))
    d = ((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
    e = ((b[0] + (c[0] - b[0]) * t, b[1] + (c[1] - b[1]) * t))
    f = ((d[0] + (e[0] - d[0]) * t, d[1] + (e[1] - d[1]) * t))
    return (p0, a, d, f), (f, e, c, p3)


def _area(ns):
    a = 0.0
    for i, node in enumerate(ns):
        j = (i + 1) % len(ns)
        a += node["p"][0] * ns[j]["p"][1] - ns[j]["p"][0] * node["p"][1]
    return a / 2


def _samples(ns, per_seg=6):
    """Dense points along the contour, for a local thickness estimate."""
    pts = []
    n = len(ns)
    for i in range(n):
        seg, line = _seg(ns[i], ns[(i + 1) % n])
        if line:
            a, b = seg[0], seg[3]
            pts += [(a[0] + (b[0] - a[0]) * t / per_seg, a[1] + (b[1] - a[1]) * t / per_seg)
                    for t in range(per_seg)]
        else:
            pts += [_cubic(seg, t / per_seg) for t in range(per_seg)]
    return pts


def _thickness(pts, node_xy, skip):
    """Nearest bit of outline that is not one of the edges leaving the corner:
    the local width of the stroke, which caps how much a cut may take."""
    best = float("inf")
    for x, y in pts:
        if skip(x, y):
            continue
        d = math.hypot(x - node_xy[0], y - node_xy[1])
        if d < best:
            best = d
    return best


def corner_flags(segs, min_turn=MIN_TURN):
    """One flag per node: round this join?

    Allo rounds the outside of a letter and the waists where two curves meet,
    and leaves the sharp T-joints of one stroke into another alone (see b, B, n),
    so: convex joins are rounded; concave ones only when a curve takes part.
    A join with a tiny edge beside it (Inter leaves 10-unit ledges at some
    diagonals) is skipped - a fillet smaller than the ledge reads as a step."""
    ns = nodes(segs)
    sign = 1.0 if _area(ns) > 0 else -1.0
    flags = []
    for i, node in enumerate(ns):
        seg_in, line_in = _seg(ns[i - 1], node)
        seg_out, line_out = _seg(node, ns[(i + 1) % len(ns)])
        d_in = _tangent(seg_in, 1.0)
        d_out = _tangent(seg_out, 0.0)
        turn = math.acos(max(-1.0, min(1.0, d_in[0] * d_out[0] + d_in[1] * d_out[1])))
        if turn < min_turn:
            flags.append(False)
            continue
        shortest = min(_length(seg_in, line_in), _length(seg_out, line_out))
        if shortest < 4 * FLAT:
            flags.append(False)
            continue
        cross = d_in[0] * d_out[1] - d_in[1] * d_out[0]
        convex = cross * sign > 0
        flags.append(convex or not (line_in and line_out))
    return flags


def round_contour(segs, r, k, flags=None, min_turn=MIN_TURN):
    """Cut convex corners back and fill concave ones: Allo rounds the outside of
    a letter and the corners of its counters, so every join is bridged with a
    k-superellipse, only the side to bulge toward differs. With `flags`, the
    decision comes from outside so every master rounds the same joins."""
    ns = nodes(segs)
    if flags is None:
        flags = corner_flags(segs, min_turn)
    if len(flags) != len(ns):
        raise ValueError(f"corner mask has {len(flags)} flags for {len(ns)} nodes")
    sign = 1.0 if _area(ns) > 0 else -1.0
    pts = _samples(ns)
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
        d_in = _tangent(seg_in, 1.0)
        d_out = _tangent(seg_out, 0.0)
        # a cut may take half an edge, but never more than the stroke is thick
        thick = _thickness(pts, node["p"], lambda x, y: math.dist((x, y), node["p"]) < max(len_in, len_out) * 0.35)
        limit = min(0.5 * len_in - FLAT, 0.5 * len_out - FLAT)
        if thick > 0:
            limit = min(limit, 0.9 * thick)
        radius = max(MIN_RADIUS, min(r, limit))
        left, _ = _split(seg_in, _param_at(seg_in, line_in, radius, from_end=True))
        _, right = _split(seg_out, _param_at(seg_out, line_out, radius, from_end=False))
        start, end = left[3], right[0]
        before = {"p": start, "in": None if line_in else left[2],
                  "out": (start[0] + d_in[0] * k * radius, start[1] + d_in[1] * k * radius)}
        after = {"p": end, "in": (end[0] - d_out[0] * k * radius, end[1] - d_out[1] * k * radius),
                 "out": None if line_out else right[1]}
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
    near = lambda q: any(abs(p[0] - q[0]) < 1e-6 and abs(p[1] - q[1]) < 1e-6 for p in pts)
    assert near((100.0, 0.0)) and near((900.0, 0.0)), pts
    # sharp master: same structure, no visible rounding
    sharp = round_contour(square, 0.0, 0.7387)
    assert len(sharp) == len(got), "point structure must not depend on the radius"
    # a shallow join stays a point instead of becoming a rounded corner
    shallow = [("moveTo", ((0, 0),)), ("lineTo", ((1000, 0),)), ("lineTo", ((2000, 20),)),
               ("lineTo", ((2000, 1000),)), ("lineTo", ((0, 1000),)), ("closePath", ())]
    got = round_contour(shallow, 100, 0.7387)
    on = [s[1][-1] for s in got if s[0] in ("moveTo", "lineTo", "curveTo")]
    assert any(abs(p[0] - 1000.0) < 1e-6 and abs(p[1]) < 1e-6 for p in on), on
    assert len([s for s in got if s[0] == "curveTo"]) == 4, "four 90-degree joins round, the shallow one does not"
    print("round.py self-check ok")


if __name__ == "__main__":
    _selfcheck()
