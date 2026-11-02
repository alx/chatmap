"""Render tests — the generated page must be self-contained and XSS-safe."""

from chatmap.geo import Place, to_geojson
from chatmap.render import render


def _sample_geojson():
    return to_geojson(
        [Place(name="Kyoto Station", url="https://x.example/1", lat=34.9858, lng=135.7588,
               category="Transport", author="Yuki", notes="meeting point")],
        title="Sample",
    )


def test_render_contains_geojson_and_leaflet():
    html = render(_sample_geojson(), title="Sample map")
    assert "leaflet" in html
    assert "Kyoto Station" in html
    assert "L.map(" in html
    assert "<!DOCTYPE html>" in html


def test_render_without_robots_is_indexable():
    html = render(_sample_geojson(), title="Sample")
    assert "noindex" not in html


def test_render_with_noindex():
    html = render(_sample_geojson(), title="Sample", noindex=True)
    assert '<meta name="robots" content="noindex, nofollow">' in html


def test_render_escapes_title():
    html = render(_sample_geojson(), title='<script>alert("x")</script>')
    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html


def test_render_reports_place_count():
    html = render(_sample_geojson(), title="Sample")
    assert "1 places" in html


def test_render_handles_empty_geojson():
    html = render(to_geojson([], title="Empty"), title="Empty")
    assert "No geolocated places yet" in html
    assert "L.map(" in html
