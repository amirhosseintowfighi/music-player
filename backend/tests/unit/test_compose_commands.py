"""Compose commands must be one command, not several by accident.

A folded YAML scalar (`>-`) keeps the newline of any line indented *deeper* than
the first one. `docker compose config` accepts the result happily, and the
container then runs the first line and treats the rest as separate commands — which
is how a fully automated certbot run turned into an interactive questionnaire on a
real server. Nothing else catches it, so this does.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
COMPOSE_FILES = [*sorted(ROOT.glob("infra/compose/*.yml")), ROOT / "compose.yaml"]


def entries() -> list[tuple[str, str, str, str]]:
    found: list[tuple[str, str, str, str]] = []
    for path in COMPOSE_FILES:
        data = yaml.safe_load(path.read_text("utf-8"))
        for name, service in (data.get("services") or {}).items():
            for key in ("command", "entrypoint"):
                value = service.get(key)
                if isinstance(value, str):
                    found.append((path.name, name, key, value))
    return found


def test_the_compose_files_are_there() -> None:
    assert COMPOSE_FILES and entries()


@pytest.mark.parametrize(
    ("file", "service", "key", "value"),
    entries(),
    ids=[f"{f}:{s}.{k}" for f, s, k, _ in entries()],
)
def test_a_string_command_is_a_single_line(file: str, service: str, key: str, value: str) -> None:
    assert "\n" not in value, (
        f"{file} → {service}.{key} spans {value.count(chr(10)) + 1} lines after YAML folding, "
        "so the container will run them as separate commands. Keep every continuation "
        "line at the same indentation as the first, or use the list form."
    )
