from pathlib import Path

from chatmap.parse import Message, extract_links, iter_link_messages, parse_file

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "golden-route" / "chat.md"


def test_extract_links_finds_urls():
    text = "look https://a.example/x and http://b.example/y?z=1"
    assert extract_links(text) == ["https://a.example/x", "http://b.example/y?z=1"]


def test_extract_links_strips_trailing_punctuation():
    assert extract_links("see https://a.example/x.") == ["https://a.example/x"]
    assert extract_links("(https://a.example/x)") == ["https://a.example/x"]
    assert extract_links("https://a.example/x**") == ["https://a.example/x"]


def test_extract_links_dedupes_keeping_order():
    text = "https://a.example https://b.example https://a.example"
    assert extract_links(text) == ["https://a.example", "https://b.example"]


def test_parse_markdown_example():
    messages = parse_file(EXAMPLE)
    assert len(messages) > 20
    first = messages[0]
    assert first.author == "Yuki"
    assert first.timestamp == "2026-09-02 09:14:22"
    assert "Golden Route" in first.body


def test_parse_markdown_multiline_body_is_joined():
    messages = parse_file(EXAMPLE)
    # a message with a link keeps its text body intact
    linked = [m for m in messages if m.links]
    assert linked
    assert all(isinstance(m.body, str) and m.body for m in linked)


def test_parse_tolerates_blank_lines():
    msg = Message(timestamp="2026-01-01 10:00:00", author="A", body="hello\n\nworld")
    assert "\n" in msg.body


def test_iter_link_messages_only_yields_messages_with_links():
    messages = parse_file(EXAMPLE)
    link_msgs = list(iter_link_messages(messages))
    assert link_msgs
    assert all(m.links for m in link_msgs)
    assert len(link_msgs) < len(messages)
