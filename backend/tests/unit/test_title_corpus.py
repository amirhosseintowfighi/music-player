"""How good is the parser, in a number (ADR-003 phase 13).

Run it on its own to see the report:

    pytest tests/unit/test_title_corpus.py -s -q

The floor asserted here is a ratchet, not a target: it exists so a future change that
makes the parser worse fails the build. Raise it when the number goes up.
"""

from __future__ import annotations

import sys

import pytest

from app.domain.text.artist_parser import parse_track_meta
from app.domain.text.normalizer import normalize_key
from tests.fixtures.persian_titles import CASES, KNOWN_ARTIST_KEYS

# Measured on 2026-09-21. Baseline before phase 13 was artist 67% / title 62%; the
# parser changes in that phase took it to 100% / 100% on this corpus. The floors sit
# just under that so an accidental regression fails the build.
MIN_ARTIST_ACCURACY = 0.95
MIN_TITLE_ACCURACY = 0.95


def parsed(case: tuple[str | None, str | None, str, str, str]) -> tuple[str, str]:
    """Exactly how ingest calls the parser, known artists included.

    The catalogue knows its own artists after the first few hundred tracks, and the
    parser uses that to settle "A - B" (which side is the name?). Measuring without it
    would measure a situation that only exists on an empty database.
    """
    title, performer, channel, _, _ = case
    meta = parse_track_meta(
        title,
        performer,
        channel_names=[channel],
        known_artist=lambda key: key in KNOWN_ARTIST_KEYS,
    )
    return (meta.artists[0] if meta.artists else ""), meta.title


def emit(report: str) -> None:
    """Prints the report without dying on a console that cannot show Persian."""
    encoding = sys.stdout.encoding or "utf-8"
    sys.stdout.write(report.encode(encoding, "replace").decode(encoding, "replace") + "\n")


def same(left: str, right: str) -> bool:
    """Compared the way the catalogue compares them, not byte for byte."""
    return normalize_key(left) == normalize_key(right)


def test_parser_accuracy_on_real_channel_titles(capsys: pytest.CaptureFixture[str]) -> None:
    artist_hits = title_hits = both = 0
    misses: list[str] = []

    for case in CASES:
        raw_title, performer, _channel, want_artist, want_title = case
        got_artist, got_title = parsed(case)
        artist_ok = same(got_artist, want_artist)
        title_ok = same(got_title, want_title)
        artist_hits += artist_ok
        title_hits += title_ok
        both += artist_ok and title_ok
        if not (artist_ok and title_ok):
            misses.append(
                f"  {raw_title!r} (performer={performer!r})\n"
                f"      want artist={want_artist!r} title={want_title!r}\n"
                f"      got  artist={got_artist!r} title={got_title!r}"
            )

    total = len(CASES)
    with capsys.disabled():
        emit(
            f"\n== parser accuracy on {total} real titles ==========================\n"
            f"artist correct ....... {artist_hits}/{total} ({artist_hits / total:.0%})\n"
            f"title correct ........ {title_hits}/{total} ({title_hits / total:.0%})\n"
            f"both correct ......... {both}/{total} ({both / total:.0%})\n"
            + ("\n".join(misses) + "\n" if misses else "")
            + "=================================================================="
        )

    assert artist_hits / total >= MIN_ARTIST_ACCURACY
    assert title_hits / total >= MIN_TITLE_ACCURACY


def test_the_parser_never_invents_an_artist() -> None:
    """A wrong artist is worse than no artist: it poisons search and grouping."""
    for case in CASES:
        _, _, _, want_artist, _ = case
        if want_artist:
            continue
        got_artist, _ = parsed(case)
        assert got_artist == "", f"invented {got_artist!r} for {case[0]!r}"


def test_the_channel_name_is_never_mistaken_for_the_artist() -> None:
    for case in CASES:
        _, _, channel, _, _ = case
        got_artist, _ = parsed(case)
        assert not same(got_artist, channel), f"used the channel as the artist: {case[0]!r}"
