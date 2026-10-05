# Proximity Recommendation API: Report

## 1. Problem

Given a dataset of locations on a 1 x 1 grid and a road-link file, build an API
that returns the 10 locations of a requested category that are closest to a
query point, where:

* **"in range"** is decided by the *circular* (straight-line) distance from the
  query point: a location qualifies only if it lies within `rad`;
* **"closest"** is decided by the *grid* (road traversal) distance: the length
  of the shortest path along existing links from the query point to the
  location.

The two measures differ because some links are missing, so a location that is
geometrically near may be far by road, or unreachable altogether.

Endpoint: `POST /search/` with fields `lat, long, cat, rad, link`.

## 2. Dataset analysis

`locations.csv` has 10,000 rows with columns `ID, Latitude, Longitude, Category`.
Inspection shows a strict structure:

| property                | value                                               |
|-------------------------|-----------------------------------------------------|
| distinct latitudes      | 100, evenly spaced from 0 to 1 (step 1/99 = 0.010101) |
| distinct longitudes     | 100, same spacing                                   |
| layout                  | a 100 x 100 lattice, no duplicate coordinates       |
| ID                      | `row * 100 + col + 1` (row from latitude, col from longitude) |
| categories              | 8 (`bank, cafe, hospital, park, pharmacy, restaurant, school, store`), 1,250 each |

So the "roads" are the lattice links between 4-neighbours. A full lattice has
19,800 links; the link file lists the subset that actually exists. The engine
does not rely on the lattice assumption (it works for any point set and any
link list), but the structure explains the behaviour of the distances.

## 3. Method

### 3.1 Graph model

* Every CSV row is a node.
* Every line `a b` of the link file is an undirected edge between nodes `a` and
  `b`. Its weight is the Euclidean length between the two endpoints, computed
  from the CSV coordinates. On this lattice every link is about 0.0101 long.
* Lines that are blank, comments, or non-numeric (a header) are skipped;
  duplicate links and self-links are ignored; IDs not present in the CSV are
  counted and skipped. A 0-based file (contains `0`, CSV starts at `1`) is
  detected and shifted.
* Parsed graphs are cached by SHA-1 of the file content (LRU, 16 entries), so
  the file is parsed once per distinct link file, not once per query.
* If no link file is sent, the full 4-neighbour lattice is used.

### 3.2 Query pipeline

1. **Snap.** The query point is usually not exactly on a node. The start node is
   the node nearest to `(lat, long)` by Euclidean distance (ties go to the
   lowest ID). This is a vectorised NumPy scan over all points.
2. **Filter set.** A boolean mask marks candidate nodes: category equal to
   `cat` (case-insensitive) and Euclidean distance from the query point
   `<= rad` (inclusive, with 1e-9 slack for floating-point noise). Note the
   radius is measured from the query point itself, not from the snapped node.
3. **Route.** Dijkstra's algorithm from the start node over the link graph with
   a binary heap. Nodes outside the radius are still traversed, because a
   shortest path may leave the circle and come back.
4. **Early termination.** Dijkstra settles nodes in non-decreasing distance
   order. When the 10th candidate is settled its distance `d10` becomes a
   cutoff; the search continues only while the heap's minimum is `<= d10`
   (to collect exact ties) and then stops. For a typical query only a few
   hundred of the 10,000 nodes are touched.
5. **Rank.** Candidates are sorted by grid distance, ties by ID, and the first
   10 IDs are returned.

An alternative ranking `mode=hops` (count of links instead of summed length) is
available as a query option. On this lattice the two agree except for ties,
because every link has essentially the same length.

### 3.3 Complexity

With `V` nodes and `E` links:

* link-file parse: `O(E)` once per distinct file (about 40 ms for 16,787 links);
* snap and filter: `O(V)` vectorised NumPy (microseconds for 10,000 points);
* Dijkstra: `O((V + E) log V)` worst case, but early termination makes the cost
  proportional to the number of nodes closer than the 10th answer;
* memory: adjacency lists, `O(V + E)`.

Measured on the 10,000-point dataset (Python 3.11, laptop):

