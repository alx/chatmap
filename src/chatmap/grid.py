"""Build a scored grid over an area of interest.

Each cell carries a suitability score for "would I want to live around here?",
combining terrain (slope, elevation band) and access (distance to the places
that matter). The grid is emitted as GeoJSON so it can be rendered as a
transparent blue→green→yellow→red heat layer.

Design choices worth knowing:

* **Value is optional detail, not the driver.** A cell with no listing nearby
  can still score well; listings only bias a cell slightly upward.
* **Terrain matters in Japan.** A 30° slope means retaining walls and no car
  access; valley floors flood. Cells outside a livable elevation band and on
  steep ground are penalised regardless of amenities.
* **Access is measured in straight-line metres to the nearest known place**,
  using whatever point set is supplied (Mokomoko listings, OSM POIs, …).
* Every score is decomposed into named components so the map can explain
  itself instead of showing a mysterious colour.
"""

from __future__ import annotations

import array
import io
import json
import math
import urllib.request
import zlib
from dataclasses import dataclass, field

DEM_TILE_URL = "https://cyberjapandata.gsi.go.jp/xyz/dem_png/{z}/{x}/{y}.png"
DEM_UA = "chatmap-grid/0.1 (https://github.com/alx/chatmap)"
NO_DATA = 0x800000  # GSI marks voids with this 24-bit value


# ---------------------------------------------------------------------------
# Tile maths
# ---------------------------------------------------------------------------


def lonlat_to_tile(lon: float, lat: float, z: int) -> tuple[int, int]:
    n = 2 ** z
    x = int((lon + 180.0) / 360.0 * n)
    y = int(
        (1.0 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat)))
         / math.pi) / 2.0 * n
    )
    return x, y


def tile_bounds(x: int, y: int, z: int) -> tuple[float, float, float, float]:
    """(west, north, east, south) of a tile, in degrees."""

    def tile_lon(tx: int) -> float:
        return tx / (2 ** z) * 360.0 - 180.0

    def tile_lat(ty: int) -> float:
        n = math.pi - 2.0 * math.pi * ty / (2 ** z)
        return math.degrees(math.atan(math.sinh(n)))

    return tile_lon(x), tile_lat(y), tile_lon(x + 1), tile_lat(y + 1)


# ---------------------------------------------------------------------------
# DEM
# ---------------------------------------------------------------------------


class Dem:
    """Lazily-fetched GSI DEM tiles (0.01 m vertical resolution, z13 ≈ 10 m/px)."""

    def __init__(self, zoom: int = 13) -> None:
        self.zoom = zoom
        self._tiles: dict[tuple[int, int], dict | None] = {}

    def _load(self, tx: int, ty: int) -> dict | None:
        key = (tx, ty)
        if key in self._tiles:
            return self._tiles[key]
        url = DEM_TILE_URL.format(z=self.zoom, x=tx, y=ty)
        tile = None
        try:
            req = urllib.request.Request(url, headers={"User-Agent": DEM_UA})
            with urllib.request.urlopen(req, timeout=25) as resp:  # noqa: S310
                data = resp.read()
            tile = _decode_dem_png(data)
        except Exception:  # noqa: BLE001 - missing tiles are normal at sea/edges
            tile = None
        self._tiles[key] = tile
        return tile

    def elevation(self, lat: float, lon: float) -> float | None:
        tx, ty = lonlat_to_tile(lon, lat, self.zoom)
        tile = self._load(tx, ty)
        if not tile:
            return None
        w, n_, e, s = tile_bounds(tx, ty, self.zoom)
        px = int((lon - w) / (e - w) * tile["w"])
        py = int((n_ - lat) / (n_ - s) * tile["h"])
        px = min(max(px, 0), tile["w"] - 1)
        py = min(max(py, 0), tile["h"] - 1)
        return tile["data"][py * tile["w"] + px]

    def slope_deg(self, lat: float, lon: float, step_m: float = 30.0) -> float | None:
        """Approximate slope from a 3×3 elevation neighbourhood."""
        dlat = step_m / 111_320.0
        dlon = step_m / (111_320.0 * max(0.2, math.cos(math.radians(lat))))
        h = self.elevation(lat, lon)
        if h is None:
            return None
        samples = []
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                e = self.elevation(lat + dy * dlat, lon + dx * dlon)
                if e is not None:
                    samples.append(abs(e - h))
        if not samples:
            return None
        grade = (max(samples) / step_m)
        return math.degrees(math.atan(grade))


