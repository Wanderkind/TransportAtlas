"""The faster simplifier and memoized display levels must not change output."""
from datetime import date
from pathlib import Path
import math
import random
from transport_atlas.ingest import import_file
from transport_atlas.network import network_geometry, simplify_line
from transport_atlas.storage import connect


def reference_simplify(points, tolerance=0.0001):
    """The pre-optimization implementation, kept verbatim as an oracle."""
    if tolerance <= 0 or len(points) <= 2:
        return points
    radial = [points[0]]
    threshold = (tolerance/2)**2
    for point in points[1:-1]:
        if (point[0]-radial[-1][0])**2+(point[1]-radial[-1][1])**2 >= threshold:
            radial.append(point)
    radial.append(points[-1])
    points = radial
    tolerance /= 2
    keep = {0, len(points)-1}
    stack = [(0, len(points)-1)]
    while stack:
        start, end = stack.pop()
        ax, ay = points[start]
        bx, by = points[end]
        dx, dy = bx-ax, by-ay
        length = dx*dx+dy*dy
        greatest, index = tolerance*tolerance, None
        for i in range(start+1, end):
            px, py = points[i]
            t = max(0, min(1, ((px-ax)*dx+(py-ay)*dy)/length)) if length else 0
            distance = (px-ax-t*dx)**2+(py-ay-t*dy)**2
            if distance > greatest:
                greatest, index = distance, i
        if index is not None:
            keep.add(index)
            stack.extend(((start, index), (index, end)))
    return [points[i] for i in sorted(keep)]


def _walks(seed=7):
    rng = random.Random(seed)
    shapes = []
    for n in (3, 4, 10, 57, 400, 1500):
        x, y, heading = 9.0+rng.random(), 40.0+rng.random(), rng.random()*math.tau
        line = []
        for _ in range(n):
            heading += rng.gauss(0, 0.4)
            step = rng.choice((0.00001, 0.00005, 0.0003, 0.002))
            x, y = x+step*math.cos(heading), y+step*math.sin(heading)
            line.append([round(x, 6), round(y, 6)])
        shapes.append(line)
    # Degenerate cases: closed loop, repeated points and backtracking.
    shapes.append([[9.0, 40.0], [9.01, 40.0], [9.01, 40.01], [9.0, 40.0]])
    shapes.append([[9.0, 40.0]]*5+[[9.002, 40.0]])
    shapes.append([[9.0, 40.0], [9.003, 40.0], [8.999, 40.0], [9.004, 40.0001]])
    return shapes


def test_simplify_matches_reference_at_every_display_level():
    for line in _walks():
        for tolerance in (0, 0.0001, 0.0002, 0.0004, 0.0008, 0.0016, 0.0032, 0.0256):
            assert simplify_line(line, tolerance) == reference_simplify(line, tolerance)


def test_memoized_levels_reproduce_uncached_geometry(tmp_path):
    con = connect(tmp_path/'simplify.sqlite', 'sqlite')
    try:
        import_file(con, Path(__file__).parent/'fixtures'/'synthetic_sardinia.zip',
                    source_id='demo', archive_dir=tmp_path/'raw')
        shared, coarser_levels = {}, 0
        for day in (date(2026, 11, 6), date(2026, 11, 7), date(2026, 11, 6)):
            for max_points in (120000, 4):
                fresh = network_geometry(con, day, max_points=max_points)
                memo = network_geometry(con, day, max_points=max_points, shape_cache=shared)
                assert memo == fresh
                coarser_levels += memo['metadata']['simplification_tolerance_degrees'] > 0.0001
        assert coarser_levels  # a tight point budget exercised the memoized coarser levels
    finally:
        con.close()
