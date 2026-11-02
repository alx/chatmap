"""Turn raw links into map places.

The pipeline is:  link  ->  (source-specific extractor)  ->  candidate place
                  candidate place -> (geocoder) -> lat/lng

Every step degrades gracefully: if no coordinates can be resolved the place is
still emitted with ``coord_accuracy: "none"`` so it appears in the sidebar and
can be fixed by hand. A missing pin is better than a silently dropped link.
"""

from __future__ import annotations

import html
import json
import re
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field, asdict
from typing import Iterable

USER_AGENT = "chatmap/0.1 (+https://github.com/alx/chatmap)"

# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------

CATEGORIES: dict[str, dict] = {
    "Stay":     {"icon": "🛏️", "color": "#805ad5"},
    "Food":     {"icon": "🍜", "color": "#dd6b20"},
    "Activity": {"icon": "🎡", "color": "#38a169"},
    "Transport": {"icon": "🚄", "color": "#3182ce"},
    "Sight":    {"icon": "🏯", "color": "#e53e3e"},
    "Practical": {"icon": "ℹ️", "color": "#718096"},
    "Other":    {"icon": "📍", "color": "#1a3a5c"},
}

_DOMAIN_CATEGORY: list[tuple[re.Pattern, str]] = [
    (re.compile(r"(airbnb|booking\.com|agoda|hostelworld|trip\.com/hotels|expedia|mokomoko|"
                r"athome\.jp|suumo|jalan\.net|rakuten-travel|tabelog\.com/.*/hotel|vrbo|"
                r"homeexchange|akiya|akiyaair|yabuakiyabank|空き家|"
                r"trip\.com/(?:w|moments)|wikipedia\.org/wiki/(?:Akiya|Ryokan))", re.I), "Stay"),
    (re.compile(r"(tabelog|gurunavi|hotpepper|retty|yelp|thefork|opentable|/restaurant)", re.I), "Food"),
    (re.compile(r"(klook|getyourguide|viator|airbnb\.com/experiences|activity)", re.I), "Activity"),
    (re.compile(r"(maps\.app\.goo\.gl|google\.[a-z.]+/maps|openstreetmap\.org|osm\.org)", re.I), "Other"),
    (re.compile(r"(hyperdia|japan-guide\.com/access|jreast|jr-odekake|willerexpress|highwaybus|"
                r"skyscanner|google\.[a-z.]+/travel|rome2rio|navitime|yahoo\.co\.jp/transit)", re.I), "Transport"),
    (re.compile(r"(japan-guide\.com|jnto|japan\.travel|lonelyplanet|wikipedia\.org/wiki|"
                r"tripadvisor\.[a-z.]+/Attraction)", re.I), "Sight"),
    (re.compile(r"(\.go\.jp|\.gob\.jp|embassy|visa|japanesepod|weather)", re.I), "Practical"),
]

# Place names people actually write after a city, used to detect where a listing is.
_KNOWN_PLACES = [
    # Japan — regions/prefectures
    "Hokkaido", "Aomori", "Iwate", "Miyagi", "Akita", "Yamagata", "Fukushima", "Ibaraki",
    "Tochigi", "Gunma", "Saitama", "Chiba", "Tokyo", "Kanagawa", "Niigata", "Toyama",
    "Ishikawa", "Fukui", "Yamanashi", "Nagano", "Gifu", "Shizuoka", "Aichi", "Mie",
    "Shiga", "Kyoto", "Osaka", "Hyogo", "Nara", "Wakayama", "Tottori", "Shimane",
    "Okayama", "Hiroshima", "Yamaguchi", "Tokushima", "Kagawa", "Ehime", "Kochi",
    "Fukuoka", "Saga", "Nagasaki", "Kumamoto", "Oita", "Beppu", "Miyazaki", "Kagoshima", "Okinawa",
    # Japan — cities people name directly
    "Kanazawa", "Nagoya", "Sapporo", "Hakone", "Nikko", "Takayama", "Matsumoto", "Kobe",
    "Yokohama", "Kamakura", "Naha", "Sendai", "Kumamoto", "Kurashiki", "Takamatsu",
    # common travel countries, so the trick also works outside Japan
    "Bali", "Malaysia", "Thailand", "Vietnam", "Korea", "Taiwan", "Portugal", "Italy",
]
_PLACE_RE = re.compile(r"\b(" + "|".join(map(re.escape, _KNOWN_PLACES)) + r")\b", re.I)

