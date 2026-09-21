"""ID3 parsing, against bytes built here rather than sample files.

Real-world tags are messy: three versions of the spec, four text encodings, numeric
genre references and files that are truncated mid-tag. The parser must return what it
can and never raise, because it runs on bytes fetched from strangers' channels.
"""

from __future__ import annotations

import struct

from tmusic_indexer import id3


def syncsafe(size: int) -> bytes:
    return bytes(((size >> shift) & 0x7F) for shift in (21, 14, 7, 0))


def frame_v3(frame_id: bytes, text: str, encoding: int = 3) -> bytes:
    body = bytes([encoding]) + text.encode("utf-8" if encoding == 3 else "latin-1") + b"\x00"
    return frame_id + struct.pack(">I", len(body)) + b"\x00\x00" + body


def frame_v4(frame_id: bytes, text: str) -> bytes:
    body = bytes([3]) + text.encode("utf-8") + b"\x00"
    return frame_id + syncsafe(len(body)) + b"\x00\x00" + body


def tag_v2(frames: bytes, major: int = 3, flags: int = 0) -> bytes:
    return b"ID3" + bytes([major, 0, flags]) + syncsafe(len(frames)) + frames


def tag_v1(
    title: str = "", artist: str = "", album: str = "", year: str = "", genre: int = 255
) -> bytes:
    def pad(value: str, length: int) -> bytes:
        return value.encode("latin-1")[:length].ljust(length, b"\x00")

    return (
        b"TAG"
        + pad(title, 30)
        + pad(artist, 30)
        + pad(album, 30)
        + pad(year, 4)
        + pad("", 30)
        + bytes([genre])
    )


def test_reads_an_id3v23_tag() -> None:
    data = tag_v2(
        frame_v3(b"TIT2", "شب بارونی")
        + frame_v3(b"TPE1", "معین")
        + frame_v3(b"TALB", "بهترین‌ها")
        + frame_v3(b"TYER", "1998")
        + frame_v3(b"TCON", "Pop")
    )
    tags = id3.parse(data)
    assert tags.title == "شب بارونی"
    assert tags.artist == "معین"
    assert tags.album == "بهترین‌ها"
    assert tags.year == 1998
    assert tags.genre == "Pop"


def test_reads_an_id3v24_tag_with_syncsafe_frame_sizes() -> None:
    data = tag_v2(frame_v4(b"TALB", "Album 24") + frame_v4(b"TDRC", "2011-05-02"), major=4)
    tags = id3.parse(data)
    assert tags.album == "Album 24"
    assert tags.year == 2011  # a full date still yields the year


def test_reads_the_old_three_letter_frames() -> None:
    body = bytes([0]) + "Old Album".encode("latin-1") + b"\x00"
    frames = b"TAL" + len(body).to_bytes(3, "big") + body
    tags = id3.parse(tag_v2(frames, major=2))
    assert tags.album == "Old Album"


def test_handles_every_text_encoding() -> None:
    utf16 = b"\x01" + "Ünïcode".encode("utf-16") + b"\x00\x00"
    frames = b"TALB" + struct.pack(">I", len(utf16)) + b"\x00\x00" + utf16
    assert id3.parse(tag_v2(frames)).album == "Ünïcode"

    latin = frame_v3(b"TALB", "Cafe", encoding=0)
    assert id3.parse(tag_v2(latin)).album == "Cafe"


def test_a_numeric_genre_reference_is_resolved() -> None:
    assert id3.parse(tag_v2(frame_v3(b"TCON", "(17)"))).genre == "Rock"
    assert id3.parse(tag_v2(frame_v3(b"TCON", "(17)Hard Rock"))).genre == "Hard Rock"


def test_an_implausible_year_is_dropped() -> None:
    assert id3.parse(tag_v2(frame_v3(b"TYER", "0000"))).year is None
    assert id3.parse(tag_v2(frame_v3(b"TYER", "not a year"))).year is None


