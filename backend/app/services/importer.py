"""Playlist import: a list of songs pasted as text, matched against the catalogue.

People bring playlists from elsewhere as text: one song per line ("Ebi - Khaneh"),
a numbered list copied from a chat, or the CSV that Spotify export tools such as
Exportify write ("Track Name", "Artist Name(s)"). Links alone carry no names, and
the core does not reach out to other services to look them up, so a line that is
only a URL is skipped and counted.

Each line is matched on trigram similarity of the title and the artist, trying both
orders, because "Title - Artist" is as common as "Artist - Title". A weak match is
left out rather than guessed: a wrong song is worse than a missing one.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.text.normalizer import normalize_key

MAX_LINES = 300
MIN_TITLE_SIMILARITY = 0.45
MIN_SCORE = 0.5

_URL = re.compile(r"https?://\S+")
_NUMBERING = re.compile(r"^\s*(?:#?\d{1,4}\s*[.)\-:]\s*|\d{1,4}\s+(?=\D))")
_DURATION = re.compile(r"[\s(\[]*\b\d{1,2}:\d{2}(?::\d{2})?\b[)\]]*\s*$")
_NOISE = re.compile(
    r"[(\[](?:official|lyrics?|audio|video|music video|visuali[sz]er|hd|hq|remaster(?:ed)?"
    r"|\d{4} remaster|live|متن|موزیک ویدیو)[^)\]]*[)\]]",
    re.IGNORECASE,
)
_SEPARATORS = (" - ", " – ", " — ", " | ", " ‐ ", " -", "- ", "–", "—")
_BY = re.compile(r"^(?P<title>.+?)\s+(?:by|از)\s+(?P<artist>.+)$", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class Wanted:
    line: str
    first: str
    second: str


def _clean(raw: str) -> str:
    line = _URL.sub(" ", raw)
    line = _NUMBERING.sub("", line)
    line = _NOISE.sub(" ", line)
    line = _DURATION.sub("", line)
    line = line.replace("🎵", " ").replace("♪", " ")
    return " ".join(line.split()).strip(" -–—|•*")


def _split(line: str) -> tuple[str, str]:
    for sep in _SEPARATORS:
        if sep in line:
            first, second = line.split(sep, 1)
            if first.strip() and second.strip():
                return first.strip(), second.strip()
    by = _BY.match(line)
    if by:
        return by.group("artist").strip(), by.group("title").strip()
    return "", line


def _from_csv(body: str) -> list[Wanted] | None:
    head = body.lstrip("﻿").split("\n", 1)[0]
    if "Track Name" not in head:
        return None
    reader = csv.DictReader(io.StringIO(body.lstrip("﻿")))
    out = []
    for row in reader:
        title = (row.get("Track Name") or "").strip()
        artists = row.get("Artist Name(s)") or row.get("Artist Name") or ""
        artist = re.split(r"[;,]", artists)[0].strip()
        if title:
            out.append(
                Wanted(line=f"{artist} - {title}" if artist else title, first=artist, second=title)
            )
    return out


def parse(body: str) -> tuple[list[Wanted], int]:
    """The songs asked for, in order, and how many lines were skipped (links, blanks)."""
    from_csv = _from_csv(body)
    if from_csv is not None:
        return from_csv[:MAX_LINES], max(0, len(from_csv) - MAX_LINES)
    wanted: list[Wanted] = []
    skipped = 0
    for raw in body.splitlines():
        if not raw.strip():
            continue
        line = _clean(raw)
        if len(normalize_key(line)) < 2:
            skipped += 1
            continue
        first, second = _split(line)
        wanted.append(Wanted(line=line, first=first, second=second))
    return wanted[:MAX_LINES], skipped + max(0, len(wanted) - MAX_LINES)


_MATCH = text(
    """
    SELECT t.id,
           greatest(
             similarity(t.normalized_title, :b) * 0.65 + similarity(t.normalized_artist, :a) * 0.35,
             similarity(t.normalized_title, :a) * 0.65 + similarity(t.normalized_artist, :b) * 0.35
           ) AS score,
           greatest(similarity(t.normalized_title, :b), similarity(t.normalized_title, :a))
             AS title_score
      FROM tracks t
     WHERE t.canonical_track_id IS NULL AND NOT t.hidden AND t.playable
       AND (t.normalized_title % :b OR (:a <> '' AND t.normalized_title % :a)
            OR t.normalized_title = :b)
     ORDER BY score DESC, t.channels_count DESC, t.plays_total DESC, t.id
     LIMIT 1
    """
)


@dataclass(frozen=True, slots=True)
class Match:
    line: str
    track_id: int | None
    score: float


async def match(session: AsyncSession, wanted: list[Wanted]) -> list[Match]:
    out: list[Match] = []
    seen: set[int] = set()
    for item in wanted:
        a = normalize_key(item.first)
        b = normalize_key(item.second)
        if not a and not b:
            out.append(Match(item.line, None, 0.0))
            continue
        if not b:
            a, b = "", a
        row = (await session.execute(_MATCH.bindparams(a=a, b=b))).first()
        if row is None:
            out.append(Match(item.line, None, 0.0))
            continue
        score = float(row.score)
        # A title-only line scores on the title alone.
        if not a:
            score = float(row.title_score)
        good = float(row.title_score) >= MIN_TITLE_SIMILARITY and score >= MIN_SCORE
        track_id = int(row.id) if good and int(row.id) not in seen else None
        if track_id is not None:
            seen.add(track_id)
        out.append(Match(item.line, track_id, round(score, 2)))
    return out
