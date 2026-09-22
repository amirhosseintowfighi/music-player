import re
from pathlib import Path

import pytest

from app.domain.text.artist_parser import candidate_artist_names, parse_track_meta, split_artists
from app.domain.text.finglish import skeleton, to_latin
from app.domain.text.language import detect_language
from app.domain.text.normalizer import clean_display, normalize_key


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("علي", "علی"),
        ("كيوان", "کیوان"),
        ("م\N{ARABIC FATHA}عین", "معین"),
        ("آرش", "ارش"),
        ("می\N{ZERO WIDTH NON-JOINER}خواهم", "می خواهم"),
        ("ــسلامــ", "سلام"),
        ("۱۴۰۲ و ٣", "1402 و 3"),
        ("  Moein   🎵 SHAB  ", "moein shab"),
        ("Hello_World!!", "hello world"),
        ("ﻣﻌﯿﻦ", "معین"),  # Arabic presentation forms
        ("", ""),
        (None, ""),
    ],
)
def test_normalize_key(raw: str | None, expected: str) -> None:
    assert normalize_key(raw) == expected


@pytest.mark.parametrize(
    ("raw", "channels", "expected"),
    [
        ("Shabe Barooni @MusicChannel", (), "Shabe Barooni"),
        ("شب بارونی | کانال موزیک ما", (), "شب بارونی"),
        ("شب بارونی | Persian Hits", ("PersianHits", "Persian Hits"), "شب بارونی"),
        ("🎵 Delam Tang Shode 🎧 [320]", (), "Delam Tang Shode"),
        ("Song (Remix) [@chan]", (), "Song (Remix)"),
        ("Song (Live)", (), "Song (Live)"),
        ("Track t.me/somechannel", (), "Track"),
        ("دانلود آهنگ جدید معین به نام شب", (), "معین به نام شب"),
        ("نمی\N{ZERO WIDTH NON-JOINER}دانم 320 kbps", (), "نمی\N{ZERO WIDTH NON-JOINER}دانم"),
        ("Exclusive: Havaye To", (), "Havaye To"),
        ("Hello - Persian Hits", ("PersianHits", "Persian Hits"), "Hello"),
        ("@onlychannel", (), "@onlychannel"),  # fallback keeps something
        ("Ab", (), "Ab"),
        ("علي #جدید", (), "علی"),
    ],
)
def test_clean_display(raw: str, channels: tuple[str, ...], expected: str) -> None:
    assert clean_display(raw, channels) == expected


def test_split_artists() -> None:
    assert split_artists("Sirvan Khosravi ft. Hamed Homayoun") == (
        ("Sirvan Khosravi",),
        ("Hamed Homayoun",),
    )
    assert split_artists("A & B, C feat. D x E") == (("A", "B", "C"), ("D", "E"))
    assert split_artists("کامران، هومن") == (("کامران", "هومن"), ())
    assert split_artists("Macan Band") == (("Macan Band",), ())


def test_parse_prefers_performer_tag() -> None:
    meta = parse_track_meta("Moein - Shabe Barooni @chan", "Moein")
    assert meta.title == "Shabe Barooni"
    assert meta.artists == ("Moein",)
    assert meta.confidence == 95
    assert not meta.needs_review


def test_parse_splits_title_when_performer_is_junk() -> None:
    meta = parse_track_meta("Ebi - Khaneh", "Unknown Artist")
    assert (meta.artists, meta.title, meta.confidence) == (("Ebi",), "Khaneh", 70)


def test_parse_uses_known_artist_to_pick_side() -> None:
    known = {"moein"}
    meta = parse_track_meta("Shabe Barooni - Moein", None, known_artist=known.__contains__)
    assert (meta.artists, meta.title, meta.confidence) == (("Moein",), "Shabe Barooni", 85)


def test_parse_channel_name_performer_is_ignored() -> None:
    meta = parse_track_meta(
        "داریوش - نون و پنیر", "Persian Hits", channel_names=("PersianHits", "Persian Hits")
    )
    assert meta.artists == ("داریوش",)
    assert meta.title == "نون و پنیر"


def test_parse_named_phrase() -> None:
    meta = parse_track_meta("دانلود آهنگ جدید محسن یگانه به نام بهت قول میدم", None)
    assert meta.artists == ("محسن یگانه",)
    assert meta.title == "بهت قول میدم"
    assert meta.confidence == 75


def test_parse_by_phrase() -> None:
    meta = parse_track_meta("Bohemian Rhapsody by Queen", None)
    assert (meta.artists, meta.title) == (("Queen",), "Bohemian Rhapsody")


def test_parse_features_from_title() -> None:
    meta = parse_track_meta("Bi Ghararam (feat. Hamed)", "Sirvan")
    assert meta.title == "Bi Ghararam"
    assert meta.artists == ("Sirvan",)
    assert meta.features == ("Hamed",)


def test_parse_falls_back_to_file_name() -> None:
    meta = parse_track_meta(None, None, file_name="Shadmehr_Aghili - Taghdir.mp3")
    assert meta.artists == ("Shadmehr Aghili",)
    assert meta.title == "Taghdir"
    assert meta.needs_review


