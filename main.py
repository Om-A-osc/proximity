from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, Optional, Tuple
from urllib.parse import parse_qs

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from starlette.datastructures import UploadFile

from engine import LocationIndex

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("proximity")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.environ.get("LOCATIONS_CSV", os.path.join(BASE_DIR, "locations.csv"))
ALLOW_LOCAL_LINK_PATHS = os.environ.get("ALLOW_LOCAL_LINK_PATHS", "0") == "1"
DEFAULT_K = 10

t_load = time.perf_counter()
index = LocationIndex(CSV_PATH)
index.lattice_graph()
log.info("loaded %d locations from %s in %.0f ms", index.n, CSV_PATH, (time.perf_counter() - t_load) * 1000)

app = FastAPI(
    title="Proximity Search API",
    version="1.0.0",
    description="Returns the 10 closest locations (by road/grid distance) of a category "
                "within a circular radius, given a road-link file.",
)

ALIASES = {
    "lat":  ("lat", "latitude"),
    "long": ("long", "lon", "lng", "longitude"),
    "cat":  ("cat", "category"),
    "rad":  ("rad", "radius"),
    "link": ("link", "links", "link_file", "linkage", "file"),
    "k":    ("k", "n", "top", "limit"),
    "mode": ("mode", "metric"),
}


def pick(params: Dict[str, Any], name: str) -> Any:
    for alias in ALIASES[name]:
        if alias in params:
            v = params[alias]
            if v is None or (isinstance(v, str) and v.strip() == ""):
                continue
            return v
    return None


def to_float(value: Any, name: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail=f"field '{name}' must be a number, got {value!r}")


async def collect_params(request: Request) -> Dict[str, Any]:
    params: Dict[str, Any] = dict(request.query_params)
    if request.method != "POST":
        return params
    ctype = request.headers.get("content-type", "").lower()
    if "application/json" in ctype:
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="request body is not valid JSON")
        if isinstance(body, dict):
            params.update(body)
    elif "multipart/form-data" in ctype or "application/x-www-form-urlencoded" in ctype:
        form = await request.form()
        for key in form.keys():
            params[key] = form[key]
    else:
        raw = (await request.body()).decode("utf-8", "replace").strip()
        if raw:
            try:
                body = json.loads(raw)
                if isinstance(body, dict):
                    params.update(body)
            except ValueError:
                for key, vals in parse_qs(raw).items():
                    params[key] = vals[-1]
    return params


async def load_link(value: Any) -> Tuple[Optional[str], str]:
    if value is None:
        return None, "none"
    if isinstance(value, UploadFile):
        data = await value.read()
        text = data.decode("utf-8", "replace")
        return (text, f"upload:{value.filename or 'link'}") if text.strip() else (None, "none")
    if isinstance(value, (bytes, bytearray)):
        text = bytes(value).decode("utf-8", "replace")
        return (text, "bytes") if text.strip() else (None, "none")

    s = str(value).strip()
    if not s:
        return None, "none"
    single_line = "\n" not in s and "\r" not in s
    if single_line and s.lower().startswith(("http://", "https://")):
        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                resp = await client.get(s)
                resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=400, detail=f"could not download link file: {exc}")
        return resp.text, f"url:{s}"
    if single_line and ALLOW_LOCAL_LINK_PATHS and os.path.isfile(s):
        with open(s, encoding="utf-8", errors="replace") as fh:
            return fh.read(), f"path:{s}"
    return s, "inline"


@app.api_route("/search/", methods=["GET", "POST"])
@app.api_route("/search", methods=["GET", "POST"], include_in_schema=False)
async def search(request: Request):
    t0 = time.perf_counter()
    params = await collect_params(request)

    lat_v, lon_v = pick(params, "lat"), pick(params, "long")
    cat_v, rad_v = pick(params, "cat"), pick(params, "rad")
    missing = [n for n, v in (("lat", lat_v), ("long", lon_v), ("cat", cat_v), ("rad", rad_v)) if v is None]
    if missing:
        raise HTTPException(status_code=400, detail="missing required field(s): " + ", ".join(missing))

    lat, lon, rad = to_float(lat_v, "lat"), to_float(lon_v, "long"), to_float(rad_v, "rad")
    cat = str(cat_v).strip()
    k = int(to_float(pick(params, "k") or DEFAULT_K, "k"))
    mode = str(pick(params, "mode") or "weighted").strip().lower()

    link_text, link_source = await load_link(pick(params, "link"))
    graph = index.lattice_graph() if link_text is None else index.graph_from_links(link_text)

    try:
        res = index.search(lat, lon, cat, rad, graph, k=k, mode=mode)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    elapsed_ms = (time.perf_counter() - t0) * 1000
    log.info("search lat=%s long=%s cat=%s rad=%s link=%s -> %s (%.1f ms)",
             lat, lon, cat, rad, link_source, res["ids"], elapsed_ms)
    return {
        "ids": res["ids"],
        "count": len(res["ids"]),
        "query": {"lat": lat, "long": lon, "cat": cat.lower(), "rad": rad, "k": k, "mode": mode,
                  "link": link_source},
        "start_node": res["start"],
        "results": res["hits"],
        "graph": graph.info(),
        "stats": res["stats"],
        "elapsed_ms": round(elapsed_ms, 2),
    }


@app.get("/health")
async def health():
    return {"status": "ok", "locations": index.n, "categories": index.categories}


INDEX_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>Proximity Search API</title>
<style>
 body{font-family:system-ui,sans-serif;max-width:760px;margin:2rem auto;padding:0 1rem;color:#222}
 label{display:block;margin:.7rem 0 .2rem;font-weight:600}
 input,select{width:100%;padding:.45rem;box-sizing:border-box}
 button{margin-top:1rem;padding:.55rem 1.2rem;font-size:1rem}
 pre{background:#f4f4f6;padding:1rem;overflow:auto;border-radius:6px}
 code{background:#f4f4f6;padding:0 .3em}
</style></head>
<body>
<h1>Proximity Search API</h1>
<p>Endpoint: <code>POST /search/</code> with fields <code>lat, long, cat, rad, link</code>
(multipart form, urlencoded form, JSON, or GET query string). Docs at <a href="/docs">/docs</a>.</p>
<form id="f">
 <label>lat</label><input name="lat" value="0.5" required>
 <label>long</label><input name="long" value="0.5" required>
 <label>cat</label><select name="cat">__CATS__</select>
 <label>rad</label><input name="rad" value="0.2" required>
 <label>link (txt file, "a b" per line; leave empty to use the full lattice)</label>
 <input type="file" name="link" accept=".txt,text/plain">
 <button type="submit">Search</button>
</form>
<pre id="out"></pre>
<script>
document.getElementById('f').addEventListener('submit', async (e) => {
  e.preventDefault();
  const fd = new FormData(e.target);
  const f = fd.get('link');
  if (!f || !f.name) fd.delete('link');
  const out = document.getElementById('out');
  out.textContent = 'searching...';
  const r = await fetch('/search/', {method: 'POST', body: fd});
  out.textContent = JSON.stringify(await r.json(), null, 2);
});
</script>
</body></html>"""


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def home():
    options = "".join(f'<option value="{c}">{c}</option>' for c in index.categories)
    return INDEX_HTML.replace("__CATS__", options)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.environ.get("PORT", "8000")), reload=False)