def _decode_dem_png(data: bytes) -> dict | None:
    """Decode a GSI DEM PNG to a flat float array of metres.

    Elevation = (R<<16 | G<<8 | B) * 0.01. 0x800000 means "no data".
    """
    try:
        from PIL import Image  # noqa: PLC0415
    except ImportError:  # pragma: no cover
        return None
    try:
        im = Image.open(io.BytesIO(data)).convert("RGB")
    except Exception:  # noqa: BLE001
        return None

    w, h = im.size
    raw = im.tobytes()
    out = array.array("f", bytes(4 * w * h))
    for i in range(w * h):
        r, g, b = raw[3 * i], raw[3 * i + 1], raw[3 * i + 2]
        v = (r << 16) | (g << 8) | b
        out[i] = -9999.0 if v == NO_DATA else v * 0.01
    return {"w": w, "h": h, "data": out}


# ---------------------------------------------------------------------------
# Grid
# ---------------------------------------------------------------------------

M_PER_DEG_LAT = 111_320.0

# Scoring bands. Each returns 0..1.
DEFAULT_WEIGHTS = {
    "terrain": 30,
    "access": 40,
    "supply": 20,
    "scenery": 10,
}


def _band_score(value: float | None, ideal: float, dead: float) -> float:
    """1.0 at/below ideal, 0.0 at/above dead, linear between."""
    if value is None:
        return 0.0
    if value <= ideal:
        return 1.0
    if value >= dead:
        return 0.0
    return (dead - value) / (dead - ideal)


@dataclass
class Cell:
    lat: float
    lon: float
    elevation: float | None
    slope: float | None
    terrain: float
    access: float
    supply: float
    scenery: float
    total: int
    extras: dict = field(default_factory=dict)


class GridBuilder:
    """Grid over a bbox, scored per cell (default cell: 10 km)."""

    def __init__(
        self,
        bbox: tuple[float, float, float, float],
        cell_m: float = 10_000.0,
        dem: Dem | None = None,
        weights: dict[str, float] | None = None,
    ) -> None:
        # bbox = (sw_lat, sw_lon, ne_lat, ne_lon)
        self.sw_lat, self.sw_lon, self.ne_lat, self.ne_lon = bbox
        self.cell_m = cell_m
        self.dem = dem or Dem()
        self.weights = weights or dict(DEFAULT_WEIGHTS)

        mid_lat = (self.sw_lat + self.ne_lat) / 2
        self.dlat = cell_m / M_PER_DEG_LAT
        self.dlon = cell_m / (M_PER_DEG_LAT * max(0.2, math.cos(math.radians(mid_lat))))
        self.nrows = max(1, int((self.ne_lat - self.sw_lat) / self.dlat))
        self.ncols = max(1, int((self.ne_lon - self.sw_lon) / self.dlon))

    # -- access: distance to nearest point of each kind ---------------------

    @staticmethod
    def _nearest_m(lat: float, lon: float, points: list[tuple[float, float]]) -> float | None:
        best = None
        for plat, plon in points:
            dlat = (plat - lat) * M_PER_DEG_LAT
            dlon = (plon - lon) * M_PER_DEG_LAT * math.cos(math.radians(lat))
            d = math.hypot(dlat, dlon)
            if best is None or d < best:
                best = d
        return best

    def _access_score(self, lat: float, lon: float, pois: dict) -> tuple[float, dict]:
        """Weighted mix of "distance to the things that matter"."""
        parts: dict[str, float | None] = {}
        score = 0.0
        total_w = 0.0
        specs = {
            "transit": (pois.get("transit"), 600, 5000, 0.30),
            "hospital": (pois.get("hospital"), 1500, 12000, 0.25),
            "konbini": (pois.get("konbini"), 400, 4000, 0.20),
            "fresh": (pois.get("fresh"), 800, 6000, 0.15),
            "coffee": (pois.get("coffee"), 600, 4000, 0.10),
        }
        for name, (pts, ideal, dead, w) in specs.items():
            if not pts:
                continue
            d = self._nearest_m(lat, lon, pts)
            parts[name] = d
            score += _band_score(d, ideal, dead) * w
            total_w += w
        return (score / total_w if total_w else 0.0), parts

    def build(self, pois: dict | None = None) -> list[Cell]:
        pois = pois or {}
        listings = pois.get("listing") or []
        cells: list[Cell] = []

        for r in range(self.nrows):
            lat = self.sw_lat + (r + 0.5) * self.dlat
            for c in range(self.ncols):
                lon = self.sw_lon + (c + 0.5) * self.dlon

                elev = self.dem.elevation(lat, lon)
                slope = self.dem.slope_deg(lat, lon)

                # Terrain: avoid steep ground and both flood-prone and
                # inconveniently high sites.
                slope_s = 1.0 - _band_score(slope, 8.0, 25.0) if slope is not None else 0.5
                if elev is None or elev <= -9999:
                    elev_s = 0.5
                elif elev < 3:
                    elev_s = 0.2          # river plain: flood risk
                elif elev > 400:
                    elev_s = 0.35         # mountain hamlet
                else:
                    elev_s = 1.0
                terrain = 0.65 * slope_s + 0.35 * elev_s

                access, parts = self._access_score(lat, lon, pois)

                # Supply: how many listings sit within ~500 m (market depth).
                near = 0
                for plat, plon in listings:
                    dlat = (plat - lat) * M_PER_DEG_LAT
                    dlon = (plon - lon) * M_PER_DEG_LAT * math.cos(math.radians(lat))
                    if math.hypot(dlat, dlon) <= 500:
                        near += 1
                supply = min(1.0, near / 5.0)

                # Scenery: prefer mid elevations with relief nearby.
                if elev is None or elev <= -9999:
                    scenery = 0.3
                else:
                    scenery = _band_score(abs(elev - 80), 40, 350)

                total = (
                    terrain * self.weights["terrain"]
                    + access * self.weights["access"]
                    + supply * self.weights["supply"]
                    + scenery * self.weights["scenery"]
                ) / sum(self.weights.values())

                cells.append(
                    Cell(
                        lat=round(lat, 6), lon=round(lon, 6), elevation=elev,
                        slope=slope, terrain=round(terrain, 3),
                        access=round(access, 3), supply=round(supply, 3),
                        scenery=round(scenery, 3), total=int(round(total * 100)),
                        extras={k: (round(v) if v is not None else None) for k, v in parts.items()},
                    )
                )
        return cells


