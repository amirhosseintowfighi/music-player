"""A measuring stick for the metadata parser (ADR-003 phase 13).

Sixty titles in the shapes Persian music channels actually post: the file name left
in, the channel's own handle glued on, bitrate and "exclusive" noise, decorative
characters, and artist/title in either order and either script. Each row says what a
human would read off the post.

This is a *baseline*, not a unit test: the point is to measure, change the parser, and
measure again. Rows are grouped by the problem they represent so a regression points
at a pattern rather than a mystery.

Format: (title, performer, channel, expected_artist, expected_title).
``expected_artist`` of "" means there is honestly no artist in the text — the app
falls back to the channel name for display, and the parser should not invent one.
"""

from __future__ import annotations

from app.domain.text.normalizer import normalize_key

CHANNEL = "Musicirani_Official"

CASES: list[tuple[str | None, str | None, str, str, str]] = [
    # ── the easy half: Telegram's own performer tag is right ──────────────────
    ("شب بارونی", "معین", CHANNEL, "معین", "شب بارونی"),
    ("Shabe Barooni", "Moein", CHANNEL, "Moein", "Shabe Barooni"),
    ("پل", "گوگوش", CHANNEL, "گوگوش", "پل"),
    ("معین - شب بارونی", "معین", CHANNEL, "معین", "شب بارونی"),
    ("Moein - Shabe Barooni", "Moein", CHANNEL, "Moein", "Shabe Barooni"),
    ("گل سنگم", "هایده", "PersianOldies", "هایده", "گل سنگم"),
    ("Soltane Ghalbha", "Aref", "PersianOldies", "Aref", "Soltane Ghalbha"),
    ("دوست دارم", "شادمهر عقیلی", CHANNEL, "شادمهر عقیلی", "دوست دارم"),
    ("Deltangi", "Sirvan Khosravi", "SirvanMusic", "Sirvan Khosravi", "Deltangi"),
    ("نفس", "معین", CHANNEL, "معین", "نفس"),
    # ── file names that were never cleaned up ─────────────────────────────────
    ("Moein - Nafas.mp3", None, CHANNEL, "Moein", "Nafas"),
    ("Googoosh_Pol_320.mp3", None, CHANNEL, "Googoosh", "Pol"),
    ("hayedeh-gole-sangam.mp3", None, "PersianOldies", "hayedeh", "gole sangam"),
    ("01 - Dariush - Cheshme Man.mp3", None, CHANNEL, "Dariush", "Cheshme Man"),
    ("ebi_khalij_128kbps.mp3", None, CHANNEL, "ebi", "khalij"),
    # ── the channel's own branding glued onto the title ───────────────────────
    ("شب بارونی - معین @Musicirani_Official", None, CHANNEL, "معین", "شب بارونی"),
    (f"Moein - Nafas | {CHANNEL}", None, CHANNEL, "Moein", "Nafas"),
    ("@PersianOldies هایده - گل سنگم", None, "PersianOldies", "هایده", "گل سنگم"),
    ("Googoosh - Pol (Musicirani_Official)", None, CHANNEL, "Googoosh", "Pol"),
    ("🎵 معین - نفس 🎵", None, CHANNEL, "معین", "نفس"),
    # ── quality and release noise ─────────────────────────────────────────────
    ("Sirvan Khosravi - Deltangi [320]", None, "SirvanMusic", "Sirvan Khosravi", "Deltangi"),
    ("Exclusive: Shadmehr Aghili - Taghdir", None, CHANNEL, "Shadmehr Aghili", "Taghdir"),
    ("New | Reza Sadeghi - Shabo Rooz", None, CHANNEL, "Reza Sadeghi", "Shabo Rooz"),
    (
        "دانلود آهنگ جدید محسن یگانه به نام بهت قول میدم",
        None,
        CHANNEL,
        "محسن یگانه",
        "بهت قول میدم",
    ),
    ("آهنگ جدید سیروان خسروی - دلتنگی", None, CHANNEL, "سیروان خسروی", "دلتنگی"),
    # ── features, in every spelling people use ────────────────────────────────
    ("Sogand ft. Sirvan Khosravi - Havaye To", None, CHANNEL, "Sogand", "Havaye To"),
    ("Moein feat Hayedeh - Doostet Daram", None, CHANNEL, "Moein", "Doostet Daram"),
    ("محسن چاوشی (با حضور سینا سرلک) - کجایی", None, CHANNEL, "محسن چاوشی", "کجایی"),
    ("Tataloo & Hamid Sefat - Divar", None, CHANNEL, "Tataloo", "Divar"),
    ("امیر تتلو و حمید صفت - دیوار", None, CHANNEL, "امیر تتلو", "دیوار"),
    # ── the reversed order: title first, artist second ────────────────────────
    ("Shabe Barooni - Moein", None, CHANNEL, "Moein", "Shabe Barooni"),
    ("گل سنگم - هایده", None, "PersianOldies", "هایده", "گل سنگم"),
    ("Pol - Googoosh", None, CHANNEL, "Googoosh", "Pol"),
    # ── brackets and parentheses carrying the artist ──────────────────────────
    ("Nafas (Moein)", None, CHANNEL, "Moein", "Nafas"),
    ("دیوار [امیر تتلو]", None, CHANNEL, "امیر تتلو", "دیوار"),
    ("Havaye To (Original Mix)", "Sogand", CHANNEL, "Sogand", "Havaye To"),
    # A remix is a different recording: the label stays, or dedup would fold them.
    ("Deltangi (Remix)", "Sirvan Khosravi", "SirvanMusic", "Sirvan Khosravi", "Deltangi (Remix)"),
    # ── a performer tag that is really the channel, or junk ───────────────────
    ("معین - شب بارونی", CHANNEL, CHANNEL, "معین", "شب بارونی"),
    ("Googoosh - Pol", "@Musicirani_Official", CHANNEL, "Googoosh", "Pol"),
    ("Hayedeh - Gole Sangam", "Unknown Artist", "PersianOldies", "Hayedeh", "Gole Sangam"),
    ("Dariush - Cheshme Man", "Various Artists", CHANNEL, "Dariush", "Cheshme Man"),
    ("Ebi - Khalij", "Telegram", CHANNEL, "Ebi", "Khalij"),
    # ── Persian punctuation, ZWNJ and decorative separators ───────────────────
    ("معین ـ شب بارونی", None, CHANNEL, "معین", "شب بارونی"),
    ("هایده | گل سنگم", None, "PersianOldies", "هایده", "گل سنگم"),
    ("سیروان خسروی – دلتنگی", None, "SirvanMusic", "سیروان خسروی", "دلتنگی"),
    ("محسن‌ یگانه - بهت قول میدم", None, CHANNEL, "محسن یگانه", "بهت قول میدم"),
    ("★ Moein ★ Nafas ★", None, CHANNEL, "Moein", "Nafas"),
    # ── nothing to work with: the parser must not invent an artist ────────────
    ("آهنگ جدید", None, CHANNEL, "", "آهنگ جدید"),
    ("Track 07", None, CHANNEL, "", "Track 07"),
    ("بی کلام", None, CHANNEL, "", "بی کلام"),
    ("🎶🎶🎶", None, CHANNEL, "", ""),
    ("audio_2026_09_18.mp3", None, CHANNEL, "", "audio 2026 09 18"),
    # ── mixed script, the same artist written both ways ───────────────────────
    ("Moein - نفس", None, CHANNEL, "Moein", "نفس"),
    ("معین - Nafas", None, CHANNEL, "معین", "Nafas"),
    ("GOOGOOSH - POL", None, CHANNEL, "GOOGOOSH", "POL"),
    # ── album/track numbering and years ───────────────────────────────────────
    ("Dariush - 1978 - Cheshme Man", None, CHANNEL, "Dariush", "Cheshme Man"),
    ("03. Ebi - Khalij", None, CHANNEL, "Ebi", "Khalij"),
    ("Moein - Nafas (1996)", None, CHANNEL, "Moein", "Nafas"),
    # ── long, chatty captions in the title field ──────────────────────────────
    (
        "دانلود آهنگ بسیار زیبای معین به نام شب بارونی با کیفیت 320",
        None,
        CHANNEL,
        "معین",
        "شب بارونی",
    ),
    (
        "New Song 2026 | Sirvan Khosravi | Deltangi",
        None,
        "SirvanMusic",
        "Sirvan Khosravi",
        "Deltangi",
    ),
    ("🔥 EXCLUSIVE 🔥 Tataloo - Divar - 320kbps", None, CHANNEL, "Tataloo", "Divar"),
]

assert len(CASES) >= 50, "the brief asks for at least fifty real samples"

# Artists an established catalogue already knows, which is what lets the parser settle
# "Title - Artist" the right way round. Production passes the real table; here it is
# the artists of this corpus, normalised the same way the database normalises them.
_ARTISTS = [
    "معین",
    "Moein",
    "گوگوش",
    "Googoosh",
    "هایده",
    "Hayedeh",
    "hayedeh",
    "Aref",
    "شادمهر عقیلی",
    "Shadmehr Aghili",
    "Sirvan Khosravi",
    "سیروان خسروی",
    "Dariush",
    "Ebi",
    "ebi",
    "محسن یگانه",
    "Reza Sadeghi",
    "Sogand",
    "محسن چاوشی",
    "Tataloo",
    "امیر تتلو",
    "GOOGOOSH",
]
KNOWN_ARTIST_KEYS = {normalize_key(name) for name in _ARTISTS}