# "Appartement · Kuala Lumpur · ★4,84 · 1 chambre" — listing titles put the area
# in the second bullet-delimited field. Short ones are cities worth geocoding.
_TITLE_AREA = re.compile(r"[·|]\s*([^·|★]{3,40}?)\s*(?:[·|★]|$)")


def area_from_title(title: str) -> str | None:
    """Pull a city/neighbourhood out of a listing title."""
    if not title:
        return None
    for match in _TITLE_AREA.finditer(title):
        candidate = match.group(1).strip(" ,.-·|")
        if not candidate or len(candidate) < 3:
            continue
        if re.search(r"\d", candidate):            # ratings, prices, room counts
            continue
        if re.search(r"(€|\$|¥|£|EUR|USD)", candidate, re.I):
            continue
        if re.fullmatch(r"(studio|chambre|chambres|bedroom|bedrooms|lit|lits|bed|beds|"
                        r"appartement|appart'hôtel|maison|house|villa)", candidate, re.I):
            continue
        # a plain capitalised place name, optionally with a spelling variant after a comma
        if re.fullmatch(r"[\w'’\-]+(?:[\s.\-]+[\w'’\-]+){0,4}", candidate, re.I):
            return candidate
    return None

_TITLE_JUNK = re.compile(
    r"\s*[|·—–-]\s*(Airbnb|Booking\.com|Agoda|Trip\.com|Tabelog|食べログ|Tripadvisor|"
    r"Klook|Google Maps|YouTube|楽天|Jalan|じゃらん|Expedia)\s*$", re.I
)

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class Place:
    name: str
    url: str
    category: str = "Other"
    lat: float | None = None
    lng: float | None = None
    notes: str = ""
    author: str = ""
    timestamp: str = ""
    domain: str = ""
    coord_source: str = ""
    coord_accuracy: str = "none"

    @property
    def feature_id(self) -> str:
        return f"poi-{abs(hash(self.url)) % (10 ** 8):08d}"

    def to_feature(self) -> dict:
        props = {
            "id": self.feature_id,
            "name": self.name,
            "category": self.category,
            "icon": CATEGORIES.get(self.category, CATEGORIES["Other"])["icon"],
            "url": self.url,
            "domain": self.domain,
            "notes": self.notes,
            "shared_by": self.author,
            "shared_at": self.timestamp,
            "coord_source": self.coord_source,
            "coord_accuracy": self.coord_accuracy,
        }
        geometry = (
            {"type": "Point", "coordinates": [self.lng, self.lat]}
            if self.lat is not None and self.lng is not None
            else None
        )
        return {"type": "Feature", "id": self.feature_id, "geometry": geometry, "properties": props}

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# HTTP helper (stdlib only, cached, polite)
# ---------------------------------------------------------------------------

_cache: dict[str, str | None] = {}
_last_call = 0.0


def fetch_text(url: str, timeout: float = 12.0) -> str | None:
    """GET a URL and return text, or None on failure. Results are cached."""
    global _last_call
    if url in _cache:
        return _cache[url]
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            charset = resp.headers.get_content_charset() or "utf-8"
            text = resp.read(400_000).decode(charset, errors="replace")
    except Exception:  # noqa: BLE001 - network is best effort
        text = None
    _cache[url] = text
    # be a good citizen
    delta = time.time() - _last_call
    if delta < 1.0:
        time.sleep(1.0 - delta)
    _last_call = time.time()
    return text


def _meta(html_text: str, *names: str) -> str | None:
    for name in names:
        for pattern in (
            rf'<meta[^>]+property=["\']{re.escape(name)}["\'][^>]+content=["\']([^"\']+)',
            rf'<meta[^>]+name=["\']{re.escape(name)}["\'][^>]+content=["\']([^"\']+)',
            rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']{re.escape(name)}',
        ):
            m = re.search(pattern, html_text, re.I)
            if m:
                return html.unescape(m.group(1)).strip()
    return None


def page_text(url: str) -> str:
    """Visible-ish text of a page, used to guess *where* a listing is."""
    text = fetch_text(url)
    if not text:
        return ""
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html.unescape(text))[:20_000]


