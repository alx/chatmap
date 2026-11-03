"""Tests for URL parsing / geocoding helpers that need no network."""

from chatmap.geo import (
    build_place,
    categorise,
    city_from_url,
    coords_from_url,
    dedupe,
    is_generic_title,
    to_geojson,
    Place,
)


def test_coords_from_google_maps_at():
    url = "https://www.google.com/maps/place/Kyoto+Station/@34.9858,135.7588,15z"
    assert coords_from_url(url) == (34.9858, 135.7588, "url_at")


def test_coords_from_google_maps_data_pin():
    url = "https://www.google.com/maps/place/X/data=!4m2!3d35.0116!4d135.7681"
    assert coords_from_url(url) == (35.0116, 135.7681, "url_data_pin")


def test_coords_from_osm_hash():
    url = "https://www.openstreetmap.org/#map=15/35.0116/135.7681"
    assert coords_from_url(url) == (35.0116, 135.7681, "url_osm")


def test_coords_from_query_param():
    url = "https://maps.example/?q=48.8566,2.3522"
    assert coords_from_url(url) == (48.8566, 2.3522, "url_query")


def test_coords_absent_returns_none():
    assert coords_from_url("https://www.japan-guide.com/e/e2100.html") is None


def test_categorise_stay_hosts():
    assert categorise("https://www.airbnb.com/rooms/1") == "Stay"
    assert categorise("https://www.booking.com/hotel/jp/x.html") == "Stay"
    assert categorise("https://app.mokomoko.com/property/abc") == "Stay"


def test_categorise_food_and_transport():
    assert categorise("https://tabelog.com/kyoto/A2601/") == "Food"
    assert categorise("https://www.hyperdia.com/en/") == "Transport"
    assert categorise("https://www.jreast.co.jp/e/hokuriku-shinkansen/") == "Transport"


def test_categorise_falls_back_to_title():
    assert categorise("https://example.com/x", "Great ramen shop") == "Food"
    assert categorise("https://example.com/y", "Old castle") == "Sight"
    assert categorise("https://example.com/z", "random") == "Other"


def test_generic_titles_detected():
    assert is_generic_title("Airbnb")
    assert is_generic_title("Google Maps")
    assert is_generic_title("")
    assert not is_generic_title("Hoshinoya Tokyo")


def test_city_from_url_tabelog():
    assert city_from_url("https://tabelog.com/kyoto/A2601/A260301/26000123/") == "Kyoto"
    assert city_from_url("https://www.japan-guide.com/e/e2100.html") is None


def test_build_place_offline_uses_url_coordinates():
    place = build_place(
        "https://www.google.com/maps/place/Kyoto+Station/@34.9858,135.7588,15z",
        online=False,
    )
    assert place.lat == 34.9858
    assert place.lng == 135.7588
    assert place.coord_accuracy == "high"
    assert place.domain == "google.com"


def test_place_to_feature_shape():
    place = Place(name="Test", url="https://x.example", lat=35.0, lng=135.0, category="Sight")
    feature = place.to_feature()
    assert feature["type"] == "Feature"
    assert feature["geometry"]["type"] == "Point"
    assert feature["geometry"]["coordinates"] == [135.0, 35.0]  # GeoJSON is lng,lat
    assert feature["properties"]["name"] == "Test"
    assert feature["properties"]["icon"] == "🏯"


def test_dedupe_collapses_same_url_with_different_query():
    a = Place(name="A", url="https://x.example/p?id=1", lat=1.0, lng=2.0)
    b = Place(name="B", url="https://x.example/p?id=2", notes="second mention")
    out = dedupe([a, b])
    assert len(out) == 1
    assert "second mention" in out[0].notes


def test_dedupe_rescues_coordinates_from_duplicate():
    a = Place(name="A", url="https://x.example/p")
    b = Place(name="A", url="https://x.example/p", lat=48.0, lng=2.0)
    out = dedupe([a, b])
    assert len(out) == 1
    assert out[0].lat == 48.0


def test_to_geojson_skips_placeless_entries_but_counts_them():
    ok = Place(name="ok", url="https://x.example/1", lat=35.0, lng=135.0)
    lost = Place(name="lost", url="https://x.example/2")
    gj = to_geojson([ok, lost], title="t")
    assert len(gj["features"]) == 1
    assert gj["_meta"]["total_links"] == 2
    assert gj["_meta"]["without_coordinates"] == 1
    assert gj["bbox"] == [135.0, 35.0, 135.0, 35.0]


