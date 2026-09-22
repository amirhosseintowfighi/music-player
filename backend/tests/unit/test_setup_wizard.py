"""The setup wizard: the first thing anyone runs, so it gets tested like code.

It lives outside the backend package (it must run on a bare ``python3`` before
anything is installed), so it is loaded here by path.
"""

from __future__ import annotations

import importlib.util
import socket
import sys
from pathlib import Path
from types import ModuleType

import pytest

WIZARD = Path(__file__).resolve().parents[3] / "scripts" / "setup.py"


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("setup_wizard", WIZARD)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["setup_wizard"] = module
    spec.loader.exec_module(module)
    return module


wizard = load()


# ── rendering the env file ────────────────────────────────────────────────────

TEMPLATE = """# a comment
ENV=dev
# another comment
BOT_TOKEN=123456:replace-me
JWT_ISSUER=tmusic
"""


def test_render_keeps_the_template_and_fills_in_the_answers() -> None:
    out = wizard.render_env(TEMPLATE, {"BOT_TOKEN": "1:abc", "ENV": "prod"})

    assert out.splitlines() == [
        "# a comment",
        "ENV=prod",
        "# another comment",
        "BOT_TOKEN=1:abc",
        "JWT_ISSUER=tmusic",  # untouched
    ]
    assert "# a comment" in out  # comments are the documentation; they survive


def test_answers_the_template_never_mentioned_are_appended_not_lost() -> None:
    out = wizard.render_env(TEMPLATE, {"BRAND_NEW": "1", "_private": "hidden"})

    assert "BRAND_NEW=1" in out
    assert "added by the setup wizard" in out
    assert "_private" not in out  # wizard bookkeeping is not configuration


def test_a_value_with_an_equals_sign_survives() -> None:
    out = wizard.render_env(TEMPLATE, {"BOT_TOKEN": "1:a=b=c"})
    assert "BOT_TOKEN=1:a=b=c" in out


# ── secrets ───────────────────────────────────────────────────────────────────


def test_every_secret_is_generated_fresh_and_strong() -> None:
    first, second = wizard.generated_secrets(), wizard.generated_secrets()

    assert first.keys() == second.keys()
    assert first["JWT_PRIVATE_KEY"] != second["JWT_PRIVATE_KEY"]
    for key, value in first.items():
        assert value and "replace" not in value.lower(), key
        if not key.startswith("JWT_"):
            assert len(value) >= 16, key


def test_the_jwt_pair_is_a_real_ed25519_pair_on_one_line() -> None:
    values = wizard.generated_secrets()
    private, public = values["JWT_PRIVATE_KEY"], values["JWT_PUBLIC_KEY"]

    assert private.startswith('"-----BEGIN PRIVATE KEY-----')
    assert public.startswith('"-----BEGIN PUBLIC KEY-----')
    assert "\n" not in private and "\\n" in private  # dotenv wants it escaped

    from cryptography.hazmat.primitives import serialization

    loaded = serialization.load_pem_private_key(
        private.strip('"').replace("\\n", "\n").encode(), password=None
    )
    assert (
        loaded.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
        .strip()
        == public.strip('"').replace("\\n", "\n").strip()
    )


def test_pem_to_env_is_reversible() -> None:
    pem = "-----BEGIN X-----\nline1\nline2\n-----END X-----\n"
    assert wizard.pem_to_env(pem).strip('"').replace("\\n", "\n") == pem.strip()


# ── machine checks ────────────────────────────────────────────────────────────


def test_a_busy_port_is_reported_as_busy() -> None:
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen(1)
        port = taken.getsockname()[1]
        assert wizard.port_free(port) is False
    assert wizard.port_free(port) is True  # and free again once it is closed


def test_prerequisites_always_include_python_and_docker() -> None:
    names = {check.name for check in wizard.prerequisites("dev")}
    assert {"python", "docker", "docker compose"} <= names
    # The edge does not need the core's ports.
    edge = {check.name for check in wizard.prerequisites("edge")}
    assert "port 5432" not in edge


# ── writing, and refusing to clobber ──────────────────────────────────────────


def test_an_existing_env_is_never_overwritten_silently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / ".env"
    target.write_text("KEEP=me\n", "utf-8")
    monkeypatch.setattr(wizard, "ask_yes", lambda *_a, **_k: False)

    written = wizard.write_env(target, "NEW=value\n")

    assert written is False
    assert target.read_text("utf-8") == "KEEP=me\n"  # the original is untouched
    assert (tmp_path / ".env.new").read_text("utf-8") == "NEW=value\n"


def test_saying_yes_keeps_a_backup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / ".env"
    target.write_text("OLD=1\n", "utf-8")
    monkeypatch.setattr(wizard, "ask_yes", lambda *_a, **_k: True)

    assert wizard.write_env(target, "NEW=2\n") is True
    assert target.read_text("utf-8") == "NEW=2\n"
    assert (tmp_path / ".env.bak").read_text("utf-8") == "OLD=1\n"


# ── the whole thing, without a terminal ───────────────────────────────────────


def test_the_wizard_writes_a_usable_env_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A full non-interactive run: answers scripted, output inspected."""
    answers = iter(
        [
            "example.com",  # domain
            "123456789:AAFFbbCCddEEffGGhhIIjjKKllMMnnOOppQ",  # bot token
            "@my_music_bot",  # username, with an @ the wizard should strip
            "",  # mini app url → default
            "",  # webhook url → default
            "",  # edge internal url → default
            "42424242",  # telegram id
        ]
    )
    # Empty means "the user pressed Enter", which keeps the offered default.
    monkeypatch.setattr(wizard, "ask", lambda _prompt, default="", **_k: next(answers) or default)
    monkeypatch.setattr(wizard, "ask_yes", lambda *_a, **_k: True)
    monkeypatch.setattr(wizard, "report", lambda _checks: True)

    target = tmp_path / ".env"
    assert wizard.main(["--profile", "core", "--output", str(target)]) == 0

    env = dict(
        line.split("=", 1)
        for line in target.read_text("utf-8").splitlines()
        if line and not line.startswith("#") and "=" in line
    )
    assert env["BOT_USERNAME"] == "my_music_bot"  # the @ was stripped
    assert env["BOT_TOKEN"].startswith("123456789:")
    assert env["WEBAPP_URL"] == "https://app.example.com"
    assert env["CORS_ORIGINS"] == '["https://app.example.com"]'
    assert env["ENV"] == "prod"
    assert "replace" not in env["INTERNAL_API_TOKEN"]
    assert env["JWT_PRIVATE_KEY"].startswith('"-----BEGIN')
    assert "_admin_tg_id" not in env

    printed = capsys.readouterr().out
    assert "alembic upgrade head" in printed  # it tells you what to do next
    assert "add-admin 42424242" in printed


def test_check_only_mode_touches_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wizard, "report", lambda _checks: True)
    monkeypatch.chdir(tmp_path)

    assert wizard.main(["--check"]) == 0
    assert list(tmp_path.iterdir()) == []
