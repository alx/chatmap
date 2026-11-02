# Contributing

Small tool, two rules.

## 1. The pipeline must never crash on a weird link

Every chat contains links nobody expected: localized domains, shorteners,
URLs with unicode, tracking parameters three screens long, and links to pages
that no longer exist. `chatmap` handles them by degrading — an unresolvable
link still becomes a listed place with `coord_accuracy: "none"`.

If you find an input that raises, that's a bug. Add it to `tests/`.

## 2. Every coordinate carries an honest accuracy

| Value | Meaning |
|---|---|
| `high` | Taken from the link itself |
| `medium` | Geocoded from a specific place name |
| `low` | Region/city centroid |
| `none` | No coordinates; listed in the sidebar only |

Do not promote an accuracy level to make a map look better. A pin in the wrong
prefecture labelled `low` is honest; the same pin labelled `high` is a lie.

## Running things

```bash
python3 -m pytest -q
PYTHONPATH=src python3 -m chatmap.cli links examples/golden-route/chat.md
PYTHONPATH=src python3 -m chatmap.cli run examples/golden-route/chat.md -o /tmp/out --hint Japan
```

Tests must pass offline — network calls belong behind `--offline` checks and
are exercised by hand, not in CI.

## Adding a site

Most useful contribution: teach the classifier a domain. In
`src/chatmap/geo.py`:

```python
_DOMAIN_CATEGORY = [
    (re.compile(r"(airbnb|booking\.com|agoda|...)"), "Stay"),
    ...
]
```

Add a test in `tests/test_geo.py` alongside it.

## Adding a city

If a directory site hides the city in a URL slug, add it to `_CITY_SLUGS` or
`_KNOWN_PLACES`.

## Commits

Conventional commits, one logical change each (`feat:`, `fix:`, `docs:`,
`test:`). Keep the diff reviewable.
