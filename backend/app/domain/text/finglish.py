"""Finglish support.

Persian omits short vowels, so "معین" has no single correct romanisation (moein, moin,
mo'in). Instead of guessing vowels we compare *consonant skeletons*: both the Persian
text (after transliteration) and the Latin query are reduced to their consonants with
equivalent spellings folded together::

    skeleton(to_latin("معین")) == skeleton("moein") == "mn"
    skeleton(to_latin("گوگوش")) == skeleton("googoosh") == "ggs"

Skeletons are indexed as a separate low-weight search field (ADR-0006).
"""

from __future__ import annotations

import re

from app.domain.text.normalizer import normalize_key

_FA_TO_LATIN = {
    "ا": "a", "آ": "a", "ب": "b", "پ": "p", "ت": "t", "ث": "s", "ج": "j", "چ": "ch",
    "ح": "h", "خ": "kh", "د": "d", "ذ": "z", "ر": "r", "ز": "z", "ژ": "zh", "س": "s",
    "ش": "sh", "ص": "s", "ض": "z", "ط": "t", "ظ": "z", "ع": "", "غ": "gh", "ف": "f",
    "ق": "gh", "ک": "k", "گ": "g", "ل": "l", "م": "m", "ن": "n", "ه": "h", "ء": "",
}  # fmt: skip
_PERSIAN_CHARS = re.compile(f"[{chr(0x0600)}-{chr(0x06FF)}]")

# Applied in order on lowercase Latin text. Digraphs first, then single letters.
_FOLDS: tuple[tuple[str, str], ...] = (
    ("kh", "x"), ("sh", "s"), ("ch", "c"), ("zh", "j"), ("gh", "q"), ("ph", "f"),
    ("ck", "k"), ("q", "g"), ("c", "c"), ("w", "v"), ("th", "t"), ("dh", "d"),
)  # fmt: skip
_VOWELS = re.compile(r"[aeiouyvh']")
_REPEATS = re.compile(r"(.)\1+")
_NON_LATIN = re.compile(r"[^a-z0-9 ]+")


def _word_to_latin(word: str) -> str:
    out: list[str] = []
    for i, ch in enumerate(word):
        if ch == "و":
            prev_is_vowel = i == 0 or word[i - 1] in "اآویه"
            out.append("v" if prev_is_vowel else "o")
        elif ch == "ی":
            out.append("y" if i == 0 else "i")
        elif ch == "ه" and i == len(word) - 1 and i > 0:
            out.append("e")
        else:
            out.append(_FA_TO_LATIN.get(ch, ch))
    return "".join(out)


def to_latin(text: str) -> str:
    """Best-effort Persian → Latin. Latin input passes through normalised."""
    key = normalize_key(text)
    if not _PERSIAN_CHARS.search(key):
        return key
    return " ".join(_word_to_latin(w) for w in key.split())


def skeleton(text: str) -> str:
    """Consonant skeleton of each word. Works on Persian or Latin input."""
    latin = _NON_LATIN.sub(" ", to_latin(text))
    words: list[str] = []
    for word in latin.split():
        w = word
        for src, dst in _FOLDS:
            w = w.replace(src, dst)
        # h is kept only at the start of a word (hayedeh → hd, but mahasti → mst).
        head, rest = (w[0], w[1:]) if w and w[0] == "h" else ("", w)
        w = _REPEATS.sub(r"\1", head + _VOWELS.sub("", rest))
        if w:
            words.append(w)
    return " ".join(words)
