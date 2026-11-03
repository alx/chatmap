"""Score candidate homes on liveability criteria.

For each candidate point we ask "what can I walk to?" and turn the answers into
per-criterion sub-scores and one overall number. Everything is distance based:
the same grid is used in a dense city and in the countryside, so a house in
rural Hyogo is not punished for failing to look like central Fukuoka — it simply
scores low on the things it genuinely lacks.

Criteria (weights sum to 100):

    hospital      18   nearest hospital / clinic
    transit       15   nearest train station or bus stop
    groceries     15   convenience store AND fresh food; a konbini alone is not food
    seaside       12   walking distance to the coast
    restaurants   10
    third space    8   libraries, museums, community centres — not shops
    coffee         7
    coworking      5

Sub-scores are linear between an "ideal" and a "dead" distance, so the score
degrades gradually instead of falling off a cliff.
"""

from __future__ import annotations

import json
import math
import re
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field, asdict

NOMINATIM = "https://nominatim.openstreetmap.org/search"
UA = "chatmap-livability/0.1 (https://github.com/alx/chatmap)"

# criterion -> (label, weight, ideal_m, dead_m, query templates)
CRITERIA: dict[str, dict] = {
    "hospital": {
        "label": "Hospital / clinic",
        "weight": 18,
        "ideal_m": 800,
        "dead_m": 8000,
        "queries": ["hospital", "clinic", "doctor"],
    },
    "transit": {
        "label": "Public transport",
        "weight": 15,
        "ideal_m": 400,
        "dead_m": 4000,
        "queries": ["railway station", "train station", "bus stop", "bus station", "station"],
        "allow_types": {"station", "halt", "bus_stop", "bus_station", "tram_stop",
                        "subway_entrance", "public_transport_station", "train_station"},
    },
    "konbini": {
        "label": "Konbini",
        "weight": 11,
        "ideal_m": 300,
        "dead_m": 3000,
        "queries": ["convenience store", "konbini"],
    },
    "fresh": {
        "label": "Fresh market / greengrocer",
        "weight": 12,
        "ideal_m": 600,
        "dead_m": 5000,
        "queries": ["greengrocer", "marketplace", "supermarket", "butcher"],
    },
    "seaside": {
        "label": "Seaside (≤15 min)",
        "weight": 12,
        # ~1.2 km ≈ 15 min at a normal walking pace
        "ideal_m": 500,
        "dead_m": 1200,
        "queries": ["beach", "seaside", "coast", "coastline", "harbour", "harbor",
                    "marina", "port", "bay", "inlet", "pier", "海岸", "海水浴場",
                    "港", "浜", "海"],
        # Only real water-edge features count; "シーサイドホテル" is not a beach.
        "allow_types": {"beach", "beach_resort", "harbour", "marina", "bay",
                        "strait", "coastline", "water", "sea", "shore"},
    },
    "restaurants": {
        "label": "Restaurants",
        "weight": 10,
        "ideal_m": 300,
        "dead_m": 3000,
        "queries": ["restaurant", "ramen", "izakaya"],
    },
    "third_space": {
        "label": "Third place (library, museum)",
        "weight": 8,
        "ideal_m": 700,
        "dead_m": 6000,
        "queries": ["library", "museum", "community centre"],
    },
    "coffee": {
        "label": "Coffee",
        "weight": 7,
        "ideal_m": 400,
        "dead_m": 3000,
        "queries": ["cafe", "coffee"],
    },
    "coworking": {
        "label": "Coworking",
        "weight": 7,
        "ideal_m": 800,
        "dead_m": 8000,
        "queries": ["coworking", "coworking space"],
    },
}

_cache: dict[str, list] = {}
_last = 0.0


