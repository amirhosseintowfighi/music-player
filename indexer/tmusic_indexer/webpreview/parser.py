"""Telegram's public preview page (``t.me/s/<username>``) → ``AudioItem``.

This is the one module that knows Telegram's HTML. That markup is **not a documented
contract** — it is the output of a web widget that can change without warning — so
everything here is deliberately defensive:

- structure is found by CSS class *substring*, never by element position
- every field is optional; a message that yields no title is skipped, not guessed
- the caller gets ``PageResult.stats`` so a sudden drop in extraction rate can be
  alerted on before anyone notices missing music

**What a preview page actually gives us** (measured against real channels, 2026-09):

    <a class="tgme_widget_message_document_wrap" href="https://t.me/chan/675">
      <div class="tgme_widget_message_document_icon accent_bg audio"></div>
      <div class="tgme_widget_message_document">
        <div class="tgme_widget_message_document_title">Bezar Too Hale Khodam</div>
        <div class="tgme_widget_message_document_extra">Amir Tataloo</div>
      </div>
    </a>

So: title, performer, message id, posted-at, views and caption. **Not** duration, file
size, mime type, ``file_unique_id`` or a playable file link — the ``href`` points at
the message, not the file. Those only exist after a resolve (ADR-002 §Finding), which
is why a crawled ``AudioItem`` carries zeros for duration and size and no CDN url.

Voice notes *do* expose a CDN ``.ogg`` link, but they are not music and are ignored.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Any, ClassVar

from tmusic_common.indexer_contract import AudioItem
from tmusic_common.logging import get_logger

log = get_logger(__name__)

MAX_TEXT = 512
MAX_CAPTION = 4096
# A document block whose icon carries this word is an audio file rather than a PDF.
AUDIO_ICON = "audio"


@dataclass(slots=True)
class Node:
    """The little of a DOM we need: a tag, its classes, its text and its children."""

    tag: str
    attrs: dict[str, str]
    text: str = ""
    children: list[Node] = field(default_factory=list)

    @property
    def classes(self) -> str:
        return self.attrs.get("class", "")

    def has(self, needle: str) -> bool:
        return needle in self.classes

    def find(self, needle: str) -> Node | None:
        """First descendant whose class contains ``needle`` (depth first)."""
        for child in self.children:
            if child.has(needle):
                return child
            found = child.find(needle)
            if found is not None:
                return found
        return None

    def find_all(self, needle: str) -> list[Node]:
        out: list[Node] = []
        for child in self.children:
            if child.has(needle):
                out.append(child)
            out.extend(child.find_all(needle))
        return out

    def all_text(self) -> str:
        parts = [self.text] + [child.all_text() for child in self.children]
        return " ".join(part for part in parts if part).strip()


class _Tree(HTMLParser):
    """Minimal HTML → Node tree. stdlib only: one more dependency is not worth it."""

    VOID: ClassVar[set[str]] = {
        "br", "img", "meta", "link", "input", "hr", "source", "path", "use", "circle",
    }  # fmt: skip

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("root", {})
        self._stack: list[Node] = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = Node(tag, {k: (v or "") for k, v in attrs})
        self._stack[-1].children.append(node)
        if tag not in self.VOID:
            self._stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._stack[-1].children.append(Node(tag, {k: (v or "") for k, v in attrs}))

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self._stack) - 1, 0, -1):
            if self._stack[index].tag == tag:
                del self._stack[index:]
                return
        # An unclosed tag is normal in the wild; ignoring it keeps the tree usable.

    def handle_data(self, data: str) -> None:
        stripped = data.strip()
        if stripped:
            node = self._stack[-1]
            node.text = f"{node.text} {stripped}".strip() if node.text else stripped


@dataclass(frozen=True, slots=True)
class PageStats:
    """How well the parser did on one page — the input to the health check."""

    messages: int = 0
    audio: int = 0
    skipped_no_title: int = 0
    voice: int = 0

    @property
    def extraction_rate(self) -> float:
        """Share of messages that produced a usable audio item."""
        return self.audio / self.messages if self.messages else 0.0


@dataclass(frozen=True, slots=True)
class ChannelInfo:
    username: str | None = None
    title: str | None = None
    description: str | None = None
    subscribers: int | None = None


@dataclass(frozen=True, slots=True)
class PageResult:
    items: list[AudioItem]
    message_ids: list[int]
    channel: ChannelInfo
    stats: PageStats
    mentions: list[str]
    # Every message's date, music or not: this is how a channel's posting rate is
    # measured, and it is what the candidate score is partly built from.
    dates: list[datetime] = field(default_factory=list)

    @property
    def posts_per_day(self) -> float | None:
        """Posting rate over the span this page covers, or None for a single post."""
        if len(self.dates) < 2:
            return None
        span = (max(self.dates) - min(self.dates)).total_seconds()
        if span <= 0:
            return float(len(self.dates))  # a burst in the same second
        return len(self.dates) * 86_400.0 / span

    @property
    def oldest_id(self) -> int | None:
        return min(self.message_ids) if self.message_ids else None

    @property
    def newest_id(self) -> int | None:
        return max(self.message_ids) if self.message_ids else None


class PreviewUnavailable(Exception):
    """The channel has no public preview (private, deleted, or previews disabled)."""


_POST = re.compile(r"^(?P<user>[^/]+)/(?P<id>\d+)$")
_MENTION = re.compile(r"(?:https?://)?t\.me/(?!s/|joinchat|\+)([A-Za-z][A-Za-z0-9_]{3,31})")
_COUNT = re.compile(r"([\d.,]+)\s*([KMB]?)", re.I)


def _clip(value: str | None, limit: int) -> str | None:
    if not value:
        return None
    cleaned = " ".join(value.split())
    return cleaned[:limit] or None


def _normalise_spaces(raw: str) -> str:
    """Telegram separates the number from its suffix with exotic spaces."""
    return raw.replace(" ", "").replace(" ", " ")


def parse_count(raw: str | None) -> int | None:
    """ "1.54K subscribers" → 1540. Telegram abbreviates; we want a number."""
    if not raw:
        return None
    match = _COUNT.search(raw.replace(" ", "").replace("\xa0", " "))
    if not match:
        return None
    number, suffix = match.groups()
    try:
        value = float(number.replace(",", ""))
    except ValueError:
        return None
    return int(value * {"": 1, "K": 1_000, "M": 1_000_000, "B": 1_000_000_000}[suffix.upper()])


def _posted_at(wrap: Node) -> datetime:
    time_node = next(
        (node for node in wrap.find_all("time") if node.attrs.get("datetime")), None
    ) or next((node for node in _walk(wrap) if node.tag == "time"), None)
    raw = time_node.attrs.get("datetime") if time_node else None
    if raw:
        try:
            parsed = datetime.fromisoformat(raw)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        except ValueError:
            pass
    # No date is not a reason to drop a track; "now" is wrong but harmless for ordering.
    return datetime.now(UTC)


def _walk(node: Node) -> list[Node]:
    out = [node]
    for child in node.children:
        out.extend(_walk(child))
    return out


def _is_audio_document(wrap: Node) -> bool:
    icon = wrap.find("tgme_widget_message_document_icon")
    return icon is not None and AUDIO_ICON in icon.classes


def _message_id(wrap: Node) -> int | None:
    for node in _walk(wrap):
        post = node.attrs.get("data-post")
        if post and (match := _POST.match(post)):
            return int(match.group("id"))
    return None


def _audio_item(wrap: Node, message_id: int) -> AudioItem | None:
    """One message → an item, or None when it carries no music."""
    document = wrap.find("tgme_widget_message_document_wrap")
    if document is None or not _is_audio_document(document):
        return None

    title_node = document.find("tgme_widget_message_document_title")
    extra_node = document.find("tgme_widget_message_document_extra")
    title = _clip(title_node.all_text() if title_node else None, MAX_TEXT)
    if not title:
        return None  # nothing to identify the track by: skip rather than invent

    caption_node = wrap.find("tgme_widget_message_text")
    views_node = wrap.find("tgme_widget_message_views")

    return AudioItem(
        message_id=message_id,
        posted_at=_posted_at(wrap),
        views=parse_count(views_node.all_text() if views_node else None),
        # Preview pages expose neither of these; the resolver fills them in.
        duration=0,
        file_size=0,
        title=title,
        performer=_clip(extra_node.all_text() if extra_node else None, MAX_TEXT),
        file_name=None,
        caption=_clip(caption_node.all_text() if caption_node else None, MAX_CAPTION),
        has_thumb=False,
    )


def _channel_info(root: Node) -> ChannelInfo:
    header = root.find("tgme_channel_info") or root
    title = header.find("tgme_channel_info_header_title")
    description = header.find("tgme_channel_info_description")
    username = header.find("tgme_channel_info_header_username")
    subscribers = None
    for counter in header.find_all("tgme_channel_info_counter"):
        kind = counter.find("counter_type")
        if kind and "subscriber" in kind.all_text().lower():
            value = counter.find("counter_value")
            subscribers = parse_count(value.all_text() if value else None)
    handle = username.all_text().lstrip("@") if username else None
    return ChannelInfo(
        username=_clip(handle, 64),
        title=_clip(title.all_text() if title else None, 256),
        description=_clip(description.all_text() if description else None, 1000),
        subscribers=subscribers,
    )


def parse_page(html: str) -> PageResult:
    """Parses one preview page. Raises ``PreviewUnavailable`` when there is no channel.

    Never raises for merely odd markup: a page whose structure changed yields fewer
    items and a lower ``extraction_rate``, which the health check turns into an alert.
    """
    if "tgme_widget_message_wrap" not in html:
        # An empty page is normal at the end of pagination, and it still carries the
        # channel header — that is what tells it apart from "this channel has no
        # public preview at all", which is a different answer entirely.
        if html and "tgme_channel_info" in html:
            tree = _Tree()
            tree.feed(html)
            return PageResult(
                items=[],
                message_ids=[],
                channel=_channel_info(tree.root),
                stats=PageStats(),
                mentions=[],
            )
        raise PreviewUnavailable("channel has no public message preview")

    tree = _Tree()
    tree.feed(html)
    root = tree.root

    items: list[AudioItem] = []
    message_ids: list[int] = []
    mentions: set[str] = set()
    dates: list[datetime] = []
    messages = audio = skipped = voice = 0

    for wrap in root.find_all("tgme_widget_message_wrap"):
        message_id = _message_id(wrap)
        if message_id is None:
            continue
        messages += 1
        message_ids.append(message_id)
        dates.append(_posted_at(wrap))

        if wrap.find("tgme_widget_message_voice") is not None:
            voice += 1

        document = wrap.find("tgme_widget_message_document_wrap")
        item = _audio_item(wrap, message_id)
        if item is not None:
            audio += 1
            items.append(item)
        elif document is not None and _is_audio_document(document):
            skipped += 1

        # Channel discovery (phase 4) reads these; collecting them is free here.
        for node in _walk(wrap):
            href = node.attrs.get("href", "")
            for match in _MENTION.finditer(href):
                mentions.add(match.group(1).lower())
        text_node = wrap.find("tgme_widget_message_text")
        if text_node:
            for match in _MENTION.finditer(text_node.all_text()):
                mentions.add(match.group(1).lower())

    info = _channel_info(root)
    if info.username:
        mentions.discard(info.username.lower())

    return PageResult(
        items=items,
        message_ids=message_ids,
        channel=info,
        stats=PageStats(messages=messages, audio=audio, skipped_no_title=skipped, voice=voice),
        mentions=sorted(mentions),
        dates=dates,
    )


def describe(result: PageResult) -> dict[str, Any]:
    """A compact summary for logs and the parser health monitor."""
    return {
        "messages": result.stats.messages,
        "audio": result.stats.audio,
        "rate": round(result.stats.extraction_rate, 3),
        "oldest": result.oldest_id,
        "newest": result.newest_id,
    }
