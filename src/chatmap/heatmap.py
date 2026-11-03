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
  #cat-legend {{ display: none; gap: .5rem; flex-wrap: wrap; margin-top: .5rem;
                 font-size: .7rem; color: #374151; }}
  #cat-legend .cat {{ display: inline-flex; align-items: center; gap: .25rem; }}
  #cat-legend i {{ width: 9px; height: 9px; border-radius: 50%; display: inline-block; }}
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
    <button id="btn-contour" title="Experimental: cell outlines above 90% (fragmented, see TODO)">&ge;90% (beta)</button>
  </div>
  <div id="cat-legend"></div>
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
/* ---- white contour joining every contiguous cell >= THRESHOLD ----
   Marching squares over the cell-score field. Each cell is a square sample;
   we walk the lattice and emit the boundary segments where the field crosses
   the threshold, then stitch segments into closed rings. */
const THRESHOLD = 90;

function buildContour() {{
  // Lattice indices must be recoverable, so sort cells onto a grid.
  const lats = [...new Set(cells.map(c => +c.lat.toFixed(6)))].sort((a, b) => a - b);
  const lons = [...new Set(cells.map(c => +c.lon.toFixed(6)))].sort((a, b) => a - b);
  const ny = lats.length, nx = lons.length;
  if (nx < 2 || ny < 2) return [];

  const latIdx = new Map(lats.map((v, i) => [v, i]));
  const lonIdx = new Map(lons.map((v, i) => [v, i]));
  const field = Array.from({{ length: ny }}, () => new Array(nx).fill(null));
  for (const c of cells) {{
    const iy = latIdx.get(+c.lat.toFixed(6));
    const ix = lonIdx.get(+c.lon.toFixed(6));
    if (iy != null && ix != null) field[iy][ix] = c.score;
  }}

  const cellLat = (lats[1] - lats[0]) / 2;
  const cellLon = (lons[1] - lons[0]) / 2;
  const above = (iy, ix) => iy >= 0 && iy < ny && ix >= 0 && ix < nx &&
                            field[iy][ix] != null && field[iy][ix] >= THRESHOLD;

  const rings = [];
  const seen = new Set();

  // For each cell above the threshold, trace its boundary and merge shared
  // edges so adjacent cells become one polygon rather than 31 little squares.
  const key = (lat, lon) => lat.toFixed(7) + '|' + lon.toFixed(7);
  const edges = new Map();   // undirected edge -> count of above-cells using it
  const addEdge = (a, b) => {{
    const k = [key(a[0], a[1]), key(b[0], b[1])].sort().join('>');
    edges.set(k, (edges.get(k) || 0) + 1);
  }};

  for (let iy = 0; iy < ny; iy++) {{
    for (let ix = 0; ix < nx; ix++) {{
      if (!above(iy, ix)) continue;
      const lat = lats[iy], lon = lons[ix];
      const n = [lat + cellLat, lon], s = [lat - cellLat, lon];
      const e = [lat, lon + cellLon], w = [lat, lon - cellLon];
      const nw = [lat + cellLat, lon - cellLon], ne = [lat + cellLat, lon + cellLon];
      const sw = [lat - cellLat, lon - cellLon], se = [lat - cellLat, lon + cellLon];
      // Skip an edge when the orthogonal neighbour is also above, AND when the
      // diagonal neighbour "fills" that corner — otherwise two cells touching
      // only at a corner leave four boundary edges meeting at one vertex
      // (degree 4), which makes the ring walk ambiguous and shatters the shape.
      const nAbove = above(iy + 1, ix), sAbove = above(iy - 1, ix);
      const eAbove = above(iy, ix + 1), wAbove = above(iy, ix - 1);
      if (!nAbove) {{ addEdge(nw, ne); }}
      if (!sAbove) {{ addEdge(sw, se); }}
      if (!eAbove) {{ addEdge(ne, se); }}
      if (!wAbove) {{ addEdge(nw, sw); }}
    }}
  }}

  // Boundary edges are those used by exactly one above-cell.
  const adj = new Map();
  for (const [k, count] of edges) {{
    if (count !== 1) continue;             // interior edge -> merged away
    const [a, b] = k.split('>');
    if (!adj.has(a)) adj.set(a, []);
    if (!adj.has(b)) adj.set(b, []);
    adj.get(a).push(b);
    adj.get(b).push(a);
  }}

  // Stitch boundary edges into closed rings. Each vertex on a cell boundary
  // has exactly 2 boundary neighbours, so walking from any unvisited vertex
  // and always picking the neighbour we did not arrive from traces the ring.
  const used = new Set();
  const usedEdge = (a, b) => [a, b].sort().join('>');
  for (const start of adj.keys()) {{
    if (used.has(start)) continue;
    const ring = [start];
    used.add(start);
    let prev = null, cur = start;
    for (;;) {{
      const cand = (adj.get(cur) || []).filter(v => !usedEdge(cur, v).length || !used.has(usedEdge(cur, v)));
      // Pick an unused edge first, falling back to the closing edge.
      let next = null;
      for (const v of adj.get(cur) || []) {{
        if (v === prev) continue;
        const ek = usedEdge(cur, v);
        if (!used.has(ek)) {{ next = v; used.add(ek); break; }}
      }}
      if (next === null) break;          // dead end (open contour at AOI edge)
      if (next === start) break;         // closed the ring
      ring.push(next);
      used.add(next);
      prev = cur; cur = next;
    }}
    if (ring.length >= 3) rings.push(ring);
  }}

  return rings.map(ring => ring.map(k => {{
    const [la, lo] = k.split('|').map(Number);
    return [la, lo];
  }}));
}}