def _throttle(min_interval: float = 1.1) -> None:
    global _last
    delta = time.time() - _last
    if delta < min_interval:
        time.sleep(min_interval - delta)
    _last = time.time()


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _search(query: str, lat: float, lon: float, radius_m: float) -> list[dict]:
    """Nominatim free-text search inside a box centred on the point."""
    key = f"{query}|{lat:.4f}|{lon:.4f}"
    if key in _cache:
        return _cache[key]

    dlat = radius_m / 111_320.0
    dlon = radius_m / (111_320.0 * max(0.2, math.cos(math.radians(lat))))
    viewbox = f"{lon - dlon},{lat + dlat},{lon + dlon},{lat - dlat}"
    url = f"{NOMINATIM}?" + urllib.parse.urlencode(
        {"q": query, "format": "json", "limit": 40, "viewbox": viewbox, "bounded": 1}
    )
    _throttle()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=25) as resp:  # noqa: S310
            results = json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception:  # noqa: BLE001 - network is best effort
        results = []
    _cache[key] = results
    return results


def nearest(label: str, queries: list[str], lat: float, lon: float,
            radius_m: float, allow_types: set[str] | None = None) -> dict | None:
    """Closest result across several query phrasings."""
    best = None
    for q in queries:
        for r in _search(q, lat, lon, radius_m):
            if allow_types is not None and (r.get("type") or "") not in allow_types:
                continue
            try:
                rlat, rlon = float(r["lat"]), float(r["lon"])
            except (KeyError, TypeError, ValueError):
                continue
            d = haversine_m(lat, lon, rlat, rlon)
            if d <= radius_m and (best is None or d < best["distance_m"]):
                raw_name = (r.get("name") or "").strip()
                if not raw_name:
                    # display_name is "street, district, city, pref, …"; the
                    # street is the least useful part, so prefer the district.
                    parts = [x.strip() for x in (r.get("display_name") or "").split(",")]
                    parts = [x for x in parts if x and not re.match(r"^[\d国道県道市道]", x)]
                    raw_name = parts[0] if parts else (r.get("type") or "").replace("_", " ")
                best = {
                    "name": raw_name[:80],
                    "distance_m": round(d),
                    "lat": round(rlat, 6),
                    "lon": round(rlon, 6),
                    "matched_query": q,
                }
    return best


def _sub_score(distance_m: float | None, ideal: float, dead: float) -> int:
    """0–100, linear between ideal (100) and dead (0)."""
    if distance_m is None:
        return 0
    if distance_m <= ideal:
        return 100
    if distance_m >= dead:
        return 0
    return int(round(100 * (dead - distance_m) / (dead - ideal)))


@dataclass
class Score:
    lat: float
    lon: float
    criteria: dict = field(default_factory=dict)
    total: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


def score_point(lat: float, lon: float, radius_m: float = 5000.0,
                weights: dict[str, int] | None = None) -> Score:
    """Evaluate every criterion around one point and combine into a total."""
    weights = weights or {k: v["weight"] for k, v in CRITERIA.items()}
    result = Score(lat=lat, lon=lon)
    total = 0.0
    weight_sum = 0.0

    for key, spec in CRITERIA.items():
        found = nearest(spec["label"], spec["queries"], lat, lon, radius_m,
                        spec.get("allow_types"))
        dist = found["distance_m"] if found else None
        sub = _sub_score(dist, spec["ideal_m"], spec["dead_m"])
        weight = weights.get(key, spec["weight"])
        result.criteria[key] = {
            "label": spec["label"],
            "weight": weight,
            "distance_m": dist,
            "nearest": found,
            "score": sub,
            "walk_min": round(dist / 80) if dist is not None else None,  # ~80 m/min
        }
        total += sub * weight
        weight_sum += weight

    result.total = int(round(total / weight_sum)) if weight_sum else 0
    return result


def describe(score: Score) -> str:
    """Human-readable block for a marker description."""
    lines = [f"livability score: {score.total}/100", ""]
    for key, spec in sorted(CRITERIA.items(),
                            key=lambda kv: -score.criteria[kv[0]]["weight"]):
        c = score.criteria[key]
        if c["distance_m"] is None:
            lines.append(f"{c['label']:32} —      (none found)  {c['score']:3}/100")
        else:
            near = c["nearest"]["name"][:38] if c["nearest"] else ""
            lines.append(
                f"{c['label']:32} {c['distance_m']:5}m "
                f"~{c['walk_min']:3}min  {c['score']:3}/100  {near}"
            )
    return "\n".join(lines)
