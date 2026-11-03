#!/usr/bin/env python3
"""Regenerate the Kyoto lodging-suitability grid.

    python scripts/kyoto_grid.py [--pois PATH] [--out PATH]

Inputs
  - Mokomoko listings for the Kyoto-Shiga bbox (live API call)
  - amenity POIs from a self-hosted Overpass instance at OVERPASS_URL
    (public endpoints are unusable from this host — see the org-7c29 beads)

Output
  examples/kyoto-suitability/grid.geojson — one square polygon per 10 km cell,
  each carrying `score` (0-100) plus its component scores.

Requires numpy/Pillow only for the DEM tiles (chatmap.grid).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from chatmap.grid import GridBuilder, cells_to_grid_geojson  # noqa: E402
from chatmap.mokomoko import fetch_bbox, usable_listings  # noqa: E402
from chatmap.overpass_aoi import AOI, OverpassClient  # noqa: E402

OVERPASS_URL = os.environ.get("OVERPASS_URL", "http://localhost:12345/api/interpreter")
CELL_M = 10_000


def load_pois(path: Path | None, use_live: bool) -> dict[str, list[tuple[float, float]]]:
    if path and path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        print(f"POIs from {path}", file=sys.stderr)
        return {k: [tuple(p) for p in v] for k, v in data.items()}
    if not use_live:
        raise SystemExit("no --pois file and --offline given")
    print(f"POIs from {OVERPASS_URL}", file=sys.stderr)
    client = OverpassClient(OVERPASS_URL)
    result = client.collect()
    print(f"  collected {sum(len(v) for v in result.features.values())} POIs "
          f"in {result.elapsed_s}s", file=sys.stderr)
    return result.features


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pois", type=Path, default=ROOT / "examples/kyoto-suitability/pois.json")
    ap.add_argument("--out", type=Path, default=ROOT / "examples/kyoto-suitability/grid.geojson")
    ap.add_argument("--offline", action="store_true", help="use the cached POI file only")
    ap.add_argument("--cell-m", type=float, default=CELL_M)
    args = ap.parse_args()

    pois = load_pois(args.pois, use_live=not args.offline)

    listings = usable_listings(
        fetch_bbox(AOI["sw_lat"], AOI["sw_lon"], AOI["ne_lat"], AOI["ne_lon"])
    )
    print(f"listings: {len(listings)}", file=sys.stderr)
    pois["listing"] = [(l.lat, l.lon) for l in listings]

    bbox = (AOI["sw_lat"], AOI["sw_lon"], AOI["ne_lat"], AOI["ne_lon"])
    builder = GridBuilder(bbox, cell_m=args.cell_m)
    print(f"grid: {builder.nrows} x {builder.ncols} @ {args.cell_m:.0f} m", file=sys.stderr)

    cells = builder.build(pois)
    geo = cells_to_grid_geojson(
        cells,
        builder,
        {
            "title": "Kyoto lodging suitability",
            "cell_m": args.cell_m,
            "aoi": AOI["name"],
            "listings": len(listings),
            "pois": sum(len(v) for k, v in pois.items() if k != "listing"),
            "weights": builder.weights,
        },
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(geo, ensure_ascii=False), encoding="utf-8")

    for cell in sorted(cells, key=lambda c: -c.total):
        print(
            f"  {cell.total:3}/100 {cell.lat:.3f},{cell.lon:.3f} "
            f"elev={cell.elevation or -1:6.1f} slope={cell.slope or 0:5.1f}",
            file=sys.stderr,
        )
    print(f"-> {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