const contourRings = buildContour();
const contourLayer = L.layerGroup();
contourRings.forEach(ring => {{
  L.polygon(ring, {{
    color: '#ffffff', weight: 3, opacity: 1, fill: false, dashArray: null,
  }}).addTo(contourLayer);
}});
// Not added by default: the ring stitching is known to fragment
// diagonal-adjacent cells (org-rm86.5). Opt-in via the toggle.
if (false && contourRings.length) contourLayer.addTo(map);

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
/* Spatial buckets: with a 1 km grid there are ~1300 cells, so a naive
   pixel x cell loop is ~170M distance computations. Index the cells into a
   coarse grid once, then each pixel only visits its own bucket + neighbours. */
const NB = 40;
const buckets = new Map();
const bkey = (bx, by) => bx + ',' + by;
for (const c of cells) {{
  const bx = Math.min(NB - 1, Math.max(0, Math.floor((c.lon - sw.lng) / lonSpan * NB)));
  const by = Math.min(NB - 1, Math.max(0, Math.floor((ne.lat - c.lat) / latSpan * NB)));
  const k = bkey(bx, by);
  if (!buckets.has(k)) buckets.set(k, []);
  buckets.get(k).push(c);
}}

function nearbyCells(bx, by, radius) {{
  const out = [];
  for (let dy = -radius; dy <= radius; dy++) {{
    for (let dx = -radius; dx <= radius; dx++) {{
      const v = buckets.get(bkey(bx + dx, by + dy));
      if (v) out.push(...v);
    }}
  }}
  return out;
}}

for (let py = 0; py < SIZE; py++) {{
  const lat = ne.lat - (py + 0.5) / SIZE * latSpan;
  for (let px = 0; px < SIZE; px++) {{
    const lon = sw.lng + (px + 0.5) / SIZE * lonSpan;
    const bx = Math.min(NB - 1, Math.max(0, Math.floor((lon - sw.lng) / lonSpan * NB)));
    const by = Math.min(NB - 1, Math.max(0, Math.floor((ne.lat - lat) / latSpan * NB)));

    // 3x3 neighbourhood, widened only where it would be empty (AOI edges).
    let pool = nearbyCells(bx, by, 1);
    if (!pool.length) pool = nearbyCells(bx, by, 3);
    if (!pool.length) pool = cells;

    let wsum = 0, vsum = 0, exact = null;
    for (const c of pool) {{
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

/* ---- lodging markers, coloured by an INFORMATIVE attribute ----
   Every Mokomoko listing classifies as "Stay", so colouring by category would
   paint 350 identical dots and produce a one-row legend. Price band is the
   dimension that actually varies and that matters when choosing where to look,
   so the pins use that. Categories are still carried for other sources. */
const CAT_COLOURS = {{
  Stay: '#805ad5', Food: '#dd6b20', Activity: '#38a169',
  Transport: '#3182ce', Sight: '#e53e3e', Other: '#718096',
}};

const pinLayer = L.layerGroup();
const presentCats = new Set();
const presentBands = new Set();

function priceBand(yen) {{
  if (yen == null) return null;
  if (yen < 7.4e6) return '< 7.4M JPY';
  if (yen < 12.8e6) return '7.4-12.8M';
  if (yen < 25.8e6) return '12.8-25.8M';
  return '> 25.8M JPY';
}}
const BAND_COLOURS = {{
  '< 7.4M JPY': '#1a9850',
  '7.4-12.8M': '#91cf60',
  '12.8-25.8M': '#fdae61',
  '> 25.8M JPY': '#d73027',
}};

const useCategory = new Set(LISTINGS.map(l => l.category || 'Stay')).size > 1;

LISTINGS.forEach(l => {{
  let colour, label;
  if (useCategory) {{
    const cat = l.category || 'Stay';
    presentCats.add(cat);
    colour = CAT_COLOURS[cat] || CAT_COLOURS.Other;
    label = cat;
  }} else {{
    const band = priceBand(l.price_yen);
    if (band) presentBands.add(band);
    colour = band ? BAND_COLOURS[band] : CAT_COLOURS.Other;
    label = band || '';
  }}
  L.circleMarker([l.lat, l.lon], {{
    radius: 3.5, color: '#ffffff', weight: 1,
    fillColor: colour, fillOpacity: 0.95,
  }}).bindTooltip(
    (l.name || '') + (label ? ` — ${{label}}` : ''),
    {{ direction: 'top' }}
  ).addTo(pinLayer);
}});
pinLayer.addTo(map);

/* Legend: categories when they vary, otherwise price bands. */
(function () {{
  const el = document.getElementById('cat-legend');
  let rows = [];
  if (useCategory) {{
    rows = [...presentCats].sort().map(c => [c, CAT_COLOURS[c] || CAT_COLOURS.Other]);
  }} else {{
    rows = [...presentBands].sort().map(b => [b, BAND_COLOURS[b]]);
  }}
  if (rows.length < 2) return;          // one colour only: a legend adds nothing
  el.innerHTML = rows.map(([name, colour]) =>
    `<span class="cat"><i style="background:${{colour}}"></i>${{name}}</span>`
  ).join('');
  el.style.display = 'flex';
}})();

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
const btnContour = document.getElementById('btn-contour');
if (btnContour) {{
  btnContour.onclick = () => {{
    const on = btnContour.classList.toggle('on');
    if (on) {{ map.addLayer(contourLayer); }} else {{ map.removeLayer(contourLayer); }}
  }};
}}
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
