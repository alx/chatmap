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