def guess_place_name(url: str, title: str = "") -> str | None:
    """Guess which city/prefecture a page is about (JS app listings have no coords).

    The title is trusted. Body text is only a fallback, and only its head: a
    page about Hong Kong ferries routinely name-drops a dozen other countries
    in nav, footers and route lists, so scanning all of it produces confident
    nonsense. Prefer the *first* place mentioned near the start instead.
    """
    if title:
        m = _PLACE_RE.search(title)
        if m:
            return m.group(1)

    body = page_text(url)
    if not body:
        return None
    # Only the opening slice: that is where "About this place" text lives.
    # Require the place name to appear at least 3 characters in, so a page
    # whose first word is "Vietnam" in a menu doesn't win by default.
    head = body[:1500]
    m = _PLACE_RE.search(head)
    if m and m.start() > 2:
        return m.group(1)
    return None


# Generic page titles that carry no place information — never use them as a query.
_GENERIC_TITLES = re.compile(
    r"^(home|homepage|welcome|japan rail pass|jr pass|route search|vols pas chers|cheap flights|"
    r"skyscanner|hyperdia|google maps|airbnb|booking\.com|tabelog|食べログ|hostelworld|klook|"
    r"trip\.com|agoda|just a moment.*|access denied|forbidden|error.*)$",
    re.I,
)

# Directory hosts that hide the real name from scrapers; the URL path is all we get.
_OPAQUE_HOSTS = re.compile(
    r"(tabelog\.com|gurunavi\.com|hotpepper\.jp|retty\.me|airbnb\.[a-z.]+|hostelworld\.com|"
    r"booking\.com|agoda\.com|klook\.com)",
    re.I,
)

# Japanese region/city slugs that appear in directory URL paths.
_CITY_SLUGS: dict[str, str] = {
    "tokyo": "Tokyo", "kyoto": "Kyoto", "osaka": "Osaka", "hokkaido": "Hokkaido",
    "ishikawa": "Kanazawa", "aichi": "Nagoya", "fukuoka": "Fukuoka", "hyogo": "Kobe",
    "kanagawa": "Yokohama", "nara": "Nara", "oita": "Beppu", "okinawa": "Naha",
    "hiroshima": "Hiroshima", "nagano": "Nagano", "gifu": "Takayama", "shizuoka": "Shizuoka",
}


def is_generic_title(title: str) -> bool:
    """True when a page title says nothing about *what or where* the page is."""
    title = (title or "").strip()
    if not title or len(title) < 3:
        return True
    if _GENERIC_TITLES.match(title):
        return True
    # "Airbnb | Vacation rentals..." style titles that merely repeat the brand
    first = re.split(r"[|·—–-]", title)[0].strip()
    return bool(_GENERIC_TITLES.match(first))


def city_from_url(url: str) -> str | None:
    """Read a city/region out of a directory URL path (tabelog, booking, klook…)."""
    if not _OPAQUE_HOSTS.search(url):
        return None
    path = urllib.parse.urlparse(url).path.lower()
    for slug, city in _CITY_SLUGS.items():
        if re.search(rf"(^|/){slug}(/|$)", path):
            return city
    return None


def page_title(url: str) -> str:
    """Best-effort human title for a URL: og:title, <title>, else the path."""
    parsed = urllib.parse.urlparse(url)
    text = fetch_text(url)
    if text:
        title = _meta(text, "og:title", "twitter:title")
        if not title:
            m = re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
            if m:
                title = html.unescape(re.sub(r"\s+", " ", m.group(1))).strip()
        if title:
            title = _TITLE_JUNK.sub("", title).strip()
            if 2 < len(title) < 140 and not is_generic_title(title):
                return title

    return _name_from_path(parsed)


def _name_from_path(parsed: urllib.parse.ParseResult) -> str:
    """Readable name from the URL path: /place/KL+Forest+Eco+Park/@3.1,101.7 → 'KL Forest Eco Park'."""
    segments = [urllib.parse.unquote(s) for s in parsed.path.strip("/").split("/") if s]

    # Google Maps says it outright: /maps/place/<Name>/data=!... — take <Name>.
    if "place" in segments:
        idx = segments.index("place")
        if idx + 1 < len(segments):
            candidate = _clean_slug(segments[idx + 1])
            if candidate:
                return candidate

    # Otherwise walk backwards for the first segment that reads like a name.
    for slug in reversed(segments):
        candidate = _clean_slug(slug)
        if candidate:
            return candidate
    return parsed.netloc.replace("www.", "")


