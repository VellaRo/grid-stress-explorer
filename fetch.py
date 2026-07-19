"""
fetch.py — pull real German electricity data from the Fraunhofer ISE
Energy-Charts API (https://api.energy-charts.info). Free, no API key.

Endpoints used (both verified live 2026-07):
  - /public_power   generation stack + load + residual load + renewable share
  - /price          spot electricity price (EUR/MWh)

Data is cached to disk as JSON so we don't hammer the API on every rerun.
"""
from __future__ import annotations

import json
import os
import time
from datetime import date, datetime
from pathlib import Path

import requests

API_BASE = "https://api.energy-charts.info"
CACHE_DIR = Path(__file__).parent / "cache"
CACHE_DIR.mkdir(exist_ok=True)


def _cache_path(kind: str, start: str, end: str) -> Path:
    return CACHE_DIR / f"{kind}_{start}_{end}.json"


def _fetch_json(endpoint: str, params: dict, cache_key: str) -> dict:
    params = {k: v for k, v in params.items() if v is not None}
    cached = _cache_path(cache_key, params.get("start", ""), params.get("end", ""))
    if cached.exists():
        # cache valid for 24h
        if (time.time() - cached.stat().st_mtime) < 86400:
            return json.loads(cached.read_text())
    resp = requests.get(f"{API_BASE}/{endpoint}", params=params, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    cached.write_text(json.dumps(data, indent=2))
    return data


def fetch_power(start: date, end: date) -> dict:
    """Generation stack, load, residual load, renewable share (15-min resolution)."""
    return _fetch_json(
        "public_power",
        {"country": "de", "start": start.isoformat(), "end": end.isoformat()},
        "power",
    )


def fetch_price(start: date, end: date) -> dict:
    """Spot electricity price EUR/MWh (15-min resolution)."""
    return _fetch_json(
        "price",
        {"country": "de", "start": start.isoformat(), "end": end.isoformat()},
        "price",
    )


def fetch_range(start: date, end: date) -> dict:
    """Convenience: returns a merged dict with both power and price."""
    power = fetch_power(start, end)
    try:
        price = fetch_price(start, end)
    except requests.HTTPError:
        price = {"unix_seconds": [], "price": [], "unit": "EUR_MWh"}
    return {"power": power, "price": price}


if __name__ == "__main__":
    s = date(2026, 7, 15)
    e = date(2026, 7, 18)
    d = fetch_range(s, e)
    print("power timestamps:", len(d["power"].get("unix_seconds", [])))
    print("price timestamps:", len(d["price"].get("unix_seconds", [])))
    print("series:", [pt["name"] for pt in d["power"].get("production_types", [])])
