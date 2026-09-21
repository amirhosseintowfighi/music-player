import json
import time

import pytest

from app.security.initdata import InitDataError, sign_init_data, validate_init_data
from app.security.tokens import (
    AccessClaims,
    TokenError,
    decode_access,
    encode_access,
    hash_refresh_token,
    new_refresh_token,
)
from tests.conftest import BOT_TOKEN, PRIVATE_PEM, PUBLIC_PEM
from tmusic_common import stream_ticket
from tmusic_common.stream_ticket import StreamTicket, TicketError

NOW = 1_800_000_000


def _fields(**over: str) -> dict[str, str]:
    base = {
        "auth_date": str(NOW),
        "query_id": "AAE",
        "user": json.dumps({"id": 42, "first_name": "Ali", "language_code": "fa"}),
    }
    return base | over


def test_valid_init_data() -> None:
    raw = sign_init_data(_fields(start_param="pl_abc"), BOT_TOKEN)
    data = validate_init_data(raw, BOT_TOKEN, 86400, now=NOW + 10)
    assert data.user.id == 42
    assert data.start_param == "pl_abc"


def test_signature_field_is_part_of_the_hash() -> None:
    raw = sign_init_data(_fields(signature="abc"), BOT_TOKEN)
    assert validate_init_data(raw, BOT_TOKEN, 86400, now=NOW).user.id == 42


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (lambda r: r.replace("Ali", "Bob"), "bad signature"),
        (lambda r: r + "&extra=1", "bad signature"),
        (lambda r: r.split("&hash=")[0], "missing hash"),
        (lambda r: r + "&hash=deadbeef", "duplicate keys"),
        (lambda r: "%%%", "malformed"),
    ],
)
def test_tampered_init_data(mutate, reason: str) -> None:  # type: ignore[no-untyped-def]
    raw = mutate(sign_init_data(_fields(), BOT_TOKEN))
    with pytest.raises(InitDataError, match=reason):
        validate_init_data(raw, BOT_TOKEN, 86400, now=NOW)


def test_wrong_bot_token() -> None:
    raw = sign_init_data(_fields(), BOT_TOKEN)
    with pytest.raises(InitDataError, match="bad signature"):
        validate_init_data(raw, "999:OTHER", 86400, now=NOW)


def test_expired_and_future_init_data() -> None:
    raw = sign_init_data(_fields(), BOT_TOKEN)
    with pytest.raises(InitDataError, match="expired"):
        validate_init_data(raw, BOT_TOKEN, 86400, now=NOW + 86401)
    with pytest.raises(InitDataError, match="future"):
        validate_init_data(raw, BOT_TOKEN, 86400, now=NOW - 120)


def test_init_data_with_bad_user_payload() -> None:
    raw = sign_init_data(_fields(user='{"first_name": "x"}'), BOT_TOKEN)
    with pytest.raises(InitDataError, match="invalid payload"):
        validate_init_data(raw, BOT_TOKEN, 86400, now=NOW)


CLAIMS = AccessClaims(user_id=7, tg_id=42, plan="free", lang="fa", features=("discover_weekly",))


def test_access_token_roundtrip() -> None:
    token, exp = encode_access(CLAIMS, PRIVATE_PEM, "tmusic", 900)
    assert exp > time.time()
    assert decode_access(token, PUBLIC_PEM, "tmusic") == CLAIMS


def test_access_token_rejections() -> None:
    token, _ = encode_access(CLAIMS, PRIVATE_PEM, "tmusic", 900, now=time.time() - 1000)
    with pytest.raises(TokenError):
        decode_access(token, PUBLIC_PEM, "tmusic")
    fresh, _ = encode_access(CLAIMS, PRIVATE_PEM, "tmusic", 900)
    with pytest.raises(TokenError):
        decode_access(fresh, PUBLIC_PEM, "other-issuer")
    with pytest.raises(TokenError):
        decode_access(fresh[:-4] + "AAAA", PUBLIC_PEM, "tmusic")
    with pytest.raises(TokenError):
        decode_access("not.a.jwt", PUBLIC_PEM, "tmusic")


def test_impersonation_claim() -> None:
    claims = AccessClaims(7, 42, "free", "en", (), act_as_admin=3)
    token, _ = encode_access(claims, PRIVATE_PEM, "tmusic", 900)
    assert decode_access(token, PUBLIC_PEM, "tmusic").act_as_admin == 3


def test_refresh_token_hashing() -> None:
    token, digest = new_refresh_token()
    assert len(token) > 40
    assert hash_refresh_token(token) == digest
    assert new_refresh_token()[0] != token


TICKET = StreamTicket(
    track_id=5, user_id=7, exp=NOW + 300, size=1234, mime="audio/mpeg",
    channel_id=99, channel_username="chan", message_id=10,
)  # fmt: skip


def test_stream_ticket_roundtrip_and_rotation() -> None:
    token = stream_ticket.sign(TICKET, b"old")
    assert stream_ticket.verify(token, [b"new", b"old"], now=NOW) == TICKET


@pytest.mark.parametrize(
    ("token_fn", "keys", "now", "msg"),
    [
        (lambda: stream_ticket.sign(TICKET, b"k"), [b"other"], NOW, "bad signature"),
        (lambda: stream_ticket.sign(TICKET, b"k"), [b"k"], NOW + 301, "expired"),
        (lambda: "garbage", [b"k"], NOW, "malformed"),
        (lambda: stream_ticket.sign(TICKET, b"k").replace(".", "x.", 1), [b"k"], NOW, "bad"),
    ],
)
def test_stream_ticket_rejections(token_fn, keys, now, msg) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(TicketError, match=msg):
        stream_ticket.verify(token_fn(), keys, now=now)


def test_stream_ticket_signed_garbage_payload() -> None:
    import base64
    import hashlib
    import hmac

    body = base64.urlsafe_b64encode(b'{"nope": 1}').rstrip(b"=").decode()
    sig = base64.urlsafe_b64encode(hmac.new(b"k", body.encode(), hashlib.sha256).digest())
    with pytest.raises(TicketError, match="malformed"):
        stream_ticket.verify(f"{body}.{sig.rstrip(b'=').decode()}", [b"k"], now=NOW)
