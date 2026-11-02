"""Render a self-contained interactive map page from GeoJSON.

The output is a single HTML file that talks to Leaflet and OpenStreetMap —
no build step, no server, drop it on any static host (or open it locally).
"""

from __future__ import annotations

import json
import html as htmllib
from typing import Any

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
  *, *::before, *::after {{ box-sizing: border-box; }}
  html, body {{ margin: 0; height: 100%; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }}
  #map {{ position: fixed; inset: 0; z-index: 0; }}

  .overlay {{ position: fixed; left: 1rem; top: 1rem; bottom: 1rem; width: 350px; z-index: 100;
             display: flex; flex-direction: column; gap: .5rem; pointer-events: none; }}
  .overlay > * {{ pointer-events: auto; }}
  .card {{ background: rgba(255,255,255,.96); backdrop-filter: blur(10px);
           border-radius: 12px; padding: .85rem 1rem; border: 1px solid rgba(255,255,255,.6);
           box-shadow: 0 4px 20px rgba(0,0,0,.13); }}
  .card.scrollable {{ flex: 1; min-height: 0; overflow-y: auto; display: flex;
                      flex-direction: column; gap: .5rem; }}
  h1 {{ font-size: 1.02rem; margin: 0 0 .2rem; color: #1a3a5c; }}
  .sub {{ font-size: .78rem; color: #6b7280; margin: 0 0 .55rem; }}
  .search {{ width: 100%; padding: .42rem .7rem; border: 1.5px solid #e2e8f0; border-radius: 8px;
             font-size: .84rem; }}
  .search:focus {{ outline: none; border-color: #1a3a5c; }}
  #filters {{ display: flex; gap: .3rem; flex-wrap: wrap; margin-top: .5rem; }}
  .filter {{ padding: .24rem .6rem; border-radius: 20px; border: 1.5px solid #e2e8f0;
             background: #fff; cursor: pointer; font-size: .74rem; font-weight: 500; color: #475569; }}
  .filter.active {{ background: #1a3a5c; color: #fff; border-color: #1a3a5c; }}
  .poi {{ background: #fff; border-radius: 10px; padding: .7rem .8rem; cursor: pointer;
          border: 2px solid transparent; transition: border-color .15s; }}
  .poi:hover, .poi.active {{ border-color: #1a3a5c; }}
  .poi h3 {{ font-size: .85rem; margin: 0 0 .12rem; color: #111827; }}
  .poi .where {{ font-size: .71rem; color: #9ca3af; margin-bottom: .3rem; }}
  .poi .note {{ font-size: .74rem; color: #4b5563; line-height: 1.45; margin-bottom: .35rem; }}
  .poi .links a {{ font-size: .72rem; color: #1a3a5c; font-weight: 500; text-decoration: none;
                   margin-right: .6rem; }}
  .badge {{ display: inline-block; font-size: .66rem; padding: .1rem .45rem; border-radius: 20px;
            font-weight: 600; margin-bottom: .25rem; }}
  .empty {{ color: #9ca3af; font-size: .82rem; padding: .5rem; }}
  @media (max-width: 780px) {{
    .overlay {{ left: 0; right: 0; bottom: 0; top: auto; width: 100%; flex-direction: column-reverse; }}
    .card {{ border-radius: 12px 12px 0 0; }}
    .card.scrollable {{ max-height: 40vh; }}
  }}
</style>
</head>
<body>
<div id="map"></div>
<div class="overlay">
  <div class="card">
    <h1>{title}</h1>
    <p class="sub">{subtitle}</p>
    <input class="search" id="search" type="text" placeholder="🔍 Search a place, city or note…">
    <div id="filters"></div>
  </div>
  <div class="card scrollable"><div id="list"></div></div>
</div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
const GEOJSON = {geojson};
const COLORS = {colors};
const ICONS  = {icons};

const map = L.map('map');
L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
  attribution: '&copy; OpenStreetMap contributors', maxZoom: 19,
}}).addTo(map);

const features = (GEOJSON.features || []).filter(f => f.geometry && f.geometry.coordinates);
let activeCategory = 'All', activeCard = null;
const markers = {{}};

function featureId(f, i) {{ return f.id || ('poi-' + i); }}

function icon(category) {{
  return L.divIcon({{
    className: '',
    html: `<div style="background:${{COLORS[category] || '#1a3a5c'}};width:32px;height:32px;border-radius:50%;
      display:flex;align-items:center;justify-content:center;font-size:16px;
      box-shadow:0 2px 8px rgba(0,0,0,.35);border:2.5px solid #fff;">${{ICONS[category] || '📍'}}</div>`,
    iconSize: [32, 32], iconAnchor: [16, 16], popupAnchor: [0, -18],
  }});
}}

function draw() {{
  features.forEach((f, i) => {{
    const [lng, lat] = f.geometry.coordinates;
    const p = f.properties;
    const m = L.marker([lat, lng], {{ icon: icon(p.category) }}).addTo(map);
    m.bindPopup(`<strong>${{escapeHtml(p.name || '')}}</strong><br>
      <small>${{escapeHtml(p.category || '')}}</small><br>
      ${{p.url ? `<a href="${{p.url}}" target="_blank" rel="noopener">Open link ↗</a>` : ''}}`);
    markers[featureId(f, i)] = m;
  }});
  if (features.length) {{
    map.fitBounds(features.map(f => [f.geometry.coordinates[1], f.geometry.coordinates[0]]),
                  {{ padding: [60, 60], maxZoom: 13 }});
  }} else {{
    map.setView([0, 0], 2);
  }}
}}

function escapeHtml(s) {{
  return String(s).replace(/[&<>"']/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
}}

function categories() {{
  const seen = [...new Set(features.map(f => f.properties.category || 'Other'))];
  seen.sort();
  return ['All', ...seen];
}}

function renderFilters() {{
  const box = document.getElementById('filters');
  box.innerHTML = '';
  categories().forEach(cat => {{
    const b = document.createElement('button');
    b.className = 'filter' + (cat === activeCategory ? ' active' : '');
    b.textContent = (ICONS[cat] ? ICONS[cat] + ' ' : '') + cat;
    b.onclick = () => {{ activeCategory = cat; renderFilters(); renderList(); }};
    box.appendChild(b);
  }});
}}

function renderList() {{
  const q = document.getElementById('search').value.toLowerCase().trim();
  const list = document.getElementById('list');
  const shown = features
    .map((f, i) => ({{ f, id: featureId(f, i) }}))
    .filter(({{ f }}) => {{
      const p = f.properties;
      const catOk = activeCategory === 'All' || (p.category || 'Other') === activeCategory;
      const hay = [p.name, p.notes, p.shared_by, p.domain].join(' ').toLowerCase();
      return catOk && (!q || hay.includes(q));
    }});

  list.innerHTML = shown.length ? '' : '<p class="empty">Nothing matches.</p>';
  shown.forEach(({{ f, id }}) => {{
    const p = f.properties;
    const [lng, lat] = f.geometry.coordinates;
    const card = document.createElement('div');
    card.className = 'poi';
    card.id = 'card-' + id;
    card.innerHTML = `
      <span class="badge" style="background:${{(COLORS[p.category]||'#1a3a5c')}}22;color:${{COLORS[p.category]||'#1a3a5c'}}">
        ${{ICONS[p.category] || '📍'}} ${{escapeHtml(p.category || 'Other')}}</span>
      <h3>${{escapeHtml(p.name || p.domain || 'Untitled')}}</h3>
      <div class="where">📍 ${{escapeHtml(p.domain || '')}}
        ${{p.shared_by ? '· shared by ' + escapeHtml(p.shared_by) : ''}}</div>
      ${{p.notes ? `<div class="note">${{escapeHtml(p.notes)}}</div>` : ''}}
      <div class="links">
        ${{p.url ? `<a href="${{p.url}}" target="_blank" rel="noopener">Open ↗</a>` : ''}}
        <a href="https://www.google.com/maps/search/?api=1&query=${{lat}},${{lng}}" target="_blank" rel="noopener">Google Maps ↗</a>
        <a href="geo:${{lat}},${{lng}}">OsmAnd</a>
      </div>`;
    card.onclick = () => {{
      map.flyTo([lat, lng], 14, {{ duration: .8 }});
      markers[id] && markers[id].openPopup();
      document.querySelectorAll('.poi.active').forEach(e => e.classList.remove('active'));
      card.classList.add('active');
    }};
    list.appendChild(card);
  }});
}}

document.getElementById('search').addEventListener('input', renderList);
draw();
renderFilters();
renderList();
</script>
</body>
</html>
"""


def render(
    geojson: dict[str, Any],
    *,
    title: str,
    subtitle: str = "",
    description: str = "",
    noindex: bool = False,
    colors: dict[str, str] | None = None,
    icons: dict[str, str] | None = None,
) -> str:
    """Render the map page as a standalone HTML string."""
    features = geojson.get("features", [])
    meta = geojson.get("_meta", {})
    subtitle = subtitle or (
        f"{len(features)} places · from a group chat"
        if features
        else "No geolocated places yet"
    )
    description = description or f"{title} — an interactive map generated from a group chat."
    return TEMPLATE.format(
        title=htmllib.escape(title),
        subtitle=htmllib.escape(subtitle),
        description=htmllib.escape(description),
        robots='<meta name="robots" content="noindex, nofollow">' if noindex else "",
        geojson=json.dumps(geojson, ensure_ascii=False),
        colors=json.dumps(colors or {}, ensure_ascii=False),
        icons=json.dumps(icons or {}, ensure_ascii=False),
        generated=htmllib.escape(str(meta.get("generated", ""))),
    )


def write(geojson: dict[str, Any], path: str, **kwargs: Any) -> str:
    content = render(geojson, **kwargs)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)
    return path