def test_a_truncated_tag_returns_what_was_readable() -> None:
    full = tag_v2(frame_v3(b"TALB", "First Album") + frame_v3(b"TCON", "Jazz"))
    cut = full[: len(full) - 12]  # chop the middle of the last frame
    tags = id3.parse(cut)
    assert tags.album == "First Album"
    assert tags.genre is None  # never guessed from half a frame


def test_padding_ends_the_scan() -> None:
    frames = frame_v3(b"TALB", "Album") + b"\x00" * 200
    assert id3.parse(tag_v2(frames)).album == "Album"


def test_an_extended_header_is_skipped() -> None:
    extended = struct.pack(">I", 6) + b"\x00" * 6
    data = tag_v2(extended + frame_v3(b"TALB", "With Extended"), flags=0x40)
    assert id3.parse(data).album == "With Extended"


def test_falls_back_to_id3v1_at_the_end_of_the_file() -> None:
    tags = id3.parse(
        b"\xff\xfb" + b"\x00" * 100, tag_v1(title="Old", album="Tape", year="1985", genre=17)
    )
    assert tags.title == "Old"
    assert tags.album == "Tape"
    assert tags.year == 1985
    assert tags.genre == "Rock"


def test_id3v2_wins_over_id3v1() -> None:
    tags = id3.parse(tag_v2(frame_v3(b"TALB", "New")), tag_v1(album="Old"))
    assert tags.album == "New"


def test_files_without_tags_are_simply_empty() -> None:
    assert id3.parse(b"\xff\xfb\x90\x00" * 50).empty
    assert id3.parse(b"").empty
    assert id3.parse(b"ID3").empty  # a header that stops after three bytes


def test_garbage_never_raises() -> None:
    for payload in (
        b"ID3\x03\x00\x00" + b"\xff" * 20,
        b"ID3\x09\x00\x00" + syncsafe(10) + b"junk",
        b"ID3\x04\x00\x00" + syncsafe(100) + b"TALB" + syncsafe(10**6),
        bytes(range(256)),
    ):
        assert isinstance(id3.parse(payload), id3.Tags)


def test_an_absurd_frame_size_is_refused() -> None:
    """A frame claiming megabytes of text is corrupt, not a very long album name."""
    huge = b"TALB" + struct.pack(">I", 10_000_000) + b"\x00\x00" + b"x" * 10
    assert id3.parse(tag_v2(huge)).album is None


# ── embedded artwork (ADR-003 phase 12) ───────────────────────────────────────


def apic_tag(picture: bytes, mime: bytes = b"image/png", major: int = 3) -> bytes:
    """A minimal ID3v2 tag carrying one APIC frame."""
    nul = bytes([0])
    body = nul + mime + nul + bytes([3]) + b"cover" + nul + picture
    frame = b"APIC" + len(body).to_bytes(4, "big") + bytes(2) + body
    return b"ID3" + bytes([major, 0, 0]) + syncsafe(len(frame)) + frame


def test_an_embedded_cover_is_extracted_with_its_mime() -> None:
    art = id3.parse_artwork(apic_tag(b"\x89PNGdata"))
    assert art is not None
    assert art.data == b"\x89PNGdata"
    assert art.mime == "image/png"


def test_a_sloppy_mime_is_normalised() -> None:
    art = id3.parse_artwork(apic_tag(b"jpegbytes", mime=b"JPG"))
    assert art is not None and art.mime == "image/jpeg"


def test_no_artwork_is_not_an_error() -> None:
    assert id3.parse_artwork(b"") is None
    assert id3.parse_artwork(b"not a tag at all") is None
    assert id3.parse_artwork(tag_v2(frame_v3(b"TIT2", "just text"))) is None


def test_a_truncated_picture_frame_yields_nothing_rather_than_garbage() -> None:
    tag = apic_tag(b"x" * 4000)
    assert id3.parse_artwork(tag[:200]) is None
