"""Password hashing for admin logins — ``hashlib.scrypt``, nothing installed.

scrypt is in the standard library, is memory-hard, and is the recommended choice
where bcrypt or argon2 are not already present. One dependency not added is one
dependency not to patch.

Stored form: ``scrypt$<n>$<r>$<p>$<salt b64>$<hash b64>``. The parameters travel with
the hash, so raising them later keeps old passwords verifiable.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

# ~64 MB and ~100 ms on a small server: slow enough to make guessing expensive,
# fast enough that a login does not feel broken.
N, R, P = 2**16, 8, 1
SALT_BYTES = 16
KEY_BYTES = 32


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _derive(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(
        password.encode(), salt=salt, n=n, r=r, p=p, dklen=KEY_BYTES, maxmem=2 * n * r * 128
    )


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(SALT_BYTES)
    return f"scrypt${N}${R}${P}${_b64(salt)}${_b64(_derive(password, salt, N, R, P))}"


def verify_password(password: str, stored: str | None) -> bool:
    """False for a wrong password, a malformed hash, or no password at all."""
    if not stored:
        return False
    try:
        scheme, n, r, p, salt, expected = stored.split("$")
        if scheme != "scrypt":
            return False
        candidate = _derive(password, _unb64(salt), int(n), int(r), int(p))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(candidate, _unb64(expected))


def generate_password(words: int = 4) -> str:
    """A password a human can retype once and then paste from a manager forever."""
    alphabet = "abcdefghijkmnopqrstuvwxyz23456789"
    return "-".join("".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(words))
