"""ID3 tag reading, from the first bytes of a file.

Telegram gives us title, performer and duration but never album, year or genre
(ARCHITECTURE §18) — those live in the file's own tags. Reading them needs only the
header, which is at the very start of an MP3, so one short range request is enough.

Written by hand rather than pulling in mutagen: we need five frames out of ID3v2 and
the fallback ID3v1 block, which is ~120 lines, while mutagen would add a dependency to
the edge image for the same result. Anything we cannot parse is simply skipped —
a missing album is normal, a crash is not.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# ID3v2 frames we care about (2.3 and 2.4 use the same four-character ids).
FRAMES = {
    b"TALB": "album",
    b"TYER": "year",  # 2.3
    b"TDRC": "year",  # 2.4 ("recording time", may be a full date)
    b"TCON": "genre",
    b"TIT2": "title",
    b"TPE1": "artist",
}
MAX_FRAME_BYTES = 4096  # a single text frame is never legitimately bigger
ID3V1_SIZE = 128
# "(17)" or "(17)Rock" — the old numeric genre reference.
_V1_GENRE_REF = re.compile(r"^\((\d+)\)")
# Winamp's original list; only the common ones matter, the rest fall through as text.
V1_GENRES = {
    0: "Blues",
    1: "Classic Rock",
    2: "Country",
    7: "Hip-Hop",
    8: "Jazz",
    9: "Metal",
    12: "Other",
    13: "Pop",
    14: "R&B",
    15: "Rap",
    17: "Rock",
    20: "Alternative",
    24: "Soundtrack",
    32: "Classical",
    52: "Electronic",
    77: "Trance",
    80: "Folk",
}


@dataclass(frozen=True, slots=True)
class Tags:
    title: str | None = None
    artist: str | None = None
    album: str | None = None
    year: int | None = None
    genre: str | None = None

    @property
    def empty(self) -> bool:
        return not any((self.title, self.artist, self.album, self.year, self.genre))


def _syncsafe(data: bytes) -> int:
    """ID3v2 sizes use 7 bits per byte so a size can never look like a frame sync."""
    size = 0
    for byte in data:
        size = (size << 7) | (byte & 0x7F)
    return size


def _plain_size(data: bytes) -> int:
    return int.from_bytes(data, "big")


def _decode(payload: bytes) -> str | None:
    """One ID3v2 text frame: first byte is the encoding, the rest is the string."""
    if not payload:
        return None
    encoding, body = payload[0], payload[1:]
    try:
        if encoding == 0:  # latin-1
            text = body.decode("latin-1")
        elif encoding == 1:  # UTF-16 with BOM
            text = body.decode("utf-16")
        elif encoding == 2:  # UTF-16BE without BOM
            text = body.decode("utf-16-be")
        else:  # 3 = UTF-8, and anything unknown is most likely UTF-8 in the wild
            text = body.decode("utf-8")
    except (UnicodeDecodeError, LookupError):
        return None
    # Frames are NUL-terminated and may carry several values; the first one wins.
    text = text.split("\x00")[0].strip()
    return text or None


def _year(value: str | None) -> int | None:
    if not value:
        return None
    match = re.search(r"(\d{4})", value)
    if not match:
        return None
    year = int(match.group(1))
    return year if 1900 <= year <= 2100 else None


def _genre(value: str | None) -> str | None:
    if not value:
        return None
    match = _V1_GENRE_REF.match(value)
    if match:
        rest = value[match.end() :].strip()
        return rest or V1_GENRES.get(int(match.group(1)))
    return value


def parse_id3v2(data: bytes) -> Tags:
    """Reads the ID3v2 header at the start of ``data``. Partial data is fine."""
    if len(data) < 10 or data[:3] != b"ID3":
        return Tags()
    major = data[3]
    flags = data[5]
    tag_size = _syncsafe(data[6:10])
    if major not in (2, 3, 4):
        return Tags()

    position = 10
    if flags & 0x40 and major >= 3:  # extended header, skip it
        if len(data) < position + 4:
            return Tags()
        ext_size = (
            _syncsafe(data[position : position + 4])
            if major == 4
            else _plain_size(data[position : position + 4]) + 4
        )
        position += max(ext_size, 0)

    found: dict[str, str] = {}
    end = min(len(data), 10 + tag_size)
    id_size, header_size = (3, 6) if major == 2 else (4, 10)

    while position + header_size <= end:
        frame_id = data[position : position + id_size]
        if not frame_id.strip(b"\x00"):
            break  # padding: the rest of the tag is empty
        raw_size = data[position + id_size : position + id_size + (3 if major == 2 else 4)]
        size = _plain_size(raw_size) if major != 4 else _syncsafe(raw_size)
        if size <= 0 or size > MAX_FRAME_BYTES:
            break
        body_start = position + header_size
        body = data[body_start : body_start + size]
        if len(body) < size:
            break  # truncated prefix: stop rather than guess
        key = FRAMES.get(frame_id if major != 2 else _V2_TO_V3.get(frame_id, frame_id))
        if key and key not in found:
            value = _decode(body)
            if value:
                found[key] = value
        position = body_start + size

    return Tags(
        title=found.get("title"),
        artist=found.get("artist"),
        album=found.get("album"),
        year=_year(found.get("year")),
        genre=_genre(found.get("genre")),
    )


# ID3v2.2 used three-character ids; map the ones we read onto their 2.3 names.
_V2_TO_V3 = {b"TAL": b"TALB", b"TYE": b"TYER", b"TCO": b"TCON", b"TT2": b"TIT2", b"TP1": b"TPE1"}


def parse_id3v1(tail: bytes) -> Tags:
    """The 128-byte block at the end of an MP3. Used only when there is no v2 tag."""
    if len(tail) < ID3V1_SIZE or tail[-ID3V1_SIZE:][:3] != b"TAG":
        return Tags()
    block = tail[-ID3V1_SIZE:]

    def field(start: int, length: int) -> str | None:
        raw = block[start : start + length].split(b"\x00")[0]
        try:
            text = raw.decode("latin-1").strip()
        except UnicodeDecodeError:
            return None
        return text or None

    genre_byte = block[127]
    return Tags(
        title=field(3, 30),
        artist=field(33, 30),
        album=field(63, 30),
        year=_year(field(93, 4)),
        genre=V1_GENRES.get(genre_byte),
    )


def parse(head: bytes, tail: bytes = b"") -> Tags:
    """Best tags available: ID3v2 from the head, falling back to ID3v1 at the tail."""
    tags = parse_id3v2(head)
    if not tags.empty:
        return tags
    return parse_id3v1(tail)


# ── embedded artwork ──────────────────────────────────────────────────────────
#
# APIC is the only place a cover exists for a track Telegram gave no thumbnail for.
# It is read from the same head-of-file bytes the tag parser already fetched, and
# served straight through (ADR-003 §2-6): nothing is stored, and the edge cache
# treats it like any other artwork request.

APIC_IDS = (b"APIC", b"PIC")
MAX_ARTWORK_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class Artwork:
    data: bytes
    mime: str


def parse_artwork(data: bytes) -> Artwork | None:
    """The first embedded picture in an ID3v2 tag, or None.

    Tolerant by design: a truncated or odd frame yields None rather than an
    exception, exactly like the text frames above.
    """
    if len(data) < 10 or data[:3] != b"ID3":
        return None
    major = data[3]
    if major not in (2, 3, 4):
        return None
    tag_size = _syncsafe(data[6:10])
    position = 10
    end = min(len(data), 10 + tag_size)
    id_size, header_size = (3, 6) if major == 2 else (4, 10)

    while position + header_size <= end:
        frame_id = data[position : position + id_size]
        if not frame_id.strip(b"\x00"):
            break
        raw_size = data[position + id_size : position + id_size + (3 if major == 2 else 4)]
        size = _plain_size(raw_size) if major != 4 else _syncsafe(raw_size)
        if size <= 0 or size > MAX_ARTWORK_BYTES:
            break
        body_start = position + header_size
        body = data[body_start : body_start + size]
        if len(body) < size:
            break
        if frame_id in APIC_IDS:
            return _artwork_frame(body, major)
        position = body_start + size
    return None


def _artwork_frame(body: bytes, major: int) -> Artwork | None:
    """APIC body: encoding, mime (or 3-char type in v2.2), picture type, description."""
    if len(body) < 4:
        return None
    encoding, rest = body[0], body[1:]
    if major == 2:
        mime = {b"PNG": "image/png"}.get(rest[:3], "image/jpeg")
        rest = rest[3:]
    else:
        mime_end = rest.find(b"\x00")
        if mime_end < 0:
            return None
        mime = rest[:mime_end].decode("latin-1", "ignore") or "image/jpeg"
        if "/" not in mime:  # some taggers write "JPG" instead of "image/jpeg"
            mime = f"image/{mime.lower().replace('jpg', 'jpeg')}"
        rest = rest[mime_end + 1 :]
    if not rest:
        return None
    rest = rest[1:]  # picture type byte
    # The description is terminated by one NUL, or two for the UTF-16 encodings.
    terminator = b"\x00\x00" if encoding in (1, 2) else b"\x00"
    cut = rest.find(terminator)
    if cut < 0:
        return None
    picture = rest[cut + len(terminator) :]
    return Artwork(data=picture, mime=mime) if picture else None
