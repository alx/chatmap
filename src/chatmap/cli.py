"""Command line interface:  chatmap run <export> -o <outdir>"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .geo import CATEGORIES, build_place, dedupe, to_geojson
from .parse import iter_link_messages, parse_files
from .render import write as write_html


def _cmd_links(args: argparse.Namespace) -> int:
    messages = parse_files([Path(p) for p in args.export])
    rows = []
    for msg in iter_link_messages(messages):
        for url in msg.links:
            rows.append({"timestamp": msg.timestamp, "author": msg.author, "url": url})
    if not rows:
        print("No links found.", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(rows, indent=2, ensure_ascii=False))
    else:
        for r in rows:
            print(f"{r['timestamp'] or '?':19}  {r['author']:20}  {r['url']}")
    print(f"\n{len(rows)} link(s) found.", file=sys.stderr)
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    messages = parse_files([Path(p) for p in args.export])
    if not messages:
        print("No messages parsed — is the export path right?", file=sys.stderr)
        return 1

    title_overrides: dict[str, str] = {}
    if args.titles:
        title_overrides = json.loads(Path(args.titles).read_text(encoding="utf-8"))

    places = []
    for msg in iter_link_messages(messages):
        for url in msg.links:
            notes = " ".join(
                line for line in msg.body.splitlines() if "http" not in line
            ).strip()[:280]
            place = build_place(
                url,
                author=msg.author,
                timestamp=msg.timestamp or "",
                notes=notes,
                hint=args.hint,
                online=not args.offline,
                title_override=title_overrides.get(url),
            )
            places.append(place)
            print(f"  {place.category:9} {place.coord_accuracy:6} {place.name[:60]}", file=sys.stderr)

    places = dedupe(places)
    geojson = to_geojson(places, title=args.title, source=str(args.export))

    outdir = Path(args.output)
    outdir.mkdir(parents=True, exist_ok=True)

    geojson_path = outdir / "locations.geojson"
    geojson_path.write_text(json.dumps(geojson, indent=2, ensure_ascii=False), encoding="utf-8")

    html_path = outdir / "index.html"
    write_html(
        geojson,
        str(html_path),
        title=args.title,
        description=args.description,
        noindex=args.noindex,
        colors={k: v["color"] for k, v in CATEGORIES.items()},
        icons={k: v["icon"] for k, v in CATEGORIES.items()},
    )

    meta = geojson["_meta"]
    print(
        f"\n✓ {meta['total_features']} mapped / {meta['total_links']} links "
        f"({meta['without_coordinates']} without coordinates)",
        file=sys.stderr,
    )
    print(f"  {geojson_path}\n  {html_path}", file=sys.stderr)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="chatmap",
        description="Turn a group chat export into a shareable interactive map.",
    )
    p.add_argument("--version", action="version", version=f"chatmap {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    links = sub.add_parser("links", help="list every link shared in the chat")
    links.add_argument("export", nargs="+", help="chat.md / data.json / export directory")
    links.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    links.set_defaults(func=_cmd_links)

    run = sub.add_parser("run", help="build locations.geojson + index.html")
    run.add_argument("export", nargs="+", help="chat.md / data.json / export directory")
    run.add_argument("-o", "--output", default="out", help="output directory (default: out)")
    run.add_argument("-t", "--title", default="Chat map", help="map title")
    run.add_argument("-d", "--description", default="", help="meta description")
    run.add_argument("--hint", default="", help="region hint appended to geocoding queries, e.g. 'Japan'")
    run.add_argument("--offline", action="store_true", help="no network: only URL-embedded coordinates")
    run.add_argument("--noindex", action="store_true", help='add <meta name="robots" content="noindex">')
    run.add_argument(
        "--titles",
        metavar="FILE",
        help="JSON file of {url: title} to use when the page itself is blocked "
             "(bot checks, paywalls). Survives rate limits that break live scraping.",
    )
    run.set_defaults(func=_cmd_run)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
