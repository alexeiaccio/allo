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


def corner_flags(segs, min_turn=MIN_TURN):
    """One flag per node: is this join sharp enough to be rounded?"""
    ns = nodes(segs)
    flags = []
    for i, node in enumerate(ns):
        d_in = _unit(ns[i - 1]["p"], node["p"])
        d_out = _unit(node["p"], ns[(i + 1) % len(ns)]["p"])
        turn = math.acos(max(-1.0, min(1.0, d_in[0] * d_out[0] + d_in[1] * d_out[1])))
        flags.append(turn >= min_turn)
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
    for i, node in enumerate(ns):
        if not flags[i]:
            out.append(dict(node))
            continue
        nxt = ns[(i + 1) % len(ns)]
        d_in = _unit(ns[i - 1]["p"], node["p"])
        d_out = _unit(node["p"], nxt["p"])
        radius = min(max(r, MIN_RADIUS), corner_radius(ns, i))
        p = node["p"]
        start = (p[0] - d_in[0] * radius, p[1] - d_in[1] * radius)
        end = (p[0] + d_out[0] * radius, p[1] + d_out[1] * radius)
        # shorten the incoming segment to the cut point, keeping its tangent
        h_in = node["in"]
        if h_in is not None:
            h_in = (start[0] - d_in[0] * math.dist(h_in, p), start[1] - d_in[1] * math.dist(h_in, p))
        h_out = nxt.get("out")
        before = {"p": start, "in": h_in, "out": (start[0] + d_in[0] * k * radius,
                                                  start[1] + d_in[1] * k * radius)}
        after = {"p": end, "in": (end[0] - d_out[0] * k * radius, end[1] - d_out[1] * k * radius),
                 "out": None}
        out += [before, after]
    # the outgoing handle of the node before a corner now belongs to the cut point
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
