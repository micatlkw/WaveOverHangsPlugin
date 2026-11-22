"""Test of the outer_walls setting (normal walls kept along the overhang's edge).

    python3 tests/outer_wall_test.py [out.png]

Needs pyclipper and numpy (matplotlib for the optional picture). Runs island_waves(), the
per-island step of the slicing hook, on three overhangs with 0, 1 and 2 kept walls, and checks:
  - no walls with 0, and the waves are then the same as without the setting;
  - the walls follow the island's edge (outer wall centre ext_w/2 inside it), only where the
    slicer's walls were cut, innermost first;
  - the waves stay inside the walls.
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import generator_smoke as g  # noqa: E402  (stubs orca and loads the plugin)

w = g.w
S = g.S
H = 0.2 * S
EXT_W = 0.45 * S
INNER_W = 0.45 * S
OV_W = int(0.45 * S)
PERIM_S = int(INNER_W - H * (1.0 - 0.25 * math.pi))
HALF_NOZZLE = int(0.2 * S)
P = dict(g.P, perimeter_count=1)


def square_hole(cx, cy, r):
    return list(reversed(g.rect(cx - r, cy - r, cx + r, cy + r)))


CASES = {
    "cantilever 30x10 on 10x10": ((g.rect(0, 0, 30, 10), []), [g.rect(0, 0, 10, 10)]),
    "ring: disc r24 on disc r16": ((g.circle(24), []), [g.circle(16)]),
    "plate with a hole on a post": ((g.rect(-12, -12, 12, 12), [square_hole(5, 5, 2)]), [g.rect(-3, -3, 3, 3)]),
}


def seg_dist(p, a, b):
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    t = 0.0 if dx == dy == 0 else max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(p[0] - ax - t * dx, p[1] - ay - t * dy)


def edge_dist(p, polys):
    return min(seg_dist(p, poly[i - 1], poly[i]) for poly in polys for i in range(len(poly)))


def run(island, lower_raw, keep):
    island_polys = w.to_polygons([island])
    lower = w.expand(lower_raw, HALF_NOZZLE)
    P_keep = dict(P, perimeter_count=max(1, 2 - keep))
    return w.island_waves(island, island_polys, lower_raw, lower, P_keep, keep, H, EXT_W, INNER_W, OV_W, PERIM_S)


def main():
    ok = True
    pictures = []
    for name, (island, lower_raw) in CASES.items():
        island_polys = w.to_polygons([island])
        # The slicer's wall_loops is 2 here; with no kept walls the generator gets all of them.
        base_regions, _ = w.generate([island], w.expand(lower_raw, HALF_NOZZLE), dict(P, perimeter_count=2))
        base = [p for r in base_regions for p in r]
        for keep in (0, 1, 2):
            regions, cut, walls = run(island, lower_raw, keep)
            waves = [p for r in regions for p in r]
            problems = []
            if keep == 0:
                if walls:
                    problems.append("walls with outer_walls = 0")
                if waves != base:
                    problems.append("waves differ from the plain generator")
            else:
                if len(walls) != keep:
                    problems.append("%d wall level(s), expected %d" % (len(walls), keep))
                roles = [role for role, _, _ in walls]
                if roles and roles[-1] != "Outer wall":
                    problems.append("outer wall not last: %s" % roles)
                outer = walls[-1][2] if walls else []
                d = [edge_dist(pt, island_polys) / S for pl in outer for pt in pl]
                # ext_w / 2 from the edge; up to sqrt(2) times that at mitred corners
                if d and (min(d) < 0.2 or max(d) > 0.225 * math.sqrt(2) + 0.01):
                    problems.append("outer wall %.3f..%.3f mm from the edge, expected 0.225" % (min(d), max(d)))
                cut_u = w.union(cut)
                stray = [pt for pl in outer for pt in pl if not w.intersection_pl([[pt, (pt[0] + 1, pt[1])]], cut_u)]
                if stray:
                    problems.append("%d outer wall point(s) outside the cut" % len(stray))
                inner_most = (EXT_W / 2 + (keep - 1) * PERIM_S) / S
                wd = [edge_dist(pt, island_polys) / S for pl in waves for pt in pl]
                if wd and min(wd) < inner_most - 0.11:
                    problems.append("waves reach %.3f mm from the edge, inside the walls (innermost wall at %.3f)"
                                    % (min(wd), inner_most))
            wall_mm = sum(w.polyline_length(pl) for _, _, pls in walls for pl in pls) / S
            ok &= not problems
            print("%s %-28s outer_walls=%d: %3d wave path(s), %6.1f mm of wall in %d piece(s)"
                  % ("OK  " if not problems else "FAIL", name, keep, len(waves), wall_mm,
                     sum(len(pls) for _, _, pls in walls)))
            for p in problems:
                print("       !! " + p)
            if keep == 1:
                pictures.append((name, island_polys, lower_raw, waves, walls))
    if len(sys.argv) > 1:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, axes = plt.subplots(1, len(pictures), figsize=(6 * len(pictures), 6))
        for ax, (name, island_polys, lower_raw, waves, walls) in zip(axes, pictures):
            for poly in lower_raw:
                ax.fill([x / S for x, _ in poly], [y / S for _, y in poly], color="#eee")
            for poly in island_polys:
                ax.plot([x / S for x, _ in poly] + [poly[0][0] / S], [y / S for _, y in poly] + [poly[0][1] / S],
                        color="#999", lw=.6)
            for pl in waves:
                ax.plot([x / S for x, _ in pl], [y / S for _, y in pl], color="#1a9", lw=.5)
            for role, _, pls in walls:
                for pl in pls:
                    ax.plot([x / S for x, _ in pl], [y / S for _, y in pl],
                            color="#c33" if role == "Outer wall" else "#e93", lw=1.2)
            ax.set_title(name + " (1 wall)")
            ax.set_aspect("equal")
        plt.tight_layout()
        plt.savefig(sys.argv[1], dpi=90)
    print("all OK" if ok else "FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
