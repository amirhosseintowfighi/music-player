"""Persian/Latin text normalisation.

Two outputs:
- ``normalize_key``: aggressive, for search/dedup keys. Never shown to users.
- ``clean_display``: removes channel advertising, emoji and bitrate noise from titles
  but keeps the original letters, case and half-spaces.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable


def _char_class(*ranges: tuple[int, int]) -> str:
    """Regex character class from code-point ranges.

    Keeps invisible/bidi characters out of the source file.
    """
    return (
        "["
        + "".join(
            re.escape(chr(a)) + ("-" + re.escape(chr(b)) if b != a else "") for a, b in ranges
        )
        + "]"
    )


_LETTERS = {
    "ي": "ی", "ى": "ی", "ئ": "ی", "ك": "ک", "ة": "ه", "ۀ": "ه", "ؤ": "و",
    "إ": "ا", "أ": "ا", "ٱ": "ا",
}  # fmt: skip
_DIGITS = {chr(0x0660 + i): str(i) for i in range(10)} | {
    chr(0x06F0 + i): str(i) for i in range(10)
}
_DISPLAY_TABLE = str.maketrans(_LETTERS | _DIGITS)
_KEY_TABLE = str.maketrans(_LETTERS | _DIGITS | {"آ": "ا"})

_DIACRITICS = re.compile(
    _char_class((0x064B, 0x065F), (0x0670, 0x0670), (0x06D6, 0x06ED), (0x0640, 0x0640))
)
_INVISIBLE = re.compile(
    _char_class(
        (0x200B, 0x200F), (0x202A, 0x202E), (0x2066, 0x2069), (0xFEFF, 0xFEFF), (0x00AD, 0x00AD)
    )
)
_EMOJI = re.compile(
    _char_class(
        (0x1F000, 0x1FAFF),  # emoji, symbols & pictographs
        (0x2190, 0x21FF),
        (0x2300, 0x23FF),
        (0x2460, 0x24FF),  # arrows, technical, enclosed
        (0x2500, 0x27BF),
        (0x2B00, 0x2BFF),  # box drawing, dingbats, misc symbols
        (0x3030, 0x3030),
        (0x303D, 0x303D),
        (0x3297, 0x3297),
        (0x3299, 0x3299),
        (0xFE0F, 0xFE0F),
        (0x20E3, 0x20E3),  # variation selector, keycap
        (0xE0020, 0xE007F),  # tag sequences
    )
    + "+"
)
_NON_WORD = re.compile(r"[\W_]+")
_SPACES = re.compile(r"\s+")

_AD_WORDS = (
    "join", "channel", "official", "exclusive", "download", "free", "new", "kbps", "mp3",
    "320", "128", "192", "256", "hq", "high quality", "subscribe", "telegram", "music",
    "عضویت", "عضو", "کانال", "اختصاصی", "دانلود", "رایگان", "جدید", "کیفیت", "آهنگ",
    "تلگرام", "بپیوندید", "لینک",
)  # fmt: skip
_AD_RE = re.compile(
    r"(?<!\w)(?:" + "|".join(re.escape(w) for w in _AD_WORDS) + r")(?!\w)", re.IGNORECASE
)

_URL = re.compile(r"(?:https?://|www\.)\S+|\b(?:t|telegram)\.me/\S+", re.IGNORECASE)
_MENTION = re.compile(r"@[A-Za-z][A-Za-z0-9_]{3,}")
_HASHTAG = re.compile(r"#\S+")
_BRACKETED = re.compile(r"[\[\(\{【«]([^\[\]\(\)\{\}【】«»]*)[\]\)\}】»]")
_PIPE_TAIL = re.compile(r"\s*[|｜¦]\s*([^|｜¦]*)$")
_BITRATE = re.compile(
    r"\b(?:320|256|192|128|96)\s*(?:kbps|kb|k)?\b|\bmp3\b|\bv0\b|\bflac\b", re.IGNORECASE
)
# "01 - ", "03. ", "1) " at the very start: album track numbering, never an artist.
_TRACK_NUMBER = re.compile(r"^\s*\d{1,3}\s*[-–—.)_]\s*(?=\S)")
# A year standing alone between separators, or parenthesised at the end.
_LONE_YEAR = re.compile(
    r"\s*[-–—|]\s*(?:19|20)\d{2}\s*(?=[-–—|])|\s*\((?:19|20)\d{2}\)"
    r"|^\s*(?:19|20)\d{2}\s*(?=[-–—|])"
)
# A separator people draw instead of typing one: "معین ـ شب بارونی", "★ A ★ B ★".
_DRAWN_SEPARATOR = re.compile(r"(?<=\S)\s+[ـ★☆♪♫♬✩✦✧•·]+\s+(?=\S)")
# Praise that sits between "آهنگ" and the artist's name and means nothing.
_FILLER = re.compile(
    r"(?:بسیار\s+)?(?:زیبای|قشنگ|فوق[\s‌]*العاده|شنیدنی|خاطره[\s‌]*انگیز)\s+"
    r"|\s*با\s+کیفیت\s*",
)
# Decorations people wrap titles in.
_DECORATION = re.compile(r"[★☆♪♫♬✩✦✧❤♥•·]+")
# Version labels that are not part of the song's name.
_VERSION = re.compile(
    r"\s*[\(\[]\s*(?:original\s+mix|radio\s+edit|official\s+(?:video|audio|music\s+video)"
    r"|lyrics?|audio|video|remaster(?:ed)?(?:\s+\d{4})?)\s*[\)\]]",
    re.IGNORECASE,
)
_NOISE_WORDS = re.compile(
    r"\b(?:exclusive|premiere|official\s+(?:audio|music|track)|new\s+song|full\s+version"
    r"|free\s+download|out\s+now)\b"
    r"|^\s*(?:new|exclusive|premiere)\s*(?=[|:\-–—])"
    r"|(?<!\S)(?:اختصاصی|آهنگ\s+جدید|نسخه\s+کامل)(?!\S)"
    # Arabic channels label their posts the way Persian ones do. Written in the
    # Persian letters the normaliser has already folded ي→ی and ة→ه into, because by
    # the time this runs there is no Arabic spelling left to match.
    r"|(?<!\S)(?:حصریا|حصری|اغنیه\s+جدیده|أغنیه\s+جدیده)(?!\S)",
    re.IGNORECASE,
)
# Video-quality labels, the one thing _BITRATE does not already cover. Only inside
# brackets: "HD" alone could be part of a name, "(HD)" never is.
_QUALITY = re.compile(r"\s*[\(\[]\s*(?:full\s*hd|hd|4k|hq)\s*[\)\]]", re.IGNORECASE)
# What a stripped bitrate leaves behind: "Song [320kbps]" → "Song [ ]".
_EMPTY_BRACKETS = re.compile(r"\s*[\(\[]\s*[\)\]]")
_DOWNLOAD_PREFIX = re.compile(
    r"^\s*(?:دانلود|download|تحمیل|تنزیل)\s+"
    r"(?:(?:آهنگ|اهنگ|song|music|اغنیه|أغنیه)\s+)?(?:(?:جدید|new|جدیده)\s+)?",
    re.IGNORECASE,
)
_EDGE_PUNCT = re.compile(r"^[\s\-–—_|:.,،؛]+|[\s\-–—_|:.,،؛]+$")
# What is left when a title was only boilerplate; keep the original instead.
_GENERIC_REMAINS = {"جدید", "new", "song", "اهنگ", "موزیک", "music"}
ZWNJ = "\N{ZERO WIDTH NON-JOINER}"
_UNDERSCORES = re.compile(r"_+")


def normalize_key(text: str | None) -> str:
    if not text:
        return ""
    t = unicodedata.normalize("NFKC", text).translate(_KEY_TABLE)
    t = _DIACRITICS.sub("", t)
    t = _INVISIBLE.sub(" ", t)
    t = _EMOJI.sub(" ", t)
    t = _NON_WORD.sub(" ", t.casefold())
    return _SPACES.sub(" ", t).strip()


def _is_advert(segment: str, channel_keys: set[str]) -> bool:
    if not segment.strip():
        return True
    if _AD_RE.search(segment) or _MENTION.search(segment) or _URL.search(segment):
        return True
    return bool(channel_keys) and normalize_key(segment) in channel_keys


def clean_display(text: str | None, channel_names: Iterable[str | None] = ()) -> str:
    """Strip advertising from a title/performer.

    ``channel_names`` (username, title) lets us drop "| Channel Name" suffixes even
    when they contain no advertising keyword. If stripping would leave fewer than three
    characters, the lightly-fixed original is returned instead.
    """
    if not text:
        return ""
    base = unicodedata.normalize("NFC", text).translate(_DISPLAY_TABLE)
    # A drawn separator is a separator: turn it into one before it is stripped as a
    # decoration (the tatweel would otherwise vanish with the diacritics below).
    base = _DRAWN_SEPARATOR.sub(" - ", base)
    base = _DIACRITICS.sub("", base).replace(ZWNJ, "\x00")  # protect ZWNJ
    base = _INVISIBLE.sub("", base).replace("\x00", ZWNJ)
    base = _SPACES.sub(" ", base).strip()
    keys = {normalize_key(n.lstrip("@")) for n in channel_names if n}
    keys.discard("")

    t = _URL.sub(" ", base)
    t = _MENTION.sub(" ", t)
    t = _HASHTAG.sub(" ", t)
    t = _EMOJI.sub(" ", t)
    t = _BRACKETED.sub(lambda m: " " if _is_advert(m.group(1), keys) else m.group(0), t)
    while (m := _PIPE_TAIL.search(t)) and _is_advert(m.group(1), keys):
        t = t[: m.start()]
    t = _VERSION.sub(" ", t)
    t = _BITRATE.sub(" ", t)
    t = _NOISE_WORDS.sub(" ", t)
    t = _QUALITY.sub(" ", t)
    t = _EMPTY_BRACKETS.sub(" ", t)
    t = _DOWNLOAD_PREFIX.sub("", t)
    t = _FILLER.sub(" ", t)
    t = _TRACK_NUMBER.sub("", t)
    t = _LONE_YEAR.sub(" ", t)
    t = _DECORATION.sub(" ", t)
    t = _UNDERSCORES.sub(" ", t)
    t = _EDGE_PUNCT.sub("", _SPACES.sub(" ", t))
    # A trailing segment that is exactly the channel name ("Song - MyChannel").
    for sep in (" - ", " – ", " — "):
        head, found, tail = t.rpartition(sep)
        if found and keys and normalize_key(tail) in keys:
            t = head
    t = t.strip()
    # Stripping the noise out of "new song" leaves "song", which is not a title.
    if len(t) < 3 or normalize_key(t) in _GENERIC_REMAINS:
        fallback = _EDGE_PUNCT.sub("", _EMOJI.sub(" ", base)).strip()
        return _SPACES.sub(" ", fallback)
    return t
