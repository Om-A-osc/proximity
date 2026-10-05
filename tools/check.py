import argparse
import csv
import heapq
import math
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine import LocationIndex


def load_points(path):
    pts = {}
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            pts[int(row["ID"])] = (float(row["Latitude"]), float(row["Longitude"]), row["Category"].strip().lower())
    return pts


def load_links(path, pts):
    adj = {i: [] for i in pts}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            parts = line.split()
            if len(parts) < 2:
                continue
            a, b = int(parts[0]), int(parts[1])
            if a in pts and b in pts and a != b:
                w = math.hypot(pts[a][0] - pts[b][0], pts[a][1] - pts[b][1])
                adj[a].append((b, w))
                adj[b].append((a, w))
    return adj


def reference(pts, adj, lat, lon, cat, rad, k=10, mode="weighted"):
    start = min(pts, key=lambda i: ((pts[i][0] - lat) ** 2 + (pts[i][1] - lon) ** 2, i))
    dist = {start: 0.0}
    pq = [(0.0, start)]
    while pq:
        d, u = heapq.heappop(pq)
        if d > dist[u]:
            continue
        for v, w in adj[u]:
            nd = d + (w if mode == "weighted" else 1)
            if nd < dist.get(v, math.inf):
                dist[v] = nd
                heapq.heappush(pq, (nd, v))
    valid = [i for i in pts
             if pts[i][2] == cat and i in dist
             and math.hypot(pts[i][0] - lat, pts[i][1] - lon) <= rad + 1e-9]
    valid.sort(key=lambda i: (round(dist[i] / 1e-9), i))
    return valid[:k]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default="locations.csv")
    ap.add_argument("--links", required=True)
    ap.add_argument("--queries", type=int, default=200)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--mode", default="weighted", choices=["weighted", "hops"])
    args = ap.parse_args()

    pts = load_points(args.csv)
    adj = load_links(args.links, pts)
    idx = LocationIndex(args.csv)
    with open(args.links, encoding="utf-8") as fh:
        graph = idx.graph_from_links(fh.read())
    cats = sorted({p[2] for p in pts.values()})
    rng = random.Random(args.seed)

    mismatches = 0
    total_ms = 0.0
    worst_ms = 0.0
    for q in range(args.queries):
        lat, lon = rng.random(), rng.random()
        cat = rng.choice(cats)
        rad = rng.uniform(0.06, 0.5)
        t0 = time.perf_counter()
        got = idx.search(lat, lon, cat, rad, graph, k=10, mode=args.mode)["ids"]
        ms = (time.perf_counter() - t0) * 1000
        total_ms += ms
        worst_ms = max(worst_ms, ms)
        exp = reference(pts, adj, lat, lon, cat, rad, 10, args.mode)
        if got != exp:
            mismatches += 1
            if mismatches <= 5:
                print(f"MISMATCH q{q}: lat={lat:.4f} lon={lon:.4f} cat={cat} rad={rad:.3f}\n  got {got}\n  exp {exp}")
    print(f"mode={args.mode} queries={args.queries} mismatches={mismatches} "
          f"avg={total_ms / args.queries:.2f} ms worst={worst_ms:.2f} ms")
    sys.exit(1 if mismatches else 0)


if __name__ == "__main__":
    main()
