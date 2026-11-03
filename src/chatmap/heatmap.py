"""Render a suitability grid as a smooth heat layer.

Nine cells with flat fills reads as a checkerboard, not a heat map. So the cell
scores are resampled onto a fine raster with inverse-distance weighting and
painted through a blue→green→yellow→red ramp, with the cell values blended so
neighbouring areas fade into each other.

The page is self-contained (Leaflet + OSM tiles from CDN), so it can be opened
from disk or served anywhere.
"""

from __future__ import annotations

import json
import math
import html as htmllib

TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<meta name="description" content="{description}">
{robots}
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<style>
  html, body {{ margin: 0; height: 100%; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }}
  #map {{ position: fixed; inset: 0; }}
  .panel {{ position: fixed; left: 1rem; top: 1rem; z-index: 500; width: 300px;
            background: rgba(255,255,255,.96); backdrop-filter: blur(10px);
            border-radius: 12px; padding: .85rem 1rem; box-shadow: 0 4px 20px rgba(0,0,0,.15);
            border: 1px solid rgba(255,255,255,.6); }}
  .panel h1 {{ font-size: 1rem; margin: 0 0 .2rem; color: #1a3a5c; }}
  .panel p {{ font-size: .76rem; color: #6b7280; margin: 0 0 .6rem; }}
  .legend {{ display: flex; align-items: center; gap: .4rem; font-size: .7rem; color: #374151; }}
  .ramp {{ flex: 1; height: 12px; border-radius: 6px;
           background: linear-gradient(90deg, #2c7bb6 0%, #4fc3a1 33%, #f6d55c 66%, #e04b3a 100%); }}
  .ctl {{ display: flex; gap: .4rem; margin-top: .55rem; flex-wrap: wrap; }}
  button {{ padding: .24rem .6rem; border-radius: 20px; border: 1.5px solid #e2e8f0;
            background: #fff; cursor: pointer; font-size: .72rem; color: #475569; }}
  button.on {{ background: #1a3a5c; color: #fff; border-color: #1a3a5c; }}
  .note {{ font-size: .68rem; color: #9ca3af; margin-top: .5rem; line-height: 1.4; }}
</style>
</head>
<body>
<div id="map"></div>
<div class="panel">
  <h1>{title}</h1>
  <p>{subtitle}</p>
  <div class="legend"><span>low</span><span class="ramp"></span><span>high</span></div>
  <div class="ctl">
    <button id="btn-heat" class="on">Heat</button>
    <button id="btn-cells">Cell values</button>
    <button id="btn-pins" class="on">Listings</button>
  </div>
  <div class="note">Blue = poor for lodging, red = best. Interpolated from
    {ncells} scored {cellm}-m cells (IDW, p=2).</div>
</div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
const GRID = {grid};
const LISTINGS = {listings};
const META = {meta};

const map = L.map('map', {{ zoomControl: true }});
const baseLayer = L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
  attribution: '&copy; OpenStreetMap contributors', maxZoom: 19,
}}).addTo(map);
const basePane = map.getPane('tilePane');

/* ---- blue → green → yellow → red ramp ---- */
const STOPS = [
  [0.00, [ 44, 123, 182]],
  [0.33, [ 79, 195, 161]],
  [0.66, [246, 213,  92]],
  [1.00, [224,  75,  58]],
];
function ramp(t) {{
  t = Math.max(0, Math.min(1, t));
  for (let i = 1; i < STOPS.length; i++) {{
    if (t <= STOPS[i][0]) {{
      const [t0, c0] = STOPS[i - 1], [t1, c1] = STOPS[i];
      const f = (t - t0) / (t1 - t0);
      const c = c0.map((v, k) => Math.round(v + (c1[k] - v) * f));
      return `rgb(${{c[0]}},${{c[1]}},${{c[2]}})`;
    }}
  }}
  return 'rgb(224,75,58)';
}}

/* ---- cells: centroid + score from the GeoJSON polygons ---- */
const cells = GRID.features.map(f => {{
  const ring = f.geometry.coordinates[0];
  let x = 0, y = 0;
  for (let i = 0; i < ring.length - 1; i++) {{ x += ring[i][0]; y += ring[i][1]; }}
  const n = ring.length - 1;
  return {{ lon: x / n, lat: y / n, score: f.properties.score, props: f.properties, ring }};
}});

const scores = cells.map(c => c.score);
const SCORE_MIN = Math.min(...scores);
const SCORE_MAX = Math.max(...scores);
const bounds = L.latLngBounds(cells.map(c => [c.lat, c.lon])).pad(0.25);
map.fitBounds(bounds);

/* ---- inverse-distance weighting onto a canvas raster ---- */
const SIZE = 360;
const sw = bounds.getSouthWest(), ne = bounds.getNorthEast();
const heatLayer = L.layerGroup();

const canvas = document.createElement('canvas');
canvas.width = SIZE; canvas.height = SIZE;
const ctx = canvas.getContext('2d');
const img = ctx.createImageData(SIZE, SIZE);

const latSpan = ne.lat - sw.lat, lonSpan = ne.lng - sw.lng;
for (let py = 0; py < SIZE; py++) {{
  const lat = ne.lat - (py + 0.5) / SIZE * latSpan;
  for (let px = 0; px < SIZE; px++) {{
    const lon = sw.lng + (px + 0.5) / SIZE * lonSpan;
    let wsum = 0, vsum = 0, exact = null;
    for (const c of cells) {{
      const dlat = (lat - c.lat) * 111.32;
      const dlon = (lon - c.lon) * 111.32 * Math.cos(lat * Math.PI / 180);
      const d2 = dlat * dlat + dlon * dlon;
      if (d2 < 0.01) {{ exact = c.score; break; }}
      const w = 1 / (d2 * d2);        // p = 4: keeps hotspots local
      wsum += w; vsum += w * c.score;
    }}
    let score = exact !== null ? exact : (wsum ? vsum / wsum : 0);
    score = (score - SCORE_MIN) / (SCORE_MAX - SCORE_MIN) * 100;
    const rgb = ramp(score / 100).match(/\\d+/g).map(Number);
    const o = (py * SIZE + px) * 4;
    img.data[o] = rgb[0]; img.data[o + 1] = rgb[1]; img.data[o + 2] = rgb[2];
    img.data[o + 3] = 150;           // semi-transparent overlay
  }}
}}
ctx.putImageData(img, 0, 0);

const overlay = L.imageOverlay(canvas.toDataURL(), bounds, {{ opacity: 0.85 }});
heatLayer.addLayer(overlay);
heatLayer.addTo(map);

/* ---- flat cell polygons, for reading the actual numbers ---- */
const cellLayer = L.layerGroup();
cells.forEach(c => {{
  const poly = L.polygon(c.ring.map(([lon, lat]) => [lat, lon]), {{
    color: '#ffffff', weight: 1, fillColor: ramp(c.score / 100), fillOpacity: 0.75,
  }});
  poly.bindTooltip(`<b>${{c.score}}/100</b><br>elev ${{c.props.elevation_m ?? '?'}} m` +
                   `<br>slope ${{c.props.slope_deg ?? '?'}}&deg;`, {{ sticky: true }});
  cellLayer.addLayer(poly);
}});

/* ---- lodging markers on top ---- */
const pinLayer = L.layerGroup();
LISTINGS.forEach(l => {{
  L.circleMarker([l.lat, l.lon], {{
    radius: 3, color: '#1a3a5c', weight: 1, fillColor: '#fff', fillOpacity: 0.9,
  }}).bindTooltip(l.name || '', {{ direction: 'top' }}).addTo(pinLayer);
}});
pinLayer.addTo(map);

/* ---- controls ---- */
const btnHeat = document.getElementById('btn-heat');
const btnCells = document.getElementById('btn-cells');
const btnPins = document.getElementById('btn-pins');
btnHeat.onclick = () => {{
  const on = btnHeat.classList.toggle('on');
  if (on) {{ map.addLayer(heatLayer); basePane.style.filter = 'grayscale(.75) brightness(1.12)'; }}
  else {{ map.removeLayer(heatLayer); basePane.style.filter = ''; }}
}};
btnCells.onclick = () => {{
  const on = btnCells.classList.toggle('on');
  on ? map.addLayer(cellLayer) : map.removeLayer(cellLayer);
}};
btnPins.onclick = () => {{
  const on = btnPins.classList.toggle('on');
  on ? map.addLayer(pinLayer) : map.removeLayer(pinLayer);
}};
</script>
</body>
</html>
"""


def render(
    grid: dict,
    listings: list[dict] | None = None,
    *,
    title: str = "Kyoto lodging suitability",
    subtitle: str = "",
    description: str = "",
    noindex: bool = False,
) -> str:
    meta = grid.get("_meta", {})
    ncells = len([f for f in grid.get("features", []) if f.get("geometry")])
    subtitle = subtitle or (
        f"{meta.get('listings', '?')} listings · {meta.get('pois', '?')} amenity POIs · "
        f"{meta.get('aoi', 'area')}"
    )
    return TEMPLATE.format(
        title=htmllib.escape(title),
        subtitle=htmllib.escape(subtitle),
        description=htmllib.escape(description or title),
        robots='<meta name="robots" content="noindex, nofollow">' if noindex else "",
        grid=json.dumps(grid, ensure_ascii=False),
        listings=json.dumps(listings or [], ensure_ascii=False),
        meta=json.dumps(meta, ensure_ascii=False),
        ncells=ncells,
        cellm=int(meta.get("cell_m", 0) / 1000) or 10,
    )


def write(grid: dict, path: str, listings: list[dict] | None = None, **kw) -> str:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(render(grid, listings, **kw))
    return path
