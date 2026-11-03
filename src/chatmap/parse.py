"""Parse chat exports into a normalised list of messages.

Supported inputs
----------------
* ``sigexport`` markdown export (``chat.md``) — the format produced by
  https://github.com/carderne/signal-export
* ``sigexport`` JSON export (``data.json``)
* plain text logs of the shape ``[YYYY-MM-DD HH:MM:SS] Author: message``
* Signal Desktop plain-text exports pasted into a ``.txt`` file

The parser is deliberately forgiving: unknown lines are appended to the
previous message. This matters because chat messages legitimately contain
blank lines, quoted replies, attachments and reactions.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Iterable, Iterator

# [2026-11-02 22:20:31] Marie: https://example.com
# [2019-05-29, 15:04] Me: hello
_TS_LINE = re.compile(
    r"^\[(?P<date>\d{4}-\d{2}-\d{2})(?:[ ,]+)(?P<time>\d{1,2}:\d{2}(?::\d{2})?)"
    r"(?P<tz>[+-]\d{2}:?\d{2})?\]\s*(?P<author>[^:]{1,80}?):\s?(?P<body>.*)$"
)

_URL = re.compile(r"https?://[^\s<>()\[\]\"']+")
_TRAILING = ".,;:!?»”'\""


@dataclass
class Message:
    """One chat message."""

    timestamp: str | None
    author: str
    body: str
    source: str = ""
    links: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _clean_url(url: str) -> str:
    """Trim trailing punctuation and markdown emphasis, without eating URL syntax.

    Underscores are legal in URLs ("sref=list_map"), so they must not be treated
    as a terminator — only trailing "*" / "_" runs left over from markdown bold
    and italic markers get stripped.
    """
    url = url.rstrip(_TRAILING)
    url = re.sub(r"[*_]+$", "", url)     # trailing markdown emphasis
    return url.rstrip(_TRAILING)


def extract_links(text: str) -> list[str]:
    """Return unique http(s) URLs found in ``text``, in order of appearance."""
    seen: dict[str, None] = {}
    for raw in _URL.findall(text):
        url = _clean_url(raw)
        if url and url not in seen:
            seen[url] = None
    return list(seen)


def parse_markdown(path: Path) -> list[Message]:
    """Parse a ``sigexport``-style ``chat.md`` file."""
    messages: list[Message] = []
    current: Message | None = None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = _TS_LINE.match(line)
        if m:
            ts = f"{m.group('date')} {m.group('time')}"
            current = Message(timestamp=ts, author=m.group("author").strip(), body=m.group("body"))
            messages.append(current)
        elif current is not None:
            current.body += "\n" + line

    for msg in messages:
        msg.body = msg.body.strip()
        msg.source = str(path)
        msg.links = extract_links(msg.body)
    return messages


def parse_json(path: Path) -> list[Message]:
    """Parse a ``sigexport`` ``data.json`` file (best effort)."""
    data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    rows: Iterable[dict]
    if isinstance(data, dict):
        rows = data.get("messages") or data.get("data") or []
    else:
        rows = data

    messages: list[Message] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        body = row.get("body") or row.get("text") or ""
        ts = row.get("timestamp") or row.get("date_sent") or row.get("sent_at")
        if isinstance(ts, (int, float)):
            from datetime import datetime, timezone

            ts = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        author = row.get("author") or row.get("sender") or row.get("source") or ""
        if isinstance(author, dict):
            author = author.get("name") or author.get("displayName") or ""
        msg = Message(timestamp=str(ts) if ts else None, author=str(author), body=body, source=str(path))
        msg.links = extract_links(body)
        messages.append(msg)
    return messages


def detect_format(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".json":
        return "json"
    return "markdown"


def parse_file(path: Path) -> list[Message]:
    path = Path(path)
    if path.is_dir():
        # sigexport puts one folder per chat; prefer chat.md then data.json
        for name in ("chat.md", "data.json"):
            candidate = path / name
            if candidate.exists():
                return parse_file(candidate)
        raise FileNotFoundError(f"no chat.md or data.json in {path}")
    fmt = detect_format(path)
    return parse_json(path) if fmt == "json" else parse_markdown(path)


def parse_files(paths: Iterable[Path]) -> list[Message]:
    out: list[Message] = []
    for p in paths:
        out.extend(parse_file(Path(p)))
    out.sort(key=lambda m: m.timestamp or "")
    return out


def iter_link_messages(messages: Iterable[Message]) -> Iterator[Message]:
    for msg in messages:
        if msg.links:
            yield msg
