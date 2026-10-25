# Proximity Search API

Recommends the 10 closest locations of a given category, where *closest* means
**road/grid traversal distance** over the links listed in a link file, and the
candidate must lie inside a **circular (Euclidean) radius** of the query point.

**Live deployment:** `http://10.1.75.51:3287/search/` (health check at `/health`, interactive docs at `/docs`).

Dataset: `locations.csv` (10,000 points on a 100 x 100 lattice, 8 categories).

## Quick start

```bash
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000
```

Open <http://127.0.0.1:8000/> for a test form, or <http://127.0.0.1:8000/docs> for the OpenAPI UI.

## Endpoint

`POST /search/` (also accepts `GET`, and `/search` without the trailing slash)

| field  | type   | meaning                                                             |
|--------|--------|---------------------------------------------------------------------|
| `lat`  | float  | current latitude (0..1)                                             |
| `long` | float  | current longitude (0..1)                                            |
| `cat`  | string | category to look for (`bank`, `cafe`, `hospital`, `park`, `pharmacy`, `restaurant`, `school`, `store`) |
| `rad`  | float  | search radius, straight-line distance from (lat, long), inclusive    |
| `link` | file / URL / text | road links, one `a b` pair of location IDs per line       |

Optional: `k` (default 10), `mode` (`weighted` = sum of link lengths, default; `hops` = link count).

`link` can be supplied three ways:

```bash
# 1. multipart file upload
curl -F lat=0.5 -F long=0.5 -F cat=bank -F rad=0.2 -F link=@links.txt http://HOST/search/

# 2. a URL to the raw text file
curl -d "lat=0.5&long=0.5&cat=bank&rad=0.2&link=https://example.com/links.txt" http://HOST/search/

# 3. the link text itself (JSON body)
curl -H "Content-Type: application/json" \
     -d '{"lat":0.5,"long":0.5,"cat":"bank","rad":0.2,"link":"1 2\n2 3\n..."}' http://HOST/search/
```

If `link` is omitted, the full 4-neighbour lattice is used.

Python client:

```python
import requests
with open("links.txt", "rb") as fh:
    r = requests.post("http://HOST/search/",
                      data={"lat": 0.5, "long": 0.5, "cat": "bank", "rad": 0.2},
                      files={"link": fh})
print(r.json()["ids"])
```

### Response

```json
{
  "ids": [4850, 4948, 4648, 4947, 4753, 4451, 4654, 4755, 4955, 5155],
  "count": 10,
  "query": {"lat": 0.5, "long": 0.5, "cat": "bank", "rad": 0.2, "k": 10, "mode": "weighted", "link": "upload:links.txt"},
  "start_node": {"id": 4950, "lat": 0.494949, "lon": 0.494949, "category": "restaurant", "snap_distance": 0.00714},
  "results": [
    {"id": 4850, "lat": 0.484848, "lon": 0.494949, "category": "bank",
     "grid_distance": 0.010101, "hops": 1, "euclidean_distance": 0.0159}
  ],
  "graph": {"source": "link file", "edges": 16787, "skipped_lines": 0, "unknown_id_links": 0, "zero_based_ids_detected": false},
  "stats": {"candidates_in_radius": 165, "nodes_settled": 79, "mode": "weighted"},
  "elapsed_ms": 1.2
}
```

`ids` is the answer, ordered nearest first. Everything else is diagnostic.
Errors (missing field, unknown category, non-numeric value, unreachable URL) return HTTP 400 with a `detail` message.

## How it works

1. **Snap**: the grid node nearest to `(lat, long)` is the start.
2. **Route**: Dijkstra from the start over the link graph. Each link's weight is the Euclidean length between its two endpoints.
3. **Filter**: keep nodes whose category matches and whose straight-line distance from the query point is `<= rad`.
4. **Rank**: sort by grid distance (ties by ID), return the first 10. Dijkstra stops as soon as the 10th distance is settled and all ties are collected.

Parsed link files are cached by content hash, so repeated queries with the same file skip the parse.
See [REPORT.md](REPORT.md) for details, verification and assumptions.

## Run with Docker

The whole stack (nginx front end + API) starts with one command:

```bash
docker compose up --build -d          # http://localhost:3000/  (set WEB_PORT to change the port)
docker compose --profile test run --rm smoke   # optional end-to-end test
docker compose down
```

`web` (nginx, port 3000) serves a static search page and proxies `/search/`, `/health` and `/docs`
to `api` (FastAPI), which is only reachable on the internal compose network.
See [DOCKER_REPORT.md](DOCKER_REPORT.md) for the design choices.

## Tools

```bash
python tools/gen_links.py --drop 0.15 --out links_sample.txt   # make a test link file (lattice minus 15 % of links)
python tools/check.py --links links_sample.txt --queries 300   # compare engine vs naive reference
python tools/api_test.py --links links_sample.txt              # exercise the HTTP layer
```

## Deployment

The app is a plain ASGI app, so any Python host works:

* **Render** (free tier): push to GitHub, "New Web Service", it picks up `render.yaml`.
* **Railway / Heroku-style**: `Procfile` is included.
* **Docker**: `docker build -t proximity . && docker run -p 8000:8000 proximity`
* **Quick public URL from your laptop**: run uvicorn locally, then `ngrok http 8000` (or `cloudflared tunnel --url http://localhost:8000`).

Environment variables: `LOCATIONS_CSV` (path to the CSV, default `./locations.csv`), `PORT`,
`ALLOW_LOCAL_LINK_PATHS=1` (lets `link` be a path on the server's disk, for local testing only).
