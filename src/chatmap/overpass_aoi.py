"""Query a scoped Overpass instance for amenity POIs, bounded to an AOI.

Everything here is deliberately bounded: every query carries a bbox or `around`
clause. That is the rule for this project — never ask for a country, never let
a self-hosted instance answer a planet-scale question.

The AOI is shared by the grid builder so the POIs and the scored cells always
cover exactly the same ground.
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass

# Kyoto–Shiga area of interest (the Mokomoko listing bbox).
AOI = {
    "name": "kyoto-shiga",
    "sw_lat": 34.94728540974337,
    "sw_lon": 135.582647370742,
    "ne_lat": 35.26654555475983,
    "ne_lon": 136.00758456890736,
}

# Overpass QL fragment per bucket: node/way/relation so stations mapped as
# ways and markets mapped as areas are not missed.
BUCKETS: dict[str, str] = {
    "transit": 'nwr["railway"~"^(station|halt|tram_stop)$"]; '
               'nwr["highway"="bus_stop"]; '
               'nwr["amenity"="bus_station"]; '
               'nwr["public_transport"="station"];',
    "hospital": 'nwr["amenity"~"^(hospital|clinic|doctors)$"]; '
                'nwr["healthcare"~"^(hospital|clinic)$"];',
    "konbini": 'nwr["shop"="convenience"]; '
               'nwr["brand"~"^(7-Eleven|Lawson|FamilyMart|Ministop|Daily Yamazaki|セブン|ローソン|ファミリーマート)$"];',
    "fresh": 'nwr["shop"~"^(greengrocer|supermarket|butcher|seafood|market)$"]; '
             'nwr["amenity"="marketplace"];',
    "coffee": 'nwr["amenity"="cafe"]; nwr["shop"="coffee"];',
    "restaurant": 'nwr["amenity"~"^(restaurant|fast_food|pub|bar)$"];',
    "third_space": 'nwr["amenity"~"^(library|community_centre)$"]; '
                   'nwr["tourism"="museum"];',
}


def build_query(bucket: str, aoi: dict | None = None, timeout: int = 180) -> str:
    """Quadrant-style query scoped to the AOI bbox.

    `nwr` covers nodes, ways and relations in one clause. `out center` gives a
    single representative coordinate for ways/relations so every feature lands
    as one point.
    """
    a = aoi or AOI
    bbox = f"{a['sw_lat']},{a['sw_lon']},{a['ne_lat']},{a['ne_lon']}"
    # Every clause must be bounded by the AOI bbox: an unbounded `nwr` would
    # scan the whole database and Overpass rejects it (HTTP 400) rather than
    # letting a query take the instance down.
    clauses = []
    for raw in BUCKETS[bucket].split(";"):
        clause = raw.strip()
        if not clause:
            continue
        # Correct Overpass QL: filters first, then the (bbox) selector.
        clauses.append(f"{clause}({bbox});")
    body = "\n".join(f"  {c}" for c in clauses)
    return f"[out:json][timeout:{timeout}];\n(\n{body}\n);\nout center tags;"


@dataclass
class OverpassResult:
    features: dict[str, list[tuple[float, float]]]
    stats: dict[str, int]
    elapsed_s: float


class OverpassClient:
    """Minimal client for a self-hosted (or otherwise reachable) instance."""

    def __init__(self, url: str = "http://localhost:12345/api/interpreter", timeout: float = 300):
        self.url = url
        self.timeout = timeout

    def query(self, qml: str) -> dict:
        data = urllib.parse.urlencode({"data": qml}).encode()
        req = urllib.request.Request(
            self.url,
            data=data,
            headers={
                "User-Agent": "chatmap-aoi/0.1 (self-hosted overpass)",
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310
            return json.loads(resp.read().decode("utf-8", errors="replace"))

    @staticmethod
    def _points(payload: dict) -> list[tuple[float, float]]:
        pts, seen = [], set()
        for el in payload.get("elements") or []:
            lat = el.get("lat")
            lon = el.get("lon")
            if lat is None or lon is None:
                centre = el.get("center") or {}
                lat, lon = centre.get("lat"), centre.get("lon")
            if lat is None or lon is None:
                continue
            key = (round(lat, 6), round(lon, 6))
            if key in seen:
                continue
            seen.add(key)
            pts.append(key)
        return pts

    def collect(self, aoi: dict | None = None) -> OverpassResult:
        t0 = time.time()
        features: dict[str, list[tuple[float, float]]] = {}
        stats: dict[str, int] = {}
        for bucket in BUCKETS:
            payload = self.query(build_query(bucket, aoi))
            pts = self._points(payload)
            features[bucket] = pts
            stats[bucket] = len(payload.get("elements") or [])
        return OverpassResult(features=features, stats=stats,
                              elapsed_s=round(time.time() - t0, 1))
