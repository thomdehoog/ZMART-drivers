"""The focus map, as the LAS X Navigator's: the focus of every field of a scan, from a few focal points.

A focal point is a place on the sample with its focus, ``(x, y, z)`` in
micrometres from the origin, as ``position`` and ``move_to`` give them.
The Navigator finds each point's focus with its autofocus ("Find all by
Autofocus") or takes it as set ("Set Z"); the driver does the same with
``find_focus_map``, or takes points given. Its "Setup Focus Points"
spreads a number of points over the fields of the regions
(:func:`spread`), and "Shift focus map to current Z" moves the whole map
by one offset (:func:`shift`).

At Start, each field is taken at the focus the map gives its place, as
LAS X's help describes it (Creating a Focus Map, the calculation): the
points are joined into triangles (a Delaunay triangulation). Inside a
triangle, or on its edge, the focus lies on the plane through its three
corners. Outside, it is the focus where a line at right angles to the
nearest outer edge meets it, when that foot lies on the edge; otherwise
the focus of the nearest point. Nothing is extrapolated, so no field is
taken beyond the deepest or the shallowest focus found. The Preview
takes no focus map, in LAS X as in the driver.
"""

from __future__ import annotations

import math

#: Two places closer than this, in micrometres, are the same place.
SAME_PLACE_UM = 1e-6


def checked_points(points) -> list[tuple[float, float, float]]:
    """The focal points as ``(x, y, z)`` floats; ValueError for a point without its focus, or fewer than two places."""
    checked = []
    for point in points:
        try:
            x, y, z = (float(v) for v in point)
        except (TypeError, ValueError) as why:
            raise ValueError(f"a focal point is (x, y, z) in micrometres, not {point!r}") from why
        checked.append((x, y, z))
    if len(checked) < 2:
        raise ValueError(f"a focus map needs two focal points or more, not {len(checked)}")
    for i, (x, y, _) in enumerate(checked):
        for other in checked[i + 1:]:
            if math.dist((x, y), other[:2]) < SAME_PLACE_UM:
                raise ValueError(f"two focal points at one place, ({x:g}, {y:g}): each place has one focus")
    return checked


def in_circle(a, b, c, p) -> bool:
    """Whether *p* lies inside the circle through *a*, *b* and *c* (only x and y count)."""
    ax, ay = a[0] - p[0], a[1] - p[1]
    bx, by = b[0] - p[0], b[1] - p[1]
    cx, cy = c[0] - p[0], c[1] - p[1]
    determinant = (
        (ax * ax + ay * ay) * (bx * cy - cx * by)
        - (bx * bx + by * by) * (ax * cy - cx * ay)
        + (cx * cx + cy * cy) * (ax * by - bx * ay)
    )
    turn = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    return determinant * turn > 1e-9


def triangles(points) -> list[tuple[int, int, int]]:
    """The Delaunay triangles of the points' places, as indices into *points*; none when they lie in a line.

    Built a point at a time inside a triangle large enough to hold them all
    (Bowyer and Watson): each new point removes the triangles whose circle
    holds it and joins the hole's edges to itself.
    """
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    middle_x, middle_y = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    reach = max(max(xs) - min(xs), max(ys) - min(ys), 1.0) * 100
    corners = [(middle_x - 2 * reach, middle_y - reach), (middle_x + 2 * reach, middle_y - reach), (middle_x, middle_y + 2 * reach)]
    places = [(p[0], p[1]) for p in points] + corners
    first_corner = len(points)
    mesh = [(first_corner, first_corner + 1, first_corner + 2)]
    for i in range(len(points)):
        broken = [t for t in mesh if in_circle(places[t[0]], places[t[1]], places[t[2]], places[i])]
        edges = {}
        for a, b, c in broken:
            for edge in ((a, b), (b, c), (c, a)):
                key = frozenset(edge)
                edges[key] = None if key in edges else edge
        mesh = [t for t in mesh if t not in broken]
        mesh += [(a, b, i) for edge in edges.values() if edge is not None for a, b in [edge]]
    return [
        t for t in mesh
        if max(t) < first_corner and abs(area(places[t[0]], places[t[1]], places[t[2]])) > SAME_PLACE_UM
    ]


