import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fastapi.testclient import TestClient

import main


def run(links_path):
    client = TestClient(main.app)
    with open(links_path, "rb") as fh:
        link_bytes = fh.read()
    link_text = link_bytes.decode()
    q = {"lat": "0.5", "long": "0.5", "cat": "bank", "rad": "0.2"}

    r1 = client.post("/search/", data=q, files={"link": ("links.txt", link_bytes, "text/plain")})
    assert r1.status_code == 200, r1.text
    ids1 = r1.json()["ids"]
    assert len(ids1) == 10 and r1.json()["graph"]["source"] == "link file", r1.json()["graph"]
    print("multipart  ->", ids1, f'{r1.json()["elapsed_ms"]} ms')

    r2 = client.post("/search/", data={**q, "link": link_text})
    assert r2.status_code == 200 and r2.json()["ids"] == ids1, r2.text
    print("form/inline->", r2.json()["ids"])

    r3 = client.post("/search/", json={"lat": 0.5, "long": 0.5, "cat": "bank", "rad": 0.2, "link": link_text})
    assert r3.status_code == 200 and r3.json()["ids"] == ids1, r3.text
    print("json       ->", r3.json()["ids"])

    r4 = client.get("/search", params=q)
    assert r4.status_code == 200 and r4.json()["graph"]["source"].startswith("default"), r4.text
    print("get/lattice->", r4.json()["ids"])

    assert client.get("/search/", params={"lat": "0.5", "long": "0.5", "cat": "bank"}).status_code == 400
    assert client.get("/search/", params={**q, "cat": "spaceport"}).status_code == 400
    assert client.get("/search/", params={**q, "lat": "abc"}).status_code == 400
    assert client.get("/health").status_code == 200
    assert "Proximity" in client.get("/").text
    print("all API checks passed")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--links", default="links_sample.txt")
    run(ap.parse_args().links)
