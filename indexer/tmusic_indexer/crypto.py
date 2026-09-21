"""Indexer account sessions are encrypted at rest (AES-256-GCM).

A session string is full access to a Telegram account, so it never touches disk in
plain text. The key comes from SESSION_ENC_KEY and lives only in the edge environment.
"""

from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

SUFFIX = ".session.enc"
_AAD = b"tmusic-session-v1"


class SessionDecryptError(Exception):
    pass


def _key(secret: str) -> bytes:
    if len(secret) < 16:
        raise ValueError("SESSION_ENC_KEY must be at least 16 characters")
    return hashlib.sha256(secret.encode()).digest()


def encrypt(plaintext: str, secret: str) -> bytes:
    nonce = os.urandom(12)
    sealed = AESGCM(_key(secret)).encrypt(nonce, plaintext.encode(), _AAD)
    return base64.b64encode(nonce + sealed)


def decrypt(blob: bytes, secret: str) -> str:
    try:
        raw = base64.b64decode(blob)
        return AESGCM(_key(secret)).decrypt(raw[:12], raw[12:], _AAD).decode()
    except (InvalidTag, ValueError) as exc:
        raise SessionDecryptError("cannot decrypt session (wrong key or corrupt file)") from exc


def save_session(directory: Path, name: str, session_string: str, secret: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}{SUFFIX}"
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(encrypt(session_string, secret))
    tmp.chmod(0o600)
    tmp.replace(path)
    return path


def load_sessions(directory: Path, secret: str) -> dict[str, str]:
    """``{session_key: session_string}`` for every encrypted session in ``directory``."""
    if not directory.exists():
        return {}
    return {
        p.name.removesuffix(SUFFIX): decrypt(p.read_bytes(), secret)
        for p in sorted(directory.glob(f"*{SUFFIX}"))
    }