def area(a, b, c) -> float:
    """Twice the signed area of the triangle *a*, *b*, *c*."""
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def outer_edges(points, mesh) -> list[tuple[int, int]]:
    """The edges of the map's outline: those of one triangle only, or, with the points in a line, from each to the next."""
    if not mesh:
        x0, y0 = points[0][0], points[0][1]
        far = max(range(len(points)), key=lambda i: (points[i][0] - x0) ** 2 + (points[i][1] - y0) ** 2)
        dx, dy = points[far][0] - x0, points[far][1] - y0
        along = sorted(range(len(points)), key=lambda i: (points[i][0] - x0) * dx + (points[i][1] - y0) * dy)
        return list(zip(along, along[1:]))
    count = {}
    for a, b, c in mesh:
        for edge in ((a, b), (b, c), (c, a)):
            key = frozenset(edge)
            count[key] = count.get(key, 0) + 1
    return [tuple(key) for key, seen in count.items() if seen == 1]


def spread(fields, number: int) -> list[tuple[float, float]]:
    """*number* places spread over *fields* (``(x, y)`` in micrometres), for the focal points, as "Setup Focus Points".

    LAS X's help does not say how it lays them out; on the simulator it put
    5 over a region of 18 by 27 fields in two rows, unevenly, the same both
    times (2026-11-03), as the middles of groups of fields do. So the fields
    are grouped round *number* middles (k-means, from middles chosen
    farthest apart, the first the field nearest the fields' centre), and the
    middles are the places: the same fields give the same places.
    """
    if isinstance(number, bool) or not isinstance(number, int) or number < 1:
        raise ValueError(f"the number of focal points is 1 or more, not {number!r}")
    fields = [(float(x), float(y)) for x, y in fields]
    if number > len(fields):
        raise ValueError(f"{number} focal points over {len(fields)} fields: at most one point per field")
    centre = (sum(x for x, _ in fields) / len(fields), sum(y for _, y in fields) / len(fields))
    middles = [min(fields, key=lambda f: math.dist(f, centre))]
    while len(middles) < number:
        middles.append(max(fields, key=lambda f: min(math.dist(f, m) for m in middles)))
    for _ in range(100):
        groups = [[] for _ in middles]
        for field in fields:
            groups[min(range(len(middles)), key=lambda i: math.dist(field, middles[i]))].append(field)
        moved = [
            (sum(x for x, _ in group) / len(group), sum(y for _, y in group) / len(group)) if group else middle
            for group, middle in zip(groups, middles)
        ]
        if moved == middles:
            break
        middles = moved
    return middles


def shift(points, x: float, y: float, z: float) -> list[tuple[float, float, float]]:
    """The map moved by one offset so that it gives *z* at (*x*, *y*), as "Shift focus map to current Z": the focus
    found here after a drift, the map's shape kept."""
    points = checked_points(points)
    offset = z - focus_at(points, x, y)
    return [(px, py, pz + offset) for px, py, pz in points]


def focus_at(points, x: float, y: float) -> float:
    """The focus the map gives the place (*x*, *y*), in micrometres, as LAS X's help describes it."""
    points = checked_points(points)
    mesh = triangles(points)
    for a, b, c in mesh:
        pa, pb, pc = points[a], points[b], points[c]
        whole = area(pa, pb, pc)
        wa, wb = area((x, y), pb, pc) / whole, area(pa, (x, y), pc) / whole
        wc = 1.0 - wa - wb
        if min(wa, wb, wc) >= -1e-9:
            return wa * pa[2] + wb * pb[2] + wc * pc[2]
    edges = []
    for a, b in outer_edges(points, mesh):
        pa, pb = points[a], points[b]
        dx, dy = pb[0] - pa[0], pb[1] - pa[1]
        along = ((x - pa[0]) * dx + (y - pa[1]) * dy) / (dx * dx + dy * dy)
        on_edge = min(max(along, 0.0), 1.0)
        distance = math.dist((pa[0] + on_edge * dx, pa[1] + on_edge * dy), (x, y))
        edges.append((distance, along, pa, pb))
    distance, along, pa, pb = min(edges, key=lambda edge: edge[0])
    if 0.0 <= along <= 1.0:
        return pa[2] + along * (pb[2] - pa[2])
    return min(points, key=lambda p: math.dist((p[0], p[1]), (x, y)))[2]
