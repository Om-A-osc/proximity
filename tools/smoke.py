import os
import random
import sys
import time

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine import LocationIndex

API = os.environ.get("API_URL", "http://127.0.0.1:3000").rstrip("/")
CSV = os.environ.get("LOCATIONS_CSV", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "locations.csv"))
QUERIES = [
    (0.5, 0.5, "bank", 0.2),
    (0.1, 0.9, "cafe", 0.15),
    (0.73, 0.21, "hospital", 0.3),
    (0.0, 0.0, "store", 0.5),
    (0.42, 0.88, "pharmacy", 0.12),
]


def wait_for_api():
    for _ in range(30):
        try:
            r = httpx.get(API + "/health", timeout=5)
            if r.status_code == 200:
                print("health:", r.json())
                return
        except httpx.HTTPError as exc:
            print("waiting for API:", exc)
        time.sleep(2)
    sys.exit("API not reachable at " + API)


def sample_links(idx):
    g = idx.lattice_graph()
    edges = sorted({(u, v) if u < v else (v, u) for u, nbrs in enumerate(g.adj) for v, _ in nbrs})
    rng = random.Random(42)
    kept = [e for e in edges if rng.random() >= 0.15]
    return "\n".join(f"{idx.ids[u]} {idx.ids[v]}" for u, v in kept) + "\n"


def main():
    wait_for_api()
    idx = LocationIndex(CSV)
    text = sample_links(idx)
    graph = idx.graph_from_links(text)
    failures = 0
    for lat, lon, cat, rad in QUERIES:
        r = httpx.post(API + "/search/", data={"lat": lat, "long": lon, "cat": cat, "rad": rad},
                       files={"link": ("links.txt", text.encode(), "text/plain")}, timeout=60)
        got = r.json().get("ids") if r.status_code == 200 else None
        exp = idx.search(lat, lon, cat, rad, graph)["ids"]
        ok = got == exp
        failures += not ok
        print(f"{'OK  ' if ok else 'FAIL'} lat={lat} long={lon} cat={cat} rad={rad} -> {got} ({r.elapsed.total_seconds() * 1000:.0f} ms)")
        if not ok:
            print("     expected", exp)

    r = httpx.get(API + "/search", params={"lat": 0.5, "long": 0.5, "cat": "bank", "rad": 0.2}, timeout=30)
    ok = r.status_code == 200 and r.json()["graph"]["source"].startswith("default")
    failures += not ok
    print(f"{'OK  ' if ok else 'FAIL'} GET without link -> lattice fallback (status {r.status_code})")

    r = httpx.get(API + "/search/", params={"lat": 0.5, "long": 0.5, "cat": "zoo", "rad": 0.2}, timeout=30)
    ok = r.status_code == 400
    failures += not ok
    print(f"{'OK  ' if ok else 'FAIL'} unknown category -> {r.status_code}")

    print("SMOKE TEST", "PASSED" if failures == 0 else f"FAILED ({failures})")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
