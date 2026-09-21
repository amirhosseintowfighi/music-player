from __future__ import annotations

import re
from typing import Literal

Language = Literal["fa", "en", "ar", "tr", "ku", "other"]

_ARABIC_SCRIPT = re.compile(f"[{chr(0x0600)}-{chr(0x06FF)}]")
_PERSIAN_ONLY = re.compile("[پچژگی]")
_ARABIC_ONLY = re.compile("[ةيىكأإ]")
_KURDISH_ONLY = re.compile("[ڕڵۆێە]")
_TURKISH_ONLY = re.compile("[ğışĞİŞ]")
_LATIN = re.compile("[A-Za-z]")


def detect_language(*texts: str | None) -> Language | None:
    """Cheap script-based guess from title/artist. None when there is no signal."""
    text = " ".join(t for t in texts if t)
    if not text.strip():
        return None
    if _ARABIC_SCRIPT.search(text):
        if _KURDISH_ONLY.search(text):
            return "ku"
        if _PERSIAN_ONLY.search(text):
            return "fa"
        return "ar" if _ARABIC_ONLY.search(text) else "fa"
    if _TURKISH_ONLY.search(text):
        return "tr"
    if _LATIN.search(text):
        return "en"
    return "other"