| scenario                                     | time        |
|----------------------------------------------|-------------|
| typical query, graph cached (300 random)     | 0.4 ms avg, 0.7 ms worst |
| forced full-graph settle (k = 10,000)        | 21 ms       |
| first query with a new link file (incl. parse) | about 40-60 ms |

## 4. API design

* Framework: FastAPI + Uvicorn (ASGI). Data and the default lattice are loaded
  once at start-up.
* Route `/search/` accepts `GET` (query string) and `POST` (multipart form,
  url-encoded form, or JSON). `/search` without the slash also works.
* `link` may be an uploaded `.txt` file, a URL to a raw text file (downloaded
  server-side), or the raw link text in the field itself.
* Field aliases are accepted (`latitude/longitude/category/radius`) to be
  tolerant of client variations.
* Response: JSON with `ids` (the answer, nearest first) plus diagnostics: the
  snapped start node, per-result grid distance / hop count / straight-line
  distance, link-file statistics, and timing.
* Errors (missing field, non-numeric value, unknown category, bad URL) return
  HTTP 400 with a message.
* A small HTML form at `/` and the generated OpenAPI docs at `/docs` make manual
  testing easy.

## 5. Verification

* **Reference cross-check** (`tools/check.py`): an independent naive
  implementation (plain dicts, full Dijkstra without early exit, explicit
  filter and sort) was run against the engine on 300 random queries (random
  point, category and radius in [0.06, 0.5]) over a link file with 15 % of the
  lattice links removed. Result: 0 mismatches in both `weighted` and `hops`
  modes, including ordering.
* **HTTP tests** (`tools/api_test.py`): multipart upload, inline text, JSON and
  GET-with-lattice all return identical IDs; validation errors return 400.
* **Edge cases**: isolated start node (returns only itself if it matches),
  disconnected graph (unreachable nodes excluded), 0-based IDs, header and
  comment lines, comma separators, radius exactly on a node.

## 6. Assumptions made where the specification is open

1. The start of the road journey is the grid node nearest to the query point.
2. Links are undirected and their length is the Euclidean distance between
   their endpoints.
3. The radius test is inclusive and uses the query point, not the snapped node.
4. Ties in grid distance are broken by lower ID.
5. Locations unreachable from the start are never recommended; if fewer than
   10 valid reachable locations exist, fewer are returned (the brief guarantees
   at least 10).
6. The snapped node itself is a valid answer if it matches the category.

## 7. Scaling beyond 10,000 points

The brief also mentions a ~1,000,000-point variant. The design carries over with
these changes:

* store the graph in CSR arrays (NumPy) instead of Python lists and run
  Dijkstra in compiled code (`scipy.sparse.csgraph.dijkstra` with a `limit`, or
  a small Numba/C++ routine);
* snap with a KD-tree (`scipy.spatial.cKDTree`) instead of a linear scan;
* use A\* with the Euclidean distance to the nearest unexplored candidate as
  heuristic (admissible, because link lengths are Euclidean), or bidirectional
  search, to shrink the explored region further;
* keep per-category coordinate arrays so the radius filter touches only the
  relevant 1/8 of the data;
* stream-parse the link file and cache the CSR graph on disk keyed by content
  hash.

## 8. Running and deploying

```bash
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000
curl -F lat=0.5 -F long=0.5 -F cat=bank -F rad=0.2 -F link=@links.txt http://127.0.0.1:8000/search/
```

`Procfile`, `render.yaml` and a `Dockerfile` are included for hosting on Render,
Railway, Heroku-style platforms or any container host.

## 9. Files

| file                 | purpose                                              |
|----------------------|------------------------------------------------------|
| `engine.py`          | data loading, graph building, Dijkstra search         |
| `main.py`            | FastAPI application and request handling              |
| `tools/gen_links.py` | generate a test link file from the lattice            |
| `tools/check.py`     | cross-check engine against a naive reference          |
| `tools/api_test.py`  | end-to-end HTTP tests                                 |
| `requirements.txt`, `Procfile`, `render.yaml`, `Dockerfile` | deployment |
