"""Guard: audio bytes must never be served by the core API (ADR-0004, ADR-003 §2-1).

The whole hybrid topology rests on this. The core runs in Iran and answers metadata;
the bytes come from an edge node abroad, through a signed ticket. If somebody ever
"simplifies" that by streaming a file from a FastAPI route, the bandwidth bill and the
latency budget both break quietly — so it breaks the build loudly instead.

This is deliberately a source check, not a behaviour check: by the time a byte-serving
route exists, review has already missed it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[2] / "app"
# Ways FastAPI/Starlette can put file bytes in a response.
BYTE_RESPONSES = re.compile(r"\b(StreamingResponse|FileResponse|send_file)\b")
AUDIO_MEDIA = re.compile(r"""media_type\s*=\s*['"]audio/""")
# The core may build edge URLs; it may not answer on a byte path itself.
BYTE_ROUTE = re.compile(r"""@router\.(get|post)\(\s*['"](/api)?/(stream|s)/""")

SOURCES = sorted(APP.rglob("*.py"))


def test_the_app_package_is_where_we_think_it_is() -> None:
    assert SOURCES, f"no sources found under {APP}"


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: str(p.relative_to(APP)))
def test_core_never_streams_bytes(path: Path) -> None:
    source = path.read_text("utf-8")
    assert not BYTE_RESPONSES.search(source), (
        f"{path.relative_to(APP)} returns a byte-streaming response. Audio must be "
        "served by the edge (ADR-0004); the core only issues signed tickets."
    )
    assert not AUDIO_MEDIA.search(source), (
        f"{path.relative_to(APP)} answers with an audio media type. See ADR-003 §2-1."
    )
    assert not BYTE_ROUTE.search(source), (
        f"{path.relative_to(APP)} declares a byte route. `/api/stream/:id` is a logical "
        "contract served from the edge, not a route on the core API."
    )


def test_the_ticket_points_at_an_edge_host_not_at_the_api() -> None:
    """`issue_ticket` builds the URL; it must come from `edge_nodes`, never from us."""
    source = (APP / "services" / "stream.py").read_text("utf-8")
    assert "await pick_edge(session)" in source
    assert 'f"{base}/s/{source.track_id}?t={token}"' in source
    assert "public_api_url" not in source  # not our own host, ever