def test_parse_title_only_needs_review() -> None:
    meta = parse_track_meta("Instrumental 04", None)
    assert meta.artists == ()
    assert meta.title == "Instrumental 04"
    assert meta.confidence == 50


def test_parse_caption_fallback() -> None:
    caption = "🎤 خواننده: هایده\n🎵 آهنگ: گل سنگم\n@channel"
    meta = parse_track_meta(None, None, caption=caption)
    assert meta.artists == ("هایده",)
    assert meta.title == "گل سنگم"
    assert meta.confidence == 60


def test_parse_nothing() -> None:
    meta = parse_track_meta(None, None)
    assert meta == type(meta)(title="", artists=(), features=(), confidence=20)


def test_candidate_artist_names() -> None:
    assert candidate_artist_names("Moein - Shab", "x.mp3") == {"moein", "shab"}
    assert candidate_artist_names(None, None) == set()


@pytest.mark.parametrize(
    ("fa", "latin"),
    [
        ("معین", "moein"),
        ("گوگوش", "googoosh"),
        ("هایده", "hayedeh"),
        ("ابی", "ebi"),
        ("شادمهر", "shadmehr"),
        ("داریوش", "dariush"),
        ("سیروان خسروی", "sirvan khosravi"),
        ("مهستی", "mahasti"),
        ("چاوشی", "chavoshi"),
        ("تتلو", "tataloo"),
        ("محسن یگانه", "mohsen yeganeh"),
        ("سیاوش قمیشی", "siavash ghomayshi"),
    ],
)
def test_skeleton_matches_across_scripts(fa: str, latin: str) -> None:
    assert skeleton(fa) == skeleton(latin)
    assert skeleton(fa)


def test_to_latin() -> None:
    assert to_latin("Moein") == "moein"
    assert to_latin("هایده") == "haide"
    assert to_latin("یاس") == "yas"


def test_skeleton_edge_cases() -> None:
    assert skeleton("") == ""
    assert skeleton("aaa") == ""
    assert skeleton("Khaneh!!") == "xn"


@pytest.mark.parametrize(
    ("texts", "lang"),
    [
        (("شب بارونی", "معین"), "fa"),
        (("كتاب الحب",), "ar"),
        (("ئەڤین",), "ku"),
        (("Bohemian Rhapsody",), "en"),
        (("Şımarık",), "tr"),
        (("123",), "other"),
        ((None, ""), None),
        (("سلام",), "fa"),
    ],
)
def test_detect_language(texts: tuple[str | None, ...], lang: str | None) -> None:
    assert detect_language(*texts) == lang


def test_seeded_artist_keys_are_normalized() -> None:
    """The reference-data migration must contain keys in normalize_key() form."""
    sql = (Path(__file__).parents[2] / "migrations/sql/0002_reference_data.sql").read_text("utf-8")

    rows = re.findall(r"\('([^']+)', '([^']+)', '[^']+', ARRAY\[([^\]]+)\]\)", sql)
    assert len(rows) >= 30
    for name, key, aliases in rows:
        assert normalize_key(name) == key, name
        for alias in re.findall(r"'([^']+)'", aliases):
            assert normalize_key(alias) == alias, alias


# ── the catalogue is not Persian-only ─────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "artist", "title"),
    [
        # English channels label their posts the way Persian ones do.
        ("Dua Lipa - Levitating (Official Video)", "Dua Lipa", "Levitating"),
        ("The Weeknd – Blinding Lights [320kbps]", "The Weeknd", "Blinding Lights"),
        ("Free Download | Coldplay - Yellow", "Coldplay", "Yellow"),
        ("Adele - Hello (HD)", "Adele", "Hello"),
        ("Download Song Eminem - Lose Yourself", "Eminem", "Lose Yourself"),
        # Arabic. The letters come back folded to their Persian shapes (ي→ی, ك→ک,
        # ة→ه): that fold is what lets one song posted in both spellings dedupe to
        # one track, and it is deliberate, not a bug to fix here.
        ("حصري | عمرو دياب - تملي معاك", "عمرو دیاب", "تملی معاک"),
        ("تحميل اغنية جديدة فيروز - زهرة المدائن", "فیروز", "زهره المداین"),
        # Turkish.
        ("Tarkan - Kuzu Kuzu", "Tarkan", "Kuzu Kuzu"),
    ],
)
def test_titles_in_other_languages_parse_too(raw: str, artist: str, title: str) -> None:
    """Nothing in the pipeline is Persian-only; only the seeded channels were."""
    meta = parse_track_meta(raw, None, [])
    assert meta.artists == (artist,)
    assert meta.title == title


def test_video_quality_labels_are_dropped_only_in_brackets() -> None:
    """ "(HD)" is never part of a name; "HD" on its own might be."""
    assert clean_display("Adele - Hello (HD)") == "Adele - Hello"
    assert clean_display("HD Empire - Runaway") == "HD Empire - Runaway"