def test_to_geojson_empty():
    gj = to_geojson([], title="t")
    assert gj["features"] == []
    assert gj["bbox"] is None


# --- regressions found on a real 70-link export -----------------------------

def test_google_maps_name_taken_from_place_segment_not_data_blob():
    from chatmap.geo import page_title

    url = ("https://www.google.com/maps/place/KL+Forest+Eco+Park/"
           "@3.1513804,101.702139,15z/data=!4m6!3m5!1s0x31cc492539c32ab5!8m2!3d3.15!4d101.7")
    name = page_title(url)
    assert name == "Kl Forest Eco Park"
    assert "Data" not in name and "4M6" not in name


def test_google_maps_coords_with_tilt_and_heading():
    # "@5.372,100.328,1895a,35y,337.96h,44.44t" — the tail is not always "15z"
    url = "https://www.google.com/maps/place/Rubbish+Mountain/@5.3721597,100.3282053,1895a,35y,337.96h,44.44t/data=!3m1!1e3"
    assert coords_from_url(url) == (5.3721597, 100.3282053, "url_at")


def test_listing_title_area_extraction():
    from chatmap.geo import area_from_title

    assert area_from_title("Appartement · Kuala Lumpur · ★4,84 · 1 chambre") == "Kuala Lumpur"
    assert area_from_title("Appart'hôtel · Bandar Tanjung Tokong · ★4,84") == "Bandar Tanjung Tokong"
    assert area_from_title("Home exchange: Maizuru - Marie's home - Maizuru/Japan") == "Maizuru"


def test_akiya_and_homeexchange_categorised_as_stay():
    assert categorise("https://www.yabuakiyabank.jp/") == "Stay"
    assert categorise("https://akiyaair.com/x") == "Stay"
    assert categorise("https://www.homeexchange.com/homes/x") == "Stay"


def test_region_guess_ignores_incidental_mentions_in_body():
    """A page about HK ferries must not be pinned to a country it merely links to."""
    from chatmap.geo import guess_place_name

    # title says nothing; body text is unavailable offline, so there is no guess
    assert guess_place_name("https://example.invalid/x", "SkyPier Terminal Ferry") is None
    # a title that names a place is still trusted
    assert guess_place_name("https://example.invalid/x", "Hotel in Kyoto") == "Kyoto"


def test_japanese_area_from_real_estate_titles():
    """athome.co.jp titles name the municipality; that is what Nominatim matches."""
    from chatmap.geo import japanese_area

    assert japanese_area("【アットホーム】福陵ハイデンス 1階 ３ＬＤＫ[1017516187]福岡市早良区のマンション") == "福岡市早良区"
    assert japanese_area("【アットホーム】糸島市 志摩師吉１丁目（筑前前原駅） 2階建 ３ＬＤＫ") == "糸島市"
    assert japanese_area("東京都渋谷区の物件") == "東京都渋谷区"
    assert japanese_area("【アットホーム】認証中") is None
    assert japanese_area("Hoshinoya Tokyo") is None


def test_bot_check_detection():
    from chatmap.geo import _looks_like_bot_check

    assert _looks_like_bot_check("<html><title>Just a moment...</title>")
    assert _looks_like_bot_check("<title>【アットホーム】認証中</title>")
    assert not _looks_like_bot_check("<title>Hotel in Kyoto</title>" + "x" * 30000)


def test_athome_co_jp_not_jp_tld():
    """Regression: the pattern said athome.jp, but the site is athome.co.jp."""
    assert categorise("https://www.athome.co.jp/mansion/1017516187/") == "Stay"
    assert categorise("https://www.athome.co.jp/kodate/1112938618/") == "Stay"


def test_homeexchange_title_prefers_city_over_brand_and_host():
    """Regression: pinned to Kyoto (35.01,135.77) instead of Maizuru (35.47,135.39)."""
    from chatmap.geo import area_from_title

    title = "Home exchange: Maizuru - Marie's home - Maizuru/Japan - HomeExchange"
    assert area_from_title(title) == "Maizuru"


def test_apostrophe_host_not_treated_as_area():
    from chatmap.geo import area_from_title

    assert area_from_title("Home exchange: X - Marie's home - nowhere") is None
