"""Fetch and normalise Mokomoko property listings.

Mokomoko (app.mokomoko.com) is an English-language Japanese akiya/real-estate
portal. Its paginate endpoint returns full JSON for a bounding box in a single
response — the `page` parameter is accepted but ignored by the server, so one
request per bbox gets everything.

    https://app.mokomoko.com/properties/paginate
      ?isCondo=null&page=0&currency=usd
      &neLon=…&neLat=…&swLon=…&swLat=…
      &isMetric=true&dataSource=all&parkingOnly=false
      &isFullOwnershipOnly=false&isSold=false&sortBy=POPULAR&boost=false

Fields observed in the wild (kept verbatim, plus a normalised subset):

    property_id, address, prefecture, lat, lon,
    building_area, land_area, construction_year,
    listed_price_yen, listed_price_foreign,
    madori_main_room_size_rank, madori_other_rooms_count,
    is_listed, is_disabled, is_hidden, is_boosted, is_featured, is_liked,
    like_count, view_count, photos[], illustrations[]

`is_disabled` is true for almost everything (the listing is not publicly
visible), which makes it a poor filter — see `usable_listings`.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass, field, asdict

PAGINATE = "https://app.mokomoko.com/properties/paginate"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"


@dataclass
class Listing:
    property_id: str
    lat: float
    lon: float
    address: str = ""
    prefecture: str = ""
    building_area: float | None = None
    land_area: float | None = None
    construction_year: int | None = None
    price_yen: float | None = None
    price_foreign: float | None = None
    room_size_rank: int | None = None
    rooms: int | None = None
    is_listed: bool = False
    is_disabled: bool = False
    is_hidden: bool = False
    like_count: int = 0
    view_count: int = 0
    raw: dict = field(default_factory=dict, repr=False)

    @property
    def age(self) -> int | None:
        return None if not self.construction_year else 2026 - self.construction_year

    @property
    def price_per_m2(self) -> float | None:
        if not self.price_yen or not self.building_area:
            return None
        return round(self.price_yen / self.building_area, 1)

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("raw", None)
        d["age"] = self.age
        d["price_per_m2"] = self.price_per_m2
        return d


def _f(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _i(v):
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def to_listing(p: dict) -> Listing | None:
    lat, lon = _f(p.get("lat")), _f(p.get("lon"))
    if lat is None or lon is None:
        return None
    return Listing(
        property_id=str(p.get("property_id") or ""),
        lat=lat,
        lon=lon,
        address=(p.get("address") or "").strip(),
        prefecture=(p.get("prefecture") or "").strip(),
        building_area=_f(p.get("building_area")),
        land_area=_f(p.get("land_area")),
        construction_year=_i(p.get("construction_year")),
        price_yen=_f(p.get("listed_price_yen")),
        price_foreign=_f(p.get("listed_price_foreign")),
        room_size_rank=_i(p.get("madori_main_room_size_rank")),
        rooms=_i(p.get("madori_other_rooms_count")),
        is_listed=bool(p.get("is_listed")),
        is_disabled=bool(p.get("is_disabled")),
        is_hidden=bool(p.get("is_hidden")),
        like_count=_i(p.get("like_count")) or 0,
        view_count=_i(p.get("view_count")) or 0,
        raw=p,
    )


def fetch_bbox(
    sw_lat: float, sw_lon: float, ne_lat: float, ne_lon: float, timeout: float = 60
) -> list[Listing]:
    """Every listing the API returns for a bounding box."""
    query = {
        "isCondo": "null",
        "page": "0",
        "currency": "usd",
        "neLon": f"{ne_lon}",
        "neLat": f"{ne_lat}",
        "swLon": f"{sw_lon}",
        "swLat": f"{sw_lat}",
        "isMetric": "true",
        "dataSource": "all",
        "parkingOnly": "false",
        "isFullOwnershipOnly": "false",
        "isSold": "false",
        "sortBy": "POPULAR",
        "boost": "false",
    }
    url = f"{PAGINATE}?{urllib.parse.urlencode(query)}"
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        data = json.loads(resp.read().decode("utf-8", errors="replace"))
    rows = ((data.get("results") or {}).get("properties")) or []
    out = [to_listing(p) for p in rows]
    return [x for x in out if x is not None]


def usable_listings(listings: list[Listing]) -> list[Listing]:
    """Listings worth showing a human.

    `is_disabled` is set on ~95% of rows and does not mean "unavailable" — it
    appears to flag rows withheld from the public grid while still returned to
    the map view. `is_hidden` and unlisted rows are the real exclusions.
    """
    return [x for x in listings if x.is_listed and not x.is_hidden]
