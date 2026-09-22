from __future__ import annotations

import time
from pathlib import Path

import pytest

from tests.conftest import audio_doc, audio_message
from tmusic_common.stream_ticket import StreamTicket, TicketError, sign
from tmusic_indexer import crypto
from tmusic_indexer.config import Settings
from tmusic_indexer.extract import audio_document, to_audio_item
from tmusic_indexer.file_ids import decode_unique_id, document_unique_id, rle_decode, rle_encode
from tmusic_indexer.stream import RangeNotSatisfiable, parse_range, ticket_for


def test_document_unique_id_matches_bot_api_shape() -> None:
    uid = document_unique_id(5_415_958_323_487_031_234)
    # Bot API file_unique_id for documents always starts with "AgAD" (type 2 + zero run).
    assert uid.startswith("AgAD")
    assert "=" not in uid
    assert decode_unique_id(uid) == (2, 5_415_958_323_487_031_234)


@pytest.mark.parametrize("doc_id", [1, 255, 256, 2**40, 2**63 - 1, -5])
def test_unique_id_roundtrip(doc_id: int) -> None:
    assert decode_unique_id(document_unique_id(doc_id)) == (2, doc_id)


def test_rle() -> None:
    raw = b"\x02\x00\x00\x00\x05" + b"\x00" * 300 + b"\x01"
    encoded = rle_encode(raw)
    assert len(encoded) < len(raw)
    assert rle_decode(encoded) == raw
    assert rle_encode(b"\x00") == b"\x00\x01"


def test_session_encryption(tmp_path: Path) -> None:
    crypto.save_session(tmp_path, "acc1", "1BVtsOK-session", "a-very-long-test-key")
    stored = (tmp_path / "acc1.session.enc").read_bytes()
    assert b"1BVtsOK" not in stored
    assert crypto.load_sessions(tmp_path, "a-very-long-test-key") == {"acc1": "1BVtsOK-session"}
    with pytest.raises(crypto.SessionDecryptError):
        crypto.load_sessions(tmp_path, "another-long-key!!")
    assert crypto.load_sessions(tmp_path / "missing", "a-very-long-test-key") == {}
    with pytest.raises(ValueError, match="16"):
        crypto.encrypt("x", "short")


def test_extract_audio_item() -> None:
    msg = audio_message(12, audio_doc(99, title="Shab", performer="Moein"), text="caption")
    item = to_audio_item(msg)
    assert item is not None
    assert item.message_id == 12
    assert item.file_unique_id == document_unique_id(99)
    assert (item.title, item.performer, item.file_name) == ("Shab", "Moein", "song.mp3")
    assert item.duration == 200
    assert item.file_size == 5_000_000
    assert item.has_thumb
    assert item.caption == "caption"
    assert item.views == 7


def test_extract_skips_voice_and_non_audio() -> None:
    assert to_audio_item(audio_message(1, audio_doc(1, voice=True))) is None
    assert audio_document(type("M", (), {"media": None})()) is None
    no_file = audio_message(
        2, audio_doc(2, title=None, performer=None, file_name=None, thumbs=False)
    )
    item = to_audio_item(no_file)
    assert item is not None
    assert (item.title, item.performer, item.file_name, item.has_thumb) == (None, None, None, False)


@pytest.mark.parametrize(
    ("header", "size", "expected"),
    [
        (None, 100, None),
        ("bytes=0-", 100, (0, 99)),
        ("bytes=10-19", 100, (10, 19)),
        ("bytes=90-200", 100, (90, 99)),
        ("bytes=-10", 100, (90, 99)),
        ("bytes=-500", 100, (0, 99)),
        ("bytes=0-1,5-6", 100, None),
        ("items=0-1", 100, None),
        ("bytes=-", 100, None),
    ],
)
def test_parse_range(header: str | None, size: int, expected: tuple[int, int] | None) -> None:
    assert parse_range(header, size) == expected


@pytest.mark.parametrize(
    ("header", "size"),
    [("bytes=100-", 100), ("bytes=5-2", 100), ("bytes=-0", 100), ("bytes=0-", 0)],
)
def test_parse_range_unsatisfiable(header: str, size: int) -> None:
    with pytest.raises(RangeNotSatisfiable):
        parse_range(header, size)


def test_ticket_for_checks_path_and_signature() -> None:
    ticket = StreamTicket(track_id=5, user_id=1, exp=2**40, size=10, mime="audio/mpeg")
    token = sign(ticket, b"k1")
    assert ticket_for("/s/5", token, [b"k1"]) == ticket
    assert ticket_for("/t/5", token, [b"k0", b"k1"]) == ticket
    with pytest.raises(TicketError, match="mismatch"):
        ticket_for("/s/6", token, [b"k1"])
    with pytest.raises(TicketError):
        ticket_for("/x/5", token, [b"k1"])
    with pytest.raises(TicketError):
        ticket_for("/s/5", None, [b"k1"])


# ── a session logged in after startup ─────────────────────────────────────────


async def test_a_session_created_after_startup_is_picked_up(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`login` writes a file; the service that needs it is already running."""
    from tests.conftest import FakeClient
    from tmusic_indexer.account import ResolverAccount
    from tmusic_indexer.crypto import save_session

    account = ResolverAccount(settings, client_factory=lambda _s: FakeClient())
    await account.start()
    assert await account.ready_or_reload() is None  # nothing on disk yet

    save_session(
        settings.sessions_dir, "acc1", "session-string", settings.session_enc_key.get_secret_value()
    )
    # Still rate-limited from the attempt above; the next minute looks again.
    assert await account.ready_or_reload() is None
    monkeypatch.setattr("tmusic_indexer.account.RELOAD_INTERVAL_S", 0.0)

    found = await account.ready_or_reload()
    assert found is not None
    assert found.key == "acc1"


async def test_a_cooling_account_is_not_reloaded(settings: Settings) -> None:
    """Reloading a cooling account would hand it straight back to Telegram."""
    from tests.conftest import FakeClient
    from tmusic_indexer.account import Account, ResolverAccount

    account = ResolverAccount(settings, client_factory=lambda _s: FakeClient())
    account.account = Account(key="acc1", client=FakeClient(), status="cooling")
    account.account.cooling_until = time.time() + 300

    assert await account.ready_or_reload() is None
    assert account.account.status == "cooling"  # untouched
