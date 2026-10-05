import argparse
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine import LocationIndex


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default="locations.csv")
    ap.add_argument("--drop", type=float, default=0.15, help="fraction of lattice links to remove")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", default="links_sample.txt")
    ap.add_argument("--shuffle", action="store_true", help="shuffle line order")
    args = ap.parse_args()

    idx = LocationIndex(args.csv)
    g = idx.lattice_graph()
    edges = sorted({(u, v) if u < v else (v, u) for u, nbrs in enumerate(g.adj) for v, _ in nbrs})
    rng = random.Random(args.seed)
    kept = [e for e in edges if rng.random() >= args.drop]
    if args.shuffle:
        rng.shuffle(kept)
    with open(args.out, "w", encoding="utf-8") as fh:
        for u, v in kept:
            fh.write(f"{idx.ids[u]} {idx.ids[v]}\n")
    print(f"lattice links: {len(edges)}  kept: {len(kept)}  dropped: {len(edges) - len(kept)}  -> {args.out}")


if __name__ == "__main__":
    main()
