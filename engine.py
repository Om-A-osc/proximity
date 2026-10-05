from __future__ import annotations

import csv
import hashlib
import heapq
import math
import re
import threading
from collections import OrderedDict
from dataclasses import asdict, dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np

DIST_TOL = 1e-9
RADIUS_TOL = 1e-9
_SPLIT = re.compile(r"[\s,;]+")


@dataclass
class Hit:
    id: int
    lat: float
    lon: float
    category: str
    grid_distance: float
    hops: int
    euclidean_distance: float


class Graph:

    def __init__(self, n: int, key: str, source: str):
        self.adj: List[List[Tuple[int, float]]] = [[] for _ in range(n)]
        self.key = key
        self.source = source
        self.edge_count = 0
        self.skipped_lines = 0
        self.unknown_ids = 0
        self.zero_based = False

    def add_edge(self, u: int, v: int, w: float) -> None:
        self.adj[u].append((v, w))
        self.adj[v].append((u, w))
        self.edge_count += 1

    def info(self) -> dict:
        return {
            "source": self.source,
            "edges": self.edge_count,
            "skipped_lines": self.skipped_lines,
            "unknown_id_links": self.unknown_ids,
            "zero_based_ids_detected": self.zero_based,
        }


class LocationIndex:
    def __init__(self, csv_path: str, graph_cache_size: int = 16):
        ids: List[int] = []
        lats: List[float] = []
        lons: List[float] = []
        cats: List[str] = []
        with open(csv_path, newline="", encoding="utf-8-sig") as fh:
            reader = csv.DictReader(fh)
            names = {name.strip().lower(): name for name in (reader.fieldnames or [])}
            try:
                f_id, f_lat, f_lon, f_cat = (names["id"], names["latitude"],
                                             names["longitude"], names["category"])
            except KeyError as exc:
                raise ValueError(f"CSV is missing column {exc}; have {reader.fieldnames}") from exc
            for row in reader:
                if not row.get(f_id):
                    continue
                ids.append(int(float(row[f_id])))
                lats.append(float(row[f_lat]))
                lons.append(float(row[f_lon]))
                cats.append(row[f_cat].strip().lower())

        self.n = len(ids)
        self.ids = np.asarray(ids, dtype=np.int64)
        self.lat = np.asarray(lats, dtype=np.float64)
        self.lon = np.asarray(lons, dtype=np.float64)
        self.cat = np.asarray(cats)
        self.categories = sorted(set(cats))
        self.id_to_idx: Dict[int, int] = {int(i): k for k, i in enumerate(ids)}
        self._cat_masks = {c: (self.cat == c) for c in self.categories}

        self._lock = threading.Lock()
        self._graphs: "OrderedDict[str, Graph]" = OrderedDict()
        self._cache_size = graph_cache_size
        self._lattice: Optional[Graph] = None

    def _euclid(self, u: int, v: int) -> float:
        return math.hypot(self.lat[u] - self.lat[v], self.lon[u] - self.lon[v])

    def nearest(self, lat: float, lon: float) -> Tuple[int, float]:
        d2 = (self.lat - lat) ** 2 + (self.lon - lon) ** 2
        m = float(d2.min())
        cands = np.flatnonzero(d2 <= m + 1e-18)
        idx = int(cands[np.argmin(self.ids[cands])])
        return idx, math.sqrt(float(d2[idx]))

    def lattice_graph(self) -> Graph:
        if self._lattice is None:
            rows = {v: i for i, v in enumerate(np.unique(self.lat))}
            cols = {v: i for i, v in enumerate(np.unique(self.lon))}
            pos = {(rows[self.lat[k]], cols[self.lon[k]]): k for k in range(self.n)}
            g = Graph(self.n, "lattice", "default 4-neighbour lattice (no link file given)")
            for (r, c), k in pos.items():
                for nb in ((r + 1, c), (r, c + 1)):
                    j = pos.get(nb)
                    if j is not None:
                        g.add_edge(k, j, self._euclid(k, j))
            self._lattice = g
        return self._lattice

    def graph_from_links(self, text: str) -> Graph:
        key = hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()
        with self._lock:
            g = self._graphs.get(key)
            if g is not None:
                self._graphs.move_to_end(key)
                return g
        g = self._build_from_links(text, key)
        with self._lock:
            self._graphs[key] = g
            while len(self._graphs) > self._cache_size:
                self._graphs.popitem(last=False)
        return g

    def _build_from_links(self, text: str, key: str) -> Graph:
        pairs: List[Tuple[int, int]] = []
        skipped = 0
        for line in text.splitlines():
            line = line.strip()
            if not line or line[0] == "#":
                continue
            toks = _SPLIT.split(line)
            if len(toks) < 2:
                skipped += 1
                continue
            try:
                a, b = int(float(toks[0])), int(float(toks[1]))
            except ValueError:
                skipped += 1
                continue
            pairs.append((a, b))

        g = Graph(self.n, key, "link file")
        g.skipped_lines = skipped
        if not pairs:
            return g

        lo = min(min(a, b) for a, b in pairs)
        shift = 0
        if lo == 0 and 0 not in self.id_to_idx:
            shift = 1
            g.zero_based = True

        seen = set()
        unknown = 0
        for a, b in pairs:
            u = self.id_to_idx.get(a + shift)
            v = self.id_to_idx.get(b + shift)
            if u is None or v is None:
                unknown += 1
                continue
            if u == v:
                continue
            e = (u, v) if u < v else (v, u)
            if e in seen:
                continue
            seen.add(e)
            g.add_edge(u, v, self._euclid(u, v))
        g.unknown_ids = unknown
        return g

    def search(self, lat: float, lon: float, category: str, radius: float,
               graph: Graph, k: int = 10, mode: str = "weighted") -> dict:
        cat = category.strip().lower()
        mask = self._cat_masks.get(cat)
        if mask is None:
            raise ValueError(f"unknown category {category!r}; known categories: {self.categories}")
        if radius < 0:
            raise ValueError("rad must be >= 0")
        if mode not in ("weighted", "hops"):
            raise ValueError("mode must be 'weighted' or 'hops'")
        k = max(1, min(int(k), self.n))

        start, snap_dist = self.nearest(lat, lon)
        eu = np.sqrt((self.lat - lat) ** 2 + (self.lon - lon) ** 2)
        cand = (mask & (eu <= radius + RADIUS_TOL)).tolist()
        n_cand = sum(cand)

        adj = graph.adj
        INF = float("inf")
        best = [INF] * self.n
        other = [0.0] * self.n
        best[start] = 0.0
        heap: List[Tuple[float, int]] = [(0.0, start)]
        found: List[int] = []
        cutoff: Optional[float] = None
        tol = DIST_TOL if mode == "weighted" else 0.0
        weighted = mode == "weighted"
        settled = 0

        while heap:
            d, u = heapq.heappop(heap)
            if d > best[u]:
                continue
            if cutoff is not None and d > cutoff + tol:
                break
            settled += 1
            if cand[u]:
                found.append(u)
                if len(found) == k and cutoff is None:
                    cutoff = d
            ou = other[u]
            for v, w in adj[u]:
                if weighted:
                    nd, no = d + w, ou + 1
                else:
                    nd, no = d + 1, ou + w
                if nd < best[v]:
                    best[v] = nd
                    other[v] = no
                    heapq.heappush(heap, (nd, v))

        hits: List[Hit] = []
        for u in found:
            gd = best[u] if weighted else other[u]
            hops = int(round(other[u])) if weighted else int(best[u])
            hits.append(Hit(int(self.ids[u]), float(self.lat[u]), float(self.lon[u]),
                            str(self.cat[u]), float(gd), hops, float(eu[u])))
        if weighted:
            hits.sort(key=lambda h: (round(h.grid_distance / DIST_TOL), h.id))
        else:
            hits.sort(key=lambda h: (h.hops, h.id))
        hits = hits[:k]

        return {
            "ids": [h.id for h in hits],
            "hits": [asdict(h) for h in hits],
            "start": {
                "id": int(self.ids[start]),
                "lat": float(self.lat[start]),
                "lon": float(self.lon[start]),
                "category": str(self.cat[start]),
                "snap_distance": snap_dist,
            },
            "stats": {
                "candidates_in_radius": int(n_cand),
                "nodes_settled": settled,
                "mode": mode,
            },
        }
