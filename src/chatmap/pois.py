"""Collect amenity POIs for a bbox from Photon (komoot's OSM geocoder).

Why Photon rather than Overpass: the public Overpass instances return HTTP 406
for this host on every request including `/api/status`, so they are unusable
here. Photon is free, needs no key, and returns OSM `osm_key`/`osm_value` tags
plus coordinates, which is exactly what the access score needs.

Caveat: Photon is a geocoder, not a POI database. It indexes named/notable
objects, so unnamed bus stops are missing and dense areas are thinned. Treat
`transit` distances as an upper bound — if Photon says 900 m, the true nearest
stop is ≤ 900 m.
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request

PHOTON = "https://photon.komoot.io/api/"
UA = "chatmap-poi/0.1 (https://github.com/alx/chatmap)"

# Photon query term -> (osm_key, osm_value) that identifies the feature.
# Several entries can map to the same bucket; results are merged.
BUCKETS: dict[str, list[tuple[str, str]]] = {
    "transit": [
        ("railway", "station"),
        ("railway", "halt"),
        ("highway", "bus_stop"),
        ("public_transport", "station"),
        ("railway", "tram_stop"),
        ("amenity", "bus_station"),
    ],
    "hospital": [
        ("amenity", "hospital"),
        ("amenity", "clinic"),
        ("amenity", "doctors"),
    ],
    "konbini": [
        ("shop", "convenience"),
    ],
    "fresh": [
        ("shop", "greengrocer"),
        ("shop", "supermarket"),
        ("amenity", "marketplace"),
        ("shop", "butcher"),
        ("shop", "seafood"),
    ],
    "coffee": [
        ("amenity", "cafe"),
        ("shop", "coffee"),
    ],
    "restaurant": [
        ("amenity", "restaurant"),
        ("amenity", "fast_food"),
    ],
    "third_space": [
        ("amenity", "library"),
        ("amenity", "community_centre"),
        ("tourism", "museum"),
    ],
}

# Photon matches on names, not categories, and Japanese OSM data is named in
# Japanese: "convenience" returns 0 results in Kyoto while "セブンイレブン"
# returns 48. So each bucket lists Japanese terms (which actually hit) plus
# English fallbacks for areas tagged in Latin script.
QUERIES: dict[str, list[str]] = {
    "transit": [
        "駅", "バス停", "停留所",                       # station, bus stop
        "station", "bus stop",
    ],
    "hospital": [
        "病院", "医院", "クリニック", "診療所",          # hospital, clinic
        "hospital", "clinic",
    ],
    "konbini": [
        "セブンイレブン", "ローソン", "ファミリーマート", "コンビニ",
        "セブン-イレブン", "ミニストップ", "デイリーヤマザキ",
        "convenience",
    ],
    "fresh": [
        "スーパー", "青果", "市場", "精肉", "鮮魚",       # supermarket, produce, market
        "supermarket", "greengrocer", "market",
    ],
    "coffee": [
        "カフェ", "喫茶", "コーヒー",                    # cafe, coffee shop
        "cafe", "coffee",
    ],
    "restaurant": [
        "レストラン", "食堂", "ラーメン", "居酒屋",
        "restaurant",
    ],
    "third_space": [
        "図書館", "博物館", "公民館", "美術館",           # library, museum, community hall
        "library", "museum",
    ],
}

_cache: dict[str, list] = {}
_last = 0.0


def _throttle(min_interval: float = 0.6) -> None:
    global _last
    delta = time.time() - _last
    if delta < min_interval:
        time.sleep(min_interval - delta)
    _last = time.time()


def _get(params: dict) -> list[dict]:
    key = json.dumps(params, sort_keys=True)
    if key in _cache:
        return _cache[key]
    url = f"{PHOTON}?{urllib.parse.urlencode(params)}"
    _throttle()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        feats = data.get("features") or []
    except Exception:  # noqa: BLE001 - network is best effort
        feats = []
    _cache[key] = feats
    return feats


def collect(
    sw_lat: float, sw_lon: float, ne_lat: float, ne_lon: float, limit: int = 200
) -> dict[str, list[tuple[float, float]]]:
    """Return {bucket: [(lat, lon), …]} for the bbox."""
    bbox = f"{sw_lon},{sw_lat},{ne_lon},{ne_lat}"
    out: dict[str, list[tuple[float, float]]] = {}
    for bucket, accepted in BUCKETS.items():
        pts: dict[tuple[float, float], None] = {}
        for term in QUERIES[bucket]:
            for feat in _get({"q": term, "bbox": bbox, "limit": limit, "lang": "en"}):
                props = feat.get("properties") or {}
                k, v = props.get("osm_key"), props.get("osm_value")
                if (k, v) not in accepted:
                    continue
                geom = feat.get("geometry") or {}
                coords = geom.get("coordinates")
                if not coords or len(coords) < 2:
                    continue
                lon, lat = float(coords[0]), float(coords[1])
                if not (sw_lat <= lat <= ne_lat and sw_lon <= lon <= ne_lon):
                    continue
                pts[(round(lat, 6), round(lon, 6))] = None
        out[bucket] = list(pts)
    return out
