# Publishing a chatmap

The output is two files — `locations.geojson` and `index.html` — with no build
step. Anything that serves files can serve them.

## Any static host

```bash
chatmap run ~/signal-export/Trip/chat.md -o site --hint Japan
```

Then upload the contents of `site/` to GitHub Pages, Netlify, Cloudflare Pages,
S3, a nginx directory, a Raspberry Pi… `index.html` loads Leaflet and map tiles
from CDNs, so the host only needs to serve static files.

For a subpath, keep the two files together — `index.html` carries the GeoJSON
inline, so there are no relative paths to break.

## Keeping a map private

```bash
chatmap run … -o site --noindex
```

This adds `<meta name="robots" content="noindex, nofollow">`, which asks
well-behaved crawlers not to index the page. It is **not** access control:

- the page is still readable by anyone who has the URL
- the URL still leaks via HTTP referrers, browser history, and shared links
- `locations.geojson` is inline, so it's protected only as well as the page is

If the chat contains home addresses or anything sensitive, put it behind
authentication (Cloudflare Access, HTTP basic auth, a tailnet-only host) rather
than relying on `noindex`.

## travel-guide / maps.girard-davila.net

[maps.girard-davila.net](https://maps.girard-davila.net) (repo
[`alx/travel-guide`](https://github.com/alx/travel-guide)) uses a superset of
the same GeoJSON shape, so `locations.geojson` drops straight in:

```
static/<slug>/locations.geojson     ← from chatmap, unchanged
content/<slug>/_index.md            ← front matter: title, description, emoji, section
layouts/<slug>/list.html            ← map layout
```

Front matter for a private map:

```yaml
---
title: "Japan 2028"
date: 2026-11-02T00:00:00Z
layout: list
description: "Places shared in the Japan 2028 group chat."
emoji: "🇯🇵"
section: "hidden"     # keeps it off the landing page
weight: 90
---
```

`section: "hidden"` removes the map from the home page's `curated` /
`community` lists. The layout is where you also add
`<meta name="robots" content="noindex, nofollow">` — see
`layouts/airbnb/single.html` for the pattern.

The travel-guide map layouts read these `properties` keys, all of which
`chatmap` already emits: `name`, `category`, `icon`, `notes`, plus extras
`url`, `domain`, `shared_by`, `shared_at`, `coord_accuracy` — those render as
the card's source line and link.

Icons and colours come from `geo.CATEGORIES`:

| Category | Icon | Used for |
|---|---|---|
| Stay | 🛏️ | hotels, airbnb, bookings, real estate |
| Food | 🍜 | restaurants, cafés, izakaya |
| Activity | 🎡 | tours, tickets, classes |
| Transport | 🚄 | trains, flights, passes |
| Sight | 🏯 | temples, castles, gardens |
| Practical | ℹ️ | visas, eSIM, weather |
| Other | 📍 | anything else |
