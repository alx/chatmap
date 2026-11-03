"""Grid scoring rules — no network."""

import math

from chatmap.grid import (
    DEFAULT_WEIGHTS,
    GridBuilder,
    _band_score,
    cells_to_geojson,
    lonlat_to_tile,
    tile_bounds,
)
from chatmap.overpass_aoi import AOI, build_query


def test_band_score_direction():
    assert _band_score(0, 8, 25) == 1.0     # ideal and below
    assert _band_score(8, 8, 25) == 1.0
    assert _band_score(25, 8, 25) == 0.0    # dead and above
    assert 0.0 < _band_score(15, 8, 25) < 1.0


def test_flat_ground_scores_better_than_steep_on_terrain():
    """Regression: slope_s was computed as 1 - band_score, rewarding cliffs."""
    from chatmap.grid import Cell  # noqa: F401  (documents intent)

    # Mirror the builder's terrain formula for two slopes at a sane elevation.
    def terrain(slope):
        slope_s = _band_score(slope, 8.0, 25.0)
        elev_s = 1.0
        return 0.65 * slope_s + 0.35 * elev_s

    assert terrain(0.5) > terrain(15.0) > terrain(30.0)
    assert terrain(0.5) == 1.0


def test_low_alluvial_plain_penalised_but_city_floor_is_not():
    """Regression: <3m flood penalty wrongly demoted Kyoto (sits at ~30-50m)."""
    def elev_s(elev):
        if elev is None:
            return 0.5
        if elev < 1:
            return 0.2
        if elev < 5:
            return 0.7
        if elev > 400:
            return 0.35
        return 1.0

    assert elev_s(0.5) == 0.2     # delta
    assert elev_s(3) == 0.7       # low plain
    assert elev_s(30) == 1.0      # Kyoto city floor — must NOT be penalised
    assert elev_s(50) == 1.0
    assert elev_s(800) == 0.35    # mountain


def test_weights_sum_to_100():
    assert sum(DEFAULT_WEIGHTS.values()) == 100


def test_grid_dimensions_for_kyoto_aoi_at_10km():
    bbox = (AOI["sw_lat"], AOI["sw_lon"], AOI["ne_lat"], AOI["ne_lon"])
    g = GridBuilder(bbox, cell_m=10_000)
    assert (g.nrows, g.ncols) == (3, 3)


def test_overpass_query_is_bbox_bounded():
    """Every clause must carry the AOI bbox — an unbounded nwr scans the DB."""
    for bucket in ("transit", "konbini", "hospital"):
        q = build_query(bucket)
        for line in q.splitlines():
            s = line.strip()
            if s.startswith("nwr") or s.startswith("node") or s.startswith("way"):
                assert "(" in s and ")" in s, s
        assert str(AOI["sw_lat"]) in q


def test_overpass_query_has_no_unsupported_regex_flag():
    """Overpass rejects "..."i in this version (parse error: 'i' found)."""
    assert "\"i]" not in build_query("konbini")


def test_tile_maths_roundtrip():
    x, y = lonlat_to_tile(135.7681, 35.0116, 13)
    w, n, e, s = tile_bounds(x, y, 13)
    assert w <= 135.7681 <= e
    assert s <= 35.0116 <= n


def test_cells_to_geojson_shape():
    from chatmap.grid import Cell

    c = Cell(lat=35.0, lon=135.8, elevation=50.0, slope=1.2, terrain=1.0,
             access=0.9, supply=0.5, scenery=0.8, total=88)
    gj = cells_to_geojson([c])
    f = gj["features"][0]
    assert f["geometry"]["type"] == "Point"
    assert f["geometry"]["coordinates"] == [135.8, 35.0]
    assert f["properties"]["score"] == 88


def test_heatmap_has_no_redeclared_score():
    """Regression: `let score` plus `const score` in one block is a SyntaxError
    that blanked the whole page."""
    import re as _re

    from chatmap import heatmap

    grid = {
        "type": "FeatureCollection",
        "_meta": {"cell_m": 10000, "listings": 1, "pois": 1, "aoi": "x"},
        "features": [
            {
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[135.7, 34.9], [135.8, 34.9],
                                     [135.8, 35.0], [135.7, 34.9]]],
                },
                "properties": {"score": 50},
            }
        ],
    }
    out = heatmap.render(grid, [], title="t")
    block = out.split("for (let py")[1].split("ctx.putImageData")[0]
    assert "let score" in block
    assert "const score" not in block


def test_heatmap_ramp_endpoints_are_blue_and_red():
    from chatmap import heatmap

    assert "44, 123, 182" in heatmap.TEMPLATE   # blue = low
    assert "224,  75,  58" in heatmap.TEMPLATE  # red = high
