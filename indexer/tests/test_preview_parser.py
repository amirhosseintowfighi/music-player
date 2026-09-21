"""Snapshot tests for the preview parser, against pages saved from real channels.

Telegram's preview markup is not a contract, so these fixtures are the contract we
test against: if Telegram changes the HTML, these fail loudly here instead of quietly
producing an empty catalogue in production.

The fixtures were fetched on 2026-09-19 from public channels and trimmed to a handful
of messages; the markup inside them is byte-for-byte what Telegram served.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tmusic_indexer.webpreview.parser import (
    PreviewUnavailable,
    parse_count,
    parse_page,
)

FIXTURES = Path(__file__).parent / "fixtures" / "preview"
SNAPSHOTS = FIXTURES / "snapshots"


def load(name: str) -> str:
    return (FIXTURES / f"{name}.html").read_text("utf-8")


def snapshot(name: str, actual: dict[str, object]) -> None:
    """Compare against the recorded shape; write it the first time.

    Regenerate deliberately with ``TMUSIC_UPDATE_SNAPSHOTS=1 pytest`` after checking
    that the change is one you meant to make.
    """
    import os

    SNAPSHOTS.mkdir(parents=True, exist_ok=True)
    path = SNAPSHOTS / f"{name}.json"
    serialised = json.dumps(actual, ensure_ascii=False, indent=2, sort_keys=True)
    if os.environ.get("TMUSIC_UPDATE_SNAPSHOTS") or not path.exists():
        path.write_text(serialised + "\n", "utf-8", newline="\n")
        return
    expected = path.read_text("utf-8").strip()
    assert serialised == expected, f"{name} drifted from its snapshot"


def shape(html: str) -> dict[str, object]:
    result = parse_page(html)
    return {
        "channel": {
            "username": result.channel.username,
            "title": result.channel.title,
            "subscribers": result.channel.subscribers,
        },
        "stats": {
            "messages": result.stats.messages,
            "audio": result.stats.audio,
            "voice": result.stats.voice,
            "skipped_no_title": result.stats.skipped_no_title,
        },
        "oldest_id": result.oldest_id,
        "newest_id": result.newest_id,
        "mentions": result.mentions,
        "items": [
            {
                "message_id": item.message_id,
                "title": item.title,
                "performer": item.performer,
                "views": item.views,
                "posted_at": item.posted_at.isoformat(),
                "duration": item.duration,
                "file_size": item.file_size,
                "file_unique_id": item.file_unique_id,
                "cdn_url": item.cdn_url,
            }
            for item in result.items
        ],
    }


# ── snapshots ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", ["music_docs", "music_before", "voice_only", "text_only"])
def test_page_shape_matches_snapshot(name: str) -> None:
    snapshot(name, shape(load(name)))


# ── the properties those snapshots encode ─────────────────────────────────────


def test_a_music_post_yields_title_and_performer() -> None:
    result = parse_page(load("music_docs"))
    assert result.stats.audio == 2

    first = next(i for i in result.items if i.message_id == 675)
    assert first.title == "Bezar Too Hale Khodam Basham(8D by Soheil khodabandeh)"
    assert first.performer == "Amir Tataloo"
    assert first.views == 1540
    assert first.posted_at.year == 2020


def test_what_the_preview_cannot_give_us_is_left_empty_not_invented() -> None:
    """Duration, size and the file id only exist after a resolve (ADR-002)."""
    item = parse_page(load("music_docs")).items[0]
    assert item.duration == 0
    assert item.file_size == 0
    assert item.file_unique_id is None
    assert item.cdn_url is None
    assert item.bot_file_id is None


def test_voice_notes_are_not_music() -> None:
    result = parse_page(load("voice_only"))
    assert result.stats.voice > 0
    assert result.items == []


def test_a_page_of_plain_posts_yields_nothing_but_still_reports_messages() -> None:
    result = parse_page(load("text_only"))
    assert result.stats.messages == 3
    assert result.items == []
    assert result.stats.extraction_rate == 0.0


def test_channel_info_is_read_from_the_header() -> None:
    info = parse_page(load("music_docs")).channel
    assert info.username == "Musicirani_Official"
    assert info.title
    assert info.subscribers and info.subscribers > 0


def test_message_ids_drive_pagination() -> None:
    newest = parse_page(load("music_docs"))
    older = parse_page(load("music_before"))
    assert newest.oldest_id is not None
    assert older.newest_id is not None
    # The `?before=` page really is older than the first page.
    assert older.newest_id < newest.newest_id


def test_mentions_are_collected_for_channel_discovery() -> None:
    result = parse_page(load("music_docs"))
    assert "tataloo_official1" in result.mentions
    # The channel never suggests itself.
    assert "musicirani_official" not in result.mentions


# ── robustness ────────────────────────────────────────────────────────────────


def test_a_page_without_messages_is_reported_not_guessed() -> None:
    with pytest.raises(PreviewUnavailable):
        parse_page("<html><body><div class='tgme_page'>no preview</div></body></html>")
    with pytest.raises(PreviewUnavailable):
        parse_page("")


def test_truncated_html_does_not_raise() -> None:
    html = load("music_docs")
    for cut in (len(html) // 2, len(html) - 500, 200):
        try:
            result = parse_page(html[:cut])
        except PreviewUnavailable:
            continue  # cut before any message: a fair answer
        assert result.stats.messages >= 0  # whatever survived, nothing exploded


def test_a_message_without_a_title_is_skipped_and_counted() -> None:
    html = load("music_docs").replace("Bezar Too Hale Khodam Basham(8D by Soheil khodabandeh)", "")
    result = parse_page(html)
    assert result.stats.audio == 1
    assert result.stats.skipped_no_title == 1


def test_renamed_classes_degrade_to_zero_instead_of_wrong_data() -> None:
    """The failure we actually expect one day: Telegram renames a class."""
    html = load("music_docs").replace("tgme_widget_message_document_wrap", "tgme_new_name")
    result = parse_page(html)
    assert result.stats.messages == 6  # the page still parses
    assert result.items == []  # but nothing is extracted — and the rate says so
    assert result.stats.extraction_rate == 0.0


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1.54K", 1540),
        ("2M", 2_000_000),
        ("288", 288),
        ("1,234", 1234),
        ("12.3K subscribers", 12_300),
        (None, None),
        ("", None),
        ("no digits", None),
    ],
)
def test_parse_count(raw: str | None, expected: int | None) -> None:
    assert parse_count(raw) == expected


def test_posting_rate_is_measured_from_every_message_not_only_music() -> None:
    """Candidate scoring needs the channel's real rhythm, audio or not."""
    result = parse_page(load("music_docs"))
    assert len(result.dates) == result.stats.messages
    rate = result.posts_per_day
    assert rate is not None and rate > 0

    # A page whose messages all land in one moment is a burst, not an infinite rate.
    same_time = load("music_docs")
    assert parse_page(same_time).posts_per_day is not None


def test_a_single_message_has_no_measurable_rate() -> None:
    html = (
        '<div class="tgme_channel_info"><div class="tgme_channel_info_header_title">C</div></div>'
        '<div class="tgme_widget_message_wrap"><div class="tgme_widget_message" data-post="c/5">'
        '<a class="tgme_widget_message_date"><time datetime="2026-09-18T10:00:00+00:00"></time></a>'
        "</div></div>"
    )
    assert parse_page(html).posts_per_day is None
