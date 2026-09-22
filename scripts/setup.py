#!/usr/bin/env python3
"""Setup wizard — turns a fresh clone into a running deployment.

    python3 scripts/setup.py            # ask everything, write .env
    python3 scripts/setup.py --profile edge --output .env.edge
    python3 scripts/setup.py --check    # only verify the machine is ready

What it does, in order:

1. checks what this machine has (docker, compose, python, git, free ports);
2. asks for the handful of things only you can know (bot token, domain, ids);
3. generates every secret itself — keys are never typed, pasted or echoed;
4. writes an ``.env`` from ``.env.example``, keeping its comments and order;
5. tells you the next three commands, in the right order.

Deliberately dependency-free: it runs on a bare ``python3`` before anything is
installed, which is exactly the moment it is needed. Nothing here talks to the
network, and an existing ``.env`` is never overwritten without saying so.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / ".env.example"


def _prepare_console() -> bool:
    """Makes stdout UTF-8 if it can be, and says whether non-ASCII is safe to print.

    A legacy Windows code page can encode neither Persian nor an em dash, and
    printing one raises halfway through a line. The wizard would rather drop to
    plain ASCII than fail on its own first message.
    """
    stream = sys.stdout
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        return True
    except (AttributeError, OSError, ValueError):
        pass
    try:
        "سلام — ✓".encode(stream.encoding or "utf-8")
    except (LookupError, UnicodeEncodeError):
        return False
    return True


UNICODE_OK = _prepare_console()
# English by default: a server console is a mixed-locale place and most people meet
# this over SSH. SETUP_LANG=fa switches it back.
_WANTS_FA = os.environ.get("SETUP_LANG", "en").lower().startswith("fa")
FA = _WANTS_FA and UNICODE_OK


def say(en: str, fa: str) -> str:
    return fa if FA else en


# ── terminal helpers ──────────────────────────────────────────────────────────

BOLD, DIM, GREEN, YELLOW, RED, RESET = (
    ("\033[1m", "\033[2m", "\033[32m", "\033[33m", "\033[31m", "\033[0m")
    if sys.stdout.isatty() and os.name != "nt"
    else ("", "", "", "", "", "")
)


def title(text: str) -> None:
    print(f"\n{BOLD}{text}{RESET}")
    print("─" * min(len(text), 70))


def ok(text: str) -> None:
    print(f"  {GREEN}✓{RESET} {text}")


def warn(text: str) -> None:
    print(f"  {YELLOW}!{RESET} {text}")


def bad(text: str) -> None:
    print(f"  {RED}✗{RESET} {text}")


def ask(prompt: str, default: str = "", *, secret: bool = False) -> str:
    """One question. Empty answer keeps the default; Ctrl-C leaves cleanly."""
    suffix = f" [{DIM}{'•' * 8 if secret and default else default}{RESET}]" if default else ""
    try:
        answer = input(f"  {prompt}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        raise SystemExit(say("cancelled", "لغو شد")) from None
    return answer or default


def ask_yes(prompt: str, default: bool = True) -> bool:
    hint = "Y/n" if default else "y/N"
    answer = ask(f"{prompt} ({hint})").lower()
    return default if not answer else answer.startswith(("y", "ب", "آ"))


# ── prerequisites ─────────────────────────────────────────────────────────────


@dataclass
class Check:
    name: str
    found: bool
    detail: str = ""
    required: bool = True


def _version(command: list[str]) -> str:
    try:
        # S603: every command here is a literal in this file, never user input.
        out = subprocess.run(  # noqa: S603
            command, capture_output=True, text=True, timeout=20, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return (out.stdout or out.stderr).strip().splitlines()[0] if out.returncode == 0 else ""


def port_free(port: int) -> bool:
    with socket.socket() as probe:
        probe.settimeout(0.4)
        return probe.connect_ex(("127.0.0.1", port)) != 0


def prerequisites(profile: str) -> list[Check]:
    checks = [
        Check("python", sys.version_info >= (3, 12), f"{sys.version.split()[0]}"),
        Check("docker", bool(shutil.which("docker")), _version(["docker", "--version"])),
        Check(
            "docker compose",
            bool(_version(["docker", "compose", "version"])),
            _version(["docker", "compose", "version"]),
        ),
        Check(
            "git",
            bool(shutil.which("git")),
            _version(["git", "--version"]),
            required=False,
        ),
    ]
    ports = {
        "core": (8000, 5432, 6379, 7700),
        "edge": (8080,),
        "dev": (8000, 5432, 6379, 7700),
    }
    for port in ports.get(profile, ()):
        checks.append(Check(f"port {port}", port_free(port), say("free", "آزاد"), required=False))
    return checks


def report(checks: list[Check]) -> bool:
    title(say("1. Checking this machine", "۱. بررسی این ماشین"))
    fine = True
    for check in checks:
        if check.found:
            ok(f"{check.name} {DIM}{check.detail}{RESET}")
        elif check.required:
            bad(f"{check.name} — {say('required', 'لازم است')}")
            fine = False
        else:
            warn(f"{check.name} — {say('missing or busy', 'نیست یا مشغول است')}")
    return fine


# ── secrets ───────────────────────────────────────────────────────────────────


def ed25519_keypair() -> tuple[str, str]:
    """A JWT signing pair, generated locally by whatever this machine has.

    Tried in order: the ``cryptography`` package, the ``openssl`` binary, then the
    project's own container. One of the three exists on any machine that can run
    this stack, and the key never leaves it.
    """
    with contextlib.suppress(Exception):  # 1. the library, if it is already installed
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        key = Ed25519PrivateKey.generate()
        private = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode()
        public = (
            key.public_key()
            .public_bytes(
                serialization.Encoding.PEM,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            )
            .decode()
        )
        return private, public

    openssl = shutil.which("openssl")  # 2. openssl, which every server has
    if openssl:
        try:
            private = subprocess.run(  # noqa: S603
                [openssl, "genpkey", "-algorithm", "ed25519"],
                capture_output=True,
                text=True,
                check=True,
                timeout=30,
            ).stdout
            public = subprocess.run(  # noqa: S603
                [openssl, "pkey", "-pubout"],
                input=private,
                capture_output=True,
                text=True,
                check=True,
                timeout=30,
            ).stdout
            return private, public
        except (OSError, subprocess.SubprocessError):
            pass

    raise RuntimeError(
        say(
            "No way to generate Ed25519 keys. Install openssl, or run:\n"
            "  docker compose run --rm api python -m app.cli gen-keys",
            "راهی برای ساخت کلید Ed25519 نبود. openssl نصب کن، یا این را اجرا کن:\n"
            "  docker compose run --rm api python -m app.cli gen-keys",
        )
    )


def pem_to_env(pem: str) -> str:
    """PEM on one line, the way dotenv wants it."""
    return '"' + pem.strip().replace("\n", "\\n") + '"'


def generated_secrets() -> dict[str, str]:
    private, public = ed25519_keypair()
    return {
        "JWT_PRIVATE_KEY": pem_to_env(private),
        "JWT_PUBLIC_KEY": pem_to_env(public),
        "WEBHOOK_SECRET": secrets.token_urlsafe(32),
        "STREAM_SIGNING_KEYS": secrets.token_urlsafe(48),
        "INTERNAL_API_TOKEN": secrets.token_urlsafe(48),
        "SESSION_ENC_KEY": secrets.token_urlsafe(32),
        "MEILI_API_KEY": secrets.token_urlsafe(32),
        "POSTGRES_PASSWORD": secrets.token_urlsafe(24),
        "APP_DB_PASSWORD": secrets.token_urlsafe(24),
        "GRAFANA_PASSWORD": secrets.token_urlsafe(16),
    }


# ── questions ─────────────────────────────────────────────────────────────────

BOT_TOKEN = re.compile(r"^\d{6,}:[A-Za-z0-9_-]{30,}$")
USERNAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{3,31}$")


@dataclass
class Answers:
    values: dict[str, str] = field(default_factory=dict)

    def set(self, key: str, value: str) -> None:
        if value:
            self.values[key] = value


def ask_core(answers: Answers, domain: str) -> None:
    title(say("3. The bot", "۳. ربات"))
    print(
        "  "
        + DIM
        + say(
            "From @BotFather: /newbot, then /setdomain for the Mini App.",
            "از @BotFather: دستور /newbot و بعد /setdomain برای مینی‌اپ.",
        )
        + RESET
    )
    while True:
        token = ask(say("Bot token", "توکن ربات"), answers.values.get("BOT_TOKEN", ""))
        if BOT_TOKEN.match(token):
            break
        bad(say("That does not look like a bot token.", "این شبیه توکن ربات نیست."))
    answers.set("BOT_TOKEN", token)

    while True:
        username = ask(say("Bot username (without @)", "یوزرنیم ربات (بدون @)")).lstrip("@")
        if USERNAME.match(username):
            break
        bad(say("4-32 characters, letters, digits, underscore.", "۴ تا ۳۲ کاراکتر مجاز."))
    answers.set("BOT_USERNAME", username)

    title(say("4. Addresses", "۴. آدرس‌ها"))
    app_url = ask(say("Mini App URL", "آدرس مینی‌اپ"), f"https://app.{domain}")
    answers.set("WEBAPP_URL", app_url)
    answers.set(
        "WEBHOOK_URL",
        ask(say("Webhook URL", "آدرس وبهوک"), f"https://hook.{domain}/tg/webhook"),
    )
    answers.set("CORS_ORIGINS", f'["{app_url}"]')
    answers.set(
        "EDGE_INTERNAL_URL",
        ask(say("Edge internal URL", "آدرس داخلی edge"), "http://edge:8080"),
    )

    title(say("5. You", "۵. شما"))
    print(
        "  "
        + DIM
        + say(
            "Your numeric Telegram id — ask @userinfobot if you do not know it.",
            "شناسهٔ عددی تلگرام خودت — اگر نمی‌دانی از @userinfobot بپرس.",
        )
        + RESET
    )
    admin_id = ask(say("Your Telegram id", "شناسهٔ تلگرام شما"), "0")
    answers.set("PAYMENTS_ADMIN_CHAT_ID", admin_id if admin_id.isdigit() else "0")
    answers.values["_admin_tg_id"] = admin_id


def ask_edge(answers: Answers) -> None:
    title(say("3. The core it reports to", "۳. هسته‌ای که به آن گزارش می‌دهد"))
    answers.set(
        "CORE_URL",
        ask(say("Core internal URL", "آدرس داخلی هسته"), "http://10.8.0.1:8080"),
    )
    print(
        "  "
        + DIM
        + say(
            "INTERNAL_API_TOKEN and STREAM_SIGNING_KEYS must be the SAME as the core's.",
            "مقادیر INTERNAL_API_TOKEN و STREAM_SIGNING_KEYS باید دقیقاً مثل هسته باشند.",
        )
        + RESET
    )
    answers.set(
        "INTERNAL_API_TOKEN",
        ask(say("INTERNAL_API_TOKEN from the core", "مقدار INTERNAL_API_TOKEN هسته")),
    )
    answers.set(
        "STREAM_SIGNING_KEYS",
        ask(say("STREAM_SIGNING_KEYS from the core", "مقدار STREAM_SIGNING_KEYS هسته")),
    )
    answers.set("BOT_TOKEN", ask(say("Bot token (for Bot API downloads)", "توکن ربات")))

    title(say("4. Telegram API (optional)", "۴. API تلگرام (اختیاری)"))
    print(
        "  "
        + DIM
        + say(
            "Only needed to play a track nobody has resolved yet. my.telegram.org → API.",
            "فقط برای پخش اولین‌بارِ ترک‌های resolve‌نشده لازم است. my.telegram.org → API.",
        )
        + RESET
    )
    answers.set("TG_API_ID", ask("TG_API_ID", "0"))
    answers.set("TG_API_HASH", ask("TG_API_HASH", "replace-me"))


# ── writing the env file ──────────────────────────────────────────────────────

_ASSIGNMENT = re.compile(r"^([A-Z][A-Z0-9_]*)=")


def render_env(template: str, values: dict[str, str]) -> str:
    """Fill ``template`` with ``values``, keeping every comment and the order.

    Keys the template does not mention are appended at the end, so nothing the
    wizard decided can silently disappear.
    """
    used: set[str] = set()
    lines: list[str] = []
    for line in template.splitlines():
        match = _ASSIGNMENT.match(line)
        if match and match.group(1) in values:
            key = match.group(1)
            lines.append(f"{key}={values[key]}")
            used.add(key)
        else:
            lines.append(line)
    extra = {k: v for k, v in values.items() if k not in used and not k.startswith("_")}
    if extra:
        lines += ["", "# ── added by the setup wizard ──"]
        lines += [f"{key}={value}" for key, value in sorted(extra.items())]
    return "\n".join(lines).rstrip() + "\n"


def write_env(path: Path, content: str) -> bool:
    """Writes the file, refusing to clobber an existing one without permission."""
    if path.exists():
        warn(say(f"{path.name} already exists.", f"فایل {path.name} از قبل هست."))
        if not ask_yes(say("Overwrite it?", "بازنویسی شود؟"), default=False):
            backup = path.with_suffix(path.suffix + ".new")
            backup.write_text(content, "utf-8", newline="\n")
            ok(
                say(
                    f"Written to {backup.name} instead.",
                    f"به‌جایش در {backup.name} نوشته شد.",
                )
            )
            return False
        path.replace(path.with_suffix(path.suffix + ".bak"))
        ok(say("Old file kept as .bak", "فایل قبلی با پسوند .bak نگه داشته شد"))
    path.write_text(content, "utf-8", newline="\n")
    with contextlib.suppress(OSError):
        path.chmod(0o600)  # secrets: owner only (a no-op on Windows)
    return True


# ── the run ───────────────────────────────────────────────────────────────────


def next_steps(profile: str, env_file: str, admin_tg_id: str) -> None:
    title(say("Done. Next:", "تمام. حالا:"))
    compose = {
        "core": f"docker compose -f infra/compose/core.yml --env-file {env_file}",
        "edge": f"docker compose -f infra/compose/edge.yml --env-file {env_file}",
        "solo": f"docker compose -f infra/compose/solo.yml --env-file {env_file}",
    }.get(profile, "docker compose")
    steps = [
        (say("start everything", "بالا آوردن سرویس‌ها"), f"{compose} up -d"),
        (
            say("create the database schema", "ساخت اسکیمای دیتابیس"),
            f"{compose} exec api alembic upgrade head",
        ),
    ]
    if profile != "edge":
        steps += [
            (
                say("make yourself an admin", "ادمین‌کردن خودت"),
                f"{compose} exec api python -m app.cli add-admin {admin_tg_id or '<your-tg-id>'}",
            ),
            (
                say("register the bot's webhook", "ثبت وبهوک ربات"),
                f"{compose} exec api python -m app.bot.set_webhook",
            ),
            (
                say("add the first channels to crawl", "افزودن اولین کانال‌ها برای کرال"),
                f"{compose} exec api python -m app.cli seed-channels channels.txt",
            ),
        ]
    else:
        steps += [
            (
                say(
                    "(optional) log the resolver account in",
                    "(اختیاری) لاگین اکانت resolver",
                ),
                f"{compose} run --rm edge python -m tmusic_indexer.login acc1",
            )
        ]
    for i, (what, command) in enumerate(steps, 1):
        print(f"\n  {BOLD}{i}.{RESET} {what}")
        print(f"     {command}")
    print(
        "\n  "
        + say(
            f"Everything the wizard decided is in {env_file}. Read docs/GETTING-STARTED.md next.",
            f"هرچه ویزارد تصمیم گرفت در {env_file} است. بعد docs/GETTING-STARTED.md را بخوان.",
        )
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Telegram Music Mini App setup wizard")
    parser.add_argument(
        "--profile",
        choices=("dev", "solo", "core", "edge"),
        default="dev",
        help=(
            "dev = local, ports on 127.0.0.1; solo = one server with TLS; "
            "core/edge = the split deployment"
        ),
    )
    parser.add_argument("--output", default="", help="where to write (default depends on profile)")
    parser.add_argument("--check", action="store_true", help="only check prerequisites")
    args = parser.parse_args(argv)

    print(
        f"\n{BOLD}"
        + say("Telegram Music Mini App — setup", "مینی‌اپ موزیک تلگرام — نصب")
        + f"{RESET}\n"
        + DIM
        + say(
            "Answer a few questions; every secret is generated here and never leaves.",
            "چند سؤال ساده؛ همهٔ کلیدها همین‌جا ساخته می‌شوند و جایی نمی‌روند.",
        )
        + RESET
    )

    ready = report(prerequisites(args.profile))
    if args.check:
        return 0 if ready else 1
    if not ready and not ask_yes(
        say(
            "Some requirements are missing. Continue anyway?",
            "بعضی پیش‌نیازها نیستند. ادامه بدهم؟",
        ),
        default=False,
    ):
        return 1

    if not EXAMPLE.exists():
        bad(
            say(
                f"{EXAMPLE} is missing — run this from the repository.",
                f"{EXAMPLE} نیست.",
            )
        )
        return 1

    title(say("2. Generating secrets", "۲. ساخت کلیدها"))
    try:
        values = generated_secrets()
    except RuntimeError as exc:
        bad(str(exc))
        return 1
    ok(
        say(
            f"{len(values)} secrets generated locally",
            f"{len(values)} کلید به‌صورت محلی ساخته شد",
        )
    )

    answers = Answers(values)
    domain = ask(say("Your domain", "دامنهٔ شما"), "example.com") if args.profile != "edge" else ""
    if domain:
        answers.set("DOMAIN", domain)
    answers.set("ENV", "dev" if args.profile == "dev" else "prod")

    if args.profile == "edge":
        ask_edge(answers)
    else:
        ask_core(answers, domain)
    if args.profile == "solo":
        title(say("6. TLS", "۶. گواهی TLS"))
        answers.set(
            "CERTBOT_EMAIL",
            ask(say("Email for Let's Encrypt", "ایمیل برای Let's Encrypt"), ""),
        )

    output = Path(
        args.output or {"core": ".env.core", "edge": ".env.edge"}.get(args.profile, ".env")
    )
    target = output if output.is_absolute() else ROOT / output
    content = render_env(EXAMPLE.read_text("utf-8"), answers.values)
    title(say("Writing the environment file", "نوشتن فایل تنظیمات"))
    if write_env(target, content):
        ok(say(f"{target.name} written", f"فایل {target.name} نوشته شد"))
    next_steps(args.profile, target.name, answers.values.get("_admin_tg_id", ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
