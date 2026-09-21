"""Work out (title, artists) from messy Telegram audio metadata.

Priority: the audio's own ``performer`` tag → "A - B" in the title → "X by Y" /
"Y به نام X" phrases → the file name → labelled lines in the caption. Every result carries
a confidence (0-100); anything under 60 lands in the admin review queue.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import PurePosixPath

from app.domain.text.normalizer import clean_display, normalize_key

REVIEW_THRESHOLD = 60

_SEPARATOR = re.compile(r"\s+[-–—|]\s+|\s*[–—]\s*|\s-\s?|\s?-\s")
_FEAT_IN_TITLE = re.compile(
    r"\s*[\(\[]\s*(?:ft|feat|featuring|with|با\s+حضور)\.?\s+([^\)\]]+)[\)\]]"
    # A trailing "ft. X" stops at the artist/title separator: in
    # "Sogand ft. Sirvan Khosravi - Havaye To" only the middle name is the guest.
    r"|\s+(?:ft|feat|featuring|با\s+حضور)\.?\s+([^-–—|]+?)(?=\s*[-–—|]|$)",
    re.IGNORECASE,
)
_FEAT_SPLIT = re.compile(r"\s+(?:ft|feat|featuring)\.?\s+|\s*\((?:ft|feat)\.?\s+", re.IGNORECASE)
_ARTIST_SPLIT = re.compile(r"\s*(?:,|،|&|\s+x\s+|\s+and\s+|\s+و\s+)\s*", re.IGNORECASE)
_BY = re.compile(r"^(?P<title>.+?)\s+(?:by|از)\s+(?P<artist>.+)$", re.IGNORECASE)
_NAMED = re.compile(r"^(?:(?:آهنگ|اهنگ)\s+)?(?P<artist>.+?)\s+به\s+نام\s+(?P<title>.+)$")
_SONG_PREFIX = re.compile(r"^(?:آهنگ|اهنگ)\s+")
_GENERIC_TITLES = {"جدید", "new", "song", "موزیک"}
_CAPTION_ARTIST = re.compile(
    r"^\W*(?:artist|singer|performer|خواننده|هنرمند)\s*[:：]\s*(.+)$", re.IGNORECASE | re.MULTILINE
)
_CAPTION_TITLE = re.compile(
    r"^\W*(?:title|track|song|نام\s+آهنگ|آهنگ|ترک)\s*[:：]\s*(.+)$", re.IGNORECASE | re.MULTILINE
)
_JUNK_PERFORMERS = {
    "unknown", "unknown artist", "unknown_artist", "various artists", "va", "artist",
    "ناشناس", "نامشخص", "خواننده",
    # Taggers and channels that write their own name into the performer field.
    "telegram", "t me", "music", "musics", "song", "audio", "channel", "admin",
    "موزیک", "اهنگ", "آهنگ", "کانال",
}  # fmt: skip

KnownArtist = Callable[[str], bool]


@dataclass(frozen=True, slots=True)
class ParsedMeta:
    title: str
    artists: tuple[str, ...]
    features: tuple[str, ...]
    confidence: int

    @property
    def needs_review(self) -> bool:
        return self.confidence < REVIEW_THRESHOLD


def _dedupe(names: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for n in names:
        key = normalize_key(n)
        if key and key not in seen:
            seen.add(key)
            out.append(n.strip())
    return tuple(out)


def split_artists(raw: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """'A & B feat. C, D' → (('A', 'B'), ('C', 'D'))."""
    parts = _FEAT_SPLIT.split(raw, maxsplit=1)
    main = parts[0]
    feat = parts[1].rstrip(")] ") if len(parts) > 1 else ""
    primary = _dedupe(p for p in _ARTIST_SPLIT.split(main) if p.strip())
    features = _dedupe(p for p in _ARTIST_SPLIT.split(feat) if p.strip())
    return primary, features


def _extract_title_features(title: str) -> tuple[str, tuple[str, ...]]:
    m = _FEAT_IN_TITLE.search(title)
    if not m:
        return title, ()
    raw = m.group(1) or m.group(2) or ""
    return (title[: m.start()] + title[m.end() :]).strip(), split_artists(raw)[0]


def _split_pair(text: str) -> tuple[str, str] | None:
    m = _SEPARATOR.search(text)
    if not m:
        return None
    left, right = text[: m.start()].strip(), text[m.end() :].strip()
    return (left, right) if left and right else None


def candidate_artist_names(title: str | None, file_name: str | None = None) -> set[str]:
    """Keys the parser may ask ``known_artist`` about; lets callers batch one DB lookup."""
    out: set[str] = set()
    for text in (clean_display(title), _file_stem(file_name)):
        pair = _split_pair(text) if text else None
        if pair:
            for side in pair:
                out.update(normalize_key(a) for a in split_artists(side)[0])
    out.discard("")
    return out


_FILE_EXT = re.compile(r"\.(?:mp3|m4a|aac|flac|ogg|opus|wav|wma)$", re.IGNORECASE)
_WORD_SEPARATORS = re.compile(r"[_]+")


def _as_words(title: str | None) -> str:
    """A title that is really a file name becomes a title.

    Channels post `Googoosh_Pol_320.mp3` and `hayedeh-gole-sangam.mp3` constantly. The
    extension is noise, underscores are spaces, and in a name with no spaces at all a
    hyphen is the separator rather than part of a word.
    """
    if not title:
        return ""
    text = _FILE_EXT.sub("", title.strip())
    if " " in text:
        return text  # a written title, not a file name: leave it exactly as posted
    for separator in ("-", "_"):
        if separator not in text:
            continue
        # "hayedeh-gole-sangam" → "hayedeh - gole sangam": the first separator splits
        # artist from title, the rest are word breaks inside the title.
        head, _, tail = text.partition(separator)
        words = _WORD_SEPARATORS.sub(" ", tail.replace("-", " ")).strip()
        if not head or not words or words.replace(" ", "").isdigit():
            break  # "audio_2026_09_18" is a file name, not an artist and a song
        return f"{head} - {words}"
    return _WORD_SEPARATORS.sub(" ", text)


def _file_stem(file_name: str | None) -> str:
    if not file_name:
        return ""
    stem = PurePosixPath(file_name.replace("\\", "/")).stem
    return clean_display(stem.replace("_", " "))


def _is_junk_performer(performer: str, channel_keys: set[str]) -> bool:
    key = normalize_key(performer)
    return not key or key in _JUNK_PERFORMERS or key in channel_keys


_BRACKETED_TAIL = re.compile(r"^(?P<title>.+?)\s*[\(\[]\s*(?P<artist>[^\)\]]+?)\s*[\)\]]\s*$")


def _bracketed_artist(title: str, known: KnownArtist) -> tuple[str, str] | None:
    """ "Nafas (Moein)" → ("Moein", "Nafas"), but only when the name is one we know.

    Without that check every "(Remix)" and "(Live)" would become an artist, which is
    exactly the kind of invented metadata that is worse than none.
    """
    m = _BRACKETED_TAIL.match(title)
    if not m:
        return None
    artist, rest = m.group("artist").strip(), m.group("title").strip()
    if not rest or not any(known(normalize_key(a)) for a in split_artists(artist)[0]):
        return None
    return artist, rest


def _from_pair(pair: tuple[str, str], known: KnownArtist) -> tuple[str, str, bool]:
    """Return (artist, title, sure). Left side wins ties (dominant Persian convention)."""
    left, right = pair
    left_known = any(known(normalize_key(a)) for a in split_artists(left)[0])
    right_known = any(known(normalize_key(a)) for a in split_artists(right)[0])
    if right_known and not left_known:
        return right, left, True
    return left, right, left_known


def parse_track_meta(
    title: str | None,
    performer: str | None,
    file_name: str | None = None,
    caption: str | None = None,
    channel_names: Iterable[str | None] = (),
    known_artist: KnownArtist | None = None,
) -> ParsedMeta:
    names = list(channel_names)
    channel_keys = {normalize_key(n.lstrip("@")) for n in names if n}
    known: KnownArtist = known_artist or (lambda _key: False)

    cleaned = clean_display(_as_words(title), names)
    without_prefix = _SONG_PREFIX.sub("", cleaned)
    # "آهنگ جدید" is not a song called "جدید": dropping the prefix has to leave
    # something that could actually be a name.
    clean_title = cleaned if normalize_key(without_prefix) in _GENERIC_TITLES else without_prefix
    clean_perf = clean_display(performer, names)
    clean_title, title_feats = _extract_title_features(clean_title)
    artist_raw = ""
    confidence = 0

    if clean_perf and not _is_junk_performer(clean_perf, channel_keys):
        artist_raw = clean_perf
        pair = _split_pair(clean_title) if clean_title else None
        if pair:
            # Title often repeats the artist: "Moein - Shabe Barooni" with performer Moein.
            perf_key = normalize_key(clean_perf)
            if normalize_key(pair[0]) == perf_key:
                clean_title = pair[1]
            elif normalize_key(pair[1]) == perf_key:
                clean_title = pair[0]
        confidence = 95 if clean_title else 70
    elif clean_title and (pair := _split_pair(clean_title)):
        artist_raw, clean_title, sure = _from_pair(pair, known)
        confidence = 85 if sure else 70
    elif clean_title and (m := _NAMED.match(clean_title) or _BY.match(clean_title)):
        artist_raw, clean_title = m.group("artist").strip(), m.group("title").strip()
        confidence = 75
    elif clean_title and (bracketed := _bracketed_artist(clean_title, known)):
        artist_raw, clean_title = bracketed
        confidence = 80

    if not clean_title or not artist_raw:
        stem = _file_stem(file_name)
        pair = _split_pair(stem) if stem else None
        if pair and not artist_raw:
            artist_raw, stem_title, sure = _from_pair(pair, known)
            clean_title = clean_title or stem_title
            confidence = 65 if sure else 55
        elif not clean_title and stem:
            clean_title = stem
            confidence = max(confidence - 15, 40) if artist_raw else 40

    if not artist_raw and caption:
        m = _CAPTION_ARTIST.search(caption)
        if m:
            artist_raw = clean_display(m.group(1), names)
            confidence = 60
        if not clean_title and (mt := _CAPTION_TITLE.search(caption)):
            clean_title = clean_display(mt.group(1), names)

    if not artist_raw:
        confidence = min(confidence or 50, 50) if clean_title else 20

    clean_title, more_feats = _extract_title_features(clean_title)
    primary, feats = split_artists(artist_raw) if artist_raw else ((), ())
    features = _dedupe((*feats, *title_feats, *more_feats))
    primary_keys = {normalize_key(p) for p in primary}
    features = tuple(f for f in features if normalize_key(f) not in primary_keys)
    return ParsedMeta(
        title=clean_title.strip(), artists=primary, features=features, confidence=confidence
    )