def _clean_slug(slug: str) -> str | None:
    """Turn one path segment into a display name, or None if it carries none."""
    # Map URLs embed their payload as path segments: @lat,lng,15z and data=!4m6!3m5!...
    if slug.startswith(("@", "data=", "!")):
        return None
    if re.fullmatch(r"-?\d+(\.\d+)?,[-\d.,zhmd]+t?", slug):
        return None
    # Opaque tokens (percent-encoded blobs, share ids) read as one long unbroken run.
    if re.fullmatch(r"[A-Za-z0-9_]+", slug) and len(slug) > 18:
        return None
    slug = re.sub(r"\.(html?|php|aspx?)$", "", slug, flags=re.I)
    slug = re.sub(r"[-_+.]+", " ", slug)
    slug = re.sub(r"\b\d{3,}\b", " ", slug)          # listing ids
    slug = re.sub(r"\s+", " ", slug).strip()
    if len(slug) < 4 or not re.search(r"[a-z\u00c0-\uffff]", slug, re.I):
        return None
    if re.fullmatch(r"(rooms?|property|hotel|activity|detail|search|index|e|maps?|place)", slug, re.I):
        return None
    # Tabelog/booking style area codes: A170101, 26000123
    if re.fullmatch(r"[A-Z]?\d{3,}", slug.replace(" ", "").strip()):
        return None
    return slug[:100].title()


# ---------------------------------------------------------------------------
# Coordinates from URLs (no network needed)
# ---------------------------------------------------------------------------

# @35.0116,135.7681,15z  and  /@35.0116,135.7681
# The tail after the two numbers is not always "15z": Google also emits
# "@5.372,100.328,1895a,35y,337.96h,44.44t" (tilt/heading). Only the pair matters.
_AT_COORDS = re.compile(r"@(-?\d{1,2}\.\d{3,}),\s*(-?\d{1,3}\.\d{3,})(?=[,\s]|$)")
# !3d35.0116!4d135.7681
_DMS_COORDS = re.compile(r"!3d(-?\d{1,2}\.\d{3,})!4d(-?\d{1,3}\.\d{3,})")
# ?q=35.0116,135.7681  / ?ll=35.0116,135.7681  / ?center=35.0116,135.7681
_QUERY_COORDS = re.compile(r"[?&](?:q|ll|center|query|mlat|sll)=(-?\d{1,2}\.\d{3,}),\s*(-?\d{1,3}\.\d{3,})")
# OpenStreetMap: #map=15/35.0116/135.7681
_OSM_COORDS = re.compile(r"#map=\d+(?:\.\d+)?/(-?\d{1,2}\.\d+)/(-?\d{1,3}\.\d+)")


def coords_from_url(url: str) -> tuple[float, float, str] | None:
    """Extract lat/lng embedded in a map URL. Returns (lat, lng, source)."""
    if m := _DMS_COORDS.search(url):
        return float(m.group(1)), float(m.group(2)), "url_data_pin"
    if m := _AT_COORDS.search(url):
        return float(m.group(1)), float(m.group(2)), "url_at"
    if m := _OSM_COORDS.search(url):
        return float(m.group(1)), float(m.group(2)), "url_osm"
    if m := _QUERY_COORDS.search(url):
        return float(m.group(1)), float(m.group(2)), "url_query"
    return None


def is_short_map_link(url: str) -> bool:
    return bool(re.search(r"(maps\.app\.goo\.gl|goo\.gl/maps|maps\.google|g\.co/kgs)", url, re.I))