def cells_to_geojson(cells: list[Cell], meta: dict | None = None) -> dict:
    """One Point per cell, with its score and score breakdown as properties."""
    features = []
    for cell in cells:
        props = {
            "score": cell.total,
            "elevation_m": round(cell.elevation, 1) if cell.elevation and cell.elevation > -9000 else None,
            "slope_deg": round(cell.slope, 1) if cell.slope is not None else None,
            "terrain": cell.terrain,
            "access": cell.access,
            "supply": cell.supply,
            "scenery": cell.scenery,
        }
        props.update({f"dist_{k}_m": v for k, v in cell.extras.items()})
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [cell.lon, cell.lat]},
                "properties": props,
            }
        )
    return {
        "type": "FeatureCollection",
        "_meta": meta or {},
        "features": features,
    }


def cells_to_grid_geojson(
    cells: list[Cell], builder: GridBuilder, meta: dict | None = None
) -> dict:
    """One square Polygon per cell — ready for a filled, semi-transparent overlay."""
    half_lat = builder.dlat / 2
    half_lon = builder.dlon / 2
    features = []
    for cell in cells:
        lat, lon = cell.lat, cell.lon
        ring = [
            [lon - half_lon, lat - half_lat],
            [lon + half_lon, lat - half_lat],
            [lon + half_lon, lat + half_lat],
            [lon - half_lon, lat + half_lat],
            [lon - half_lon, lat - half_lat],
        ]
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Polygon", "coordinates": [ring]},
                "properties": {
                    "score": cell.total,
                    "elevation_m": round(cell.elevation, 1)
                    if cell.elevation and cell.elevation > -9000 else None,
                    "slope_deg": round(cell.slope, 1) if cell.slope is not None else None,
                    "terrain": cell.terrain,
                    "access": cell.access,
                    "supply": cell.supply,
                    "scenery": cell.scenery,
                },
            }
        )
    return {
        "type": "FeatureCollection",
        "_meta": meta or {},
        "features": features,
    }