def expand_short_link(url: str) -> str | None:
    """Follow a shortener redirect (maps.app.goo.gl, g.co/kgs) to the real URL."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
            final = resp.geturl()
            body = resp.read(200_000).decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return None
    if coords_from_url(final):
        return final
    # goo.gl map links put the pin in the page payload
    m = re.search(r'(?:35|3[0-9])\.\d{4,}[",\s]+1(?:3[0-9])\.\d{4,}', body)
    if m:
        return final + "#" + m.group(0)
    return final


# ---------------------------------------------------------------------------
# Geocoders
# ---------------------------------------------------------------------------


def nominatim(query: str, hint: str = "") -> tuple[float, float] | None:
    """Geocode free text with Nominatim (OSM). Returns (lat, lng)."""
    q = f"{query}, {hint}" if hint else query
    url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(
        {"q": q, "format": "json", "limit": 1}
    )
    text = fetch_text(url)
    if not text:
        return None
    try:
        results = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not results:
        return None
    return float(results[0]["lat"]), float(results[0]["lon"])


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


def categorise(url: str, title: str = "") -> str:
    for pattern, category in _DOMAIN_CATEGORY:
        if pattern.search(url):
            return category
    lowered = title.lower()
    if re.search(r"\b(hotel|ryokan|hostel|apartment|apartm|guesthouse|airbnb|stay|auberge|machiya)\b", lowered):
        return "Stay"
    if re.search(r"\b(restaurant|ramen|izakaya|sushi|cafe|coffee|kaiseki|soba|udon|bar)\b", lowered):
        return "Food"
    if re.search(r"\b(temple|shrine|castle|museum|park|garden|onsen|viewpoint)\b", lowered):
        return "Sight"
    if re.search(r"\b(tour|ticket|experience|class|workshop|activity)\b", lowered):
        return "Activity"
    if re.search(r"\b(train|rail|pass|flight|bus|ferry|rental|car)\b", lowered):
        return "Transport"
    return "Other"


def build_place(url: str, *, author: str = "", timestamp: str = "", notes: str = "",
                hint: str = "", online: bool = True) -> Place:
    """Turn one shared link into a :class:`Place`."""
    domain = urllib.parse.urlparse(url).netloc.replace("www.", "")
    place = Place(name="", url=url, domain=domain, author=author, timestamp=timestamp, notes=notes)

    resolved_url = url
    if online and is_short_map_link(url):
        resolved_url = expand_short_link(url) or url

    if coords := coords_from_url(resolved_url):
        place.lat, place.lng, place.coord_source = coords
        place.coord_accuracy = "high"

    place.name = page_title(resolved_url) if online else (domain or url)
    # A bare domain means the page told us nothing useful — label it by source instead.
    if not place.name or place.name in (domain, url):
        city = city_from_url(resolved_url)
        place.name = f"{domain}" + (f" — {city}" if city else "")
    place.category = categorise(resolved_url, place.name)

    # Geocoding ladder: precise query first, then the page's region, then the
    # city hidden in the URL, then the domain.
    if place.lat is None and online:
        queries: list[str] = []
        region = guess_place_name(resolved_url, place.name)
        if region:
            place.notes = (place.notes + f" · area: {region}").strip(" ·")
            queries.append(region)
        if city := city_from_url(resolved_url):
            place.notes = (place.notes + f" · city: {city}").strip(" ·")
            queries.append(city)
        if title_area := area_from_title(place.name):
            place.notes = (place.notes + f" · city: {title_area}").strip(" ·")
            queries.append(title_area)
        if place.name and place.name != domain and not is_generic_title(place.name):
            queries.append(place.name)
        queries.append(domain)
        for query in queries:
            if coords := nominatim(query, hint):
                place.lat, place.lng = coords
                place.coord_source = "nominatim"
                # a bare region/city lands on a centroid — say so honestly
                place.coord_accuracy = "low" if query in (region, city, title_area) else "medium"
                break

    if place.lat is not None:
        place.coord_accuracy = place.coord_accuracy if place.coord_accuracy != "none" else "medium"
    return place


def dedupe(places: Iterable[Place]) -> list[Place]:
    """Collapse duplicate URLs, keeping the first (earliest) mention and merging notes."""
    by_url: dict[str, Place] = {}
    for p in places:
        key = re.sub(r"[?#].*$", "", p.url).rstrip("/")
        if key in by_url:
            keep = by_url[key]
            if p.notes and p.notes not in keep.notes:
                keep.notes = (keep.notes + " · " + p.notes).strip(" ·")
            if not keep.lat and p.lat:
                keep.lat, keep.lng = p.lat, p.lng
                keep.coord_source, keep.coord_accuracy = p.coord_source, p.coord_accuracy
        else:
            by_url[key] = p
    return list(by_url.values())


def to_geojson(places: Iterable[Place], *, title: str = "", source: str = "") -> dict:
    places = list(places)
    features = [p.to_feature() for p in places if p.lat is not None]
    lngs = [f["geometry"]["coordinates"][0] for f in features]
    lats = [f["geometry"]["coordinates"][1] for f in features]
    from datetime import datetime, timezone

    return {
        "type": "FeatureCollection",
        "bbox": [min(lngs), min(lats), max(lngs), max(lats)] if features else None,
        "_meta": {
            "crs": "EPSG:4326",
            "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "title": title,
            "source": source,
            "total_features": len(features),
            "total_links": len(places),
            "without_coordinates": len(places) - len(features),
        },
        "features": features,
    }
