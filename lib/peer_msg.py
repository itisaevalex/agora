"""
peer-msg — the wire format for inter-session messages.

XML-tagged for clean recognition in Claude transcripts:

    <peer-msg from="<label>" thread="<id>" type="ask" at="<iso>">
    body lines here
    </peer-msg>
"""
from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from typing import Literal, Optional

MsgType = Literal["ask", "reply", "fyi", "escalate-cc", "done"]
VALID_TYPES = {"ask", "reply", "fyi", "escalate-cc", "done"}


@dataclass
class PeerMsg:
    sender_label: str
    thread: str
    msg_type: MsgType
    body: str
    at: str  # ISO timestamp

    def to_wire(self) -> str:
        return (
            f'<peer-msg from="{_attr(self.sender_label)}" '
            f'thread="{_attr(self.thread)}" '
            f'type="{_attr(self.msg_type)}" '
            f'at="{_attr(self.at)}">\n'
            f"{self.body}\n"
            f"</peer-msg>"
        )

    def to_dict(self) -> dict:
        return asdict(self)


_TAG_RE = re.compile(
    r'<peer-msg\s+([^>]*?)>\s*(.*?)\s*</peer-msg>',
    re.DOTALL,
)
_ATTR_RE = re.compile(r'(\w+)="([^"]*)"')


def parse(text: str) -> list[PeerMsg]:
    """Find all peer-msg blocks in arbitrary text. Skips malformed ones silently."""
    out = []
    for m in _TAG_RE.finditer(text):
        attrs = dict(_ATTR_RE.findall(m.group(1)))
        body = m.group(2)
        if not all(k in attrs for k in ("from", "thread", "type", "at")):
            continue
        if attrs["type"] not in VALID_TYPES:
            continue
        out.append(PeerMsg(
            sender_label=_unattr(attrs["from"]),
            thread=_unattr(attrs["thread"]),
            msg_type=attrs["type"],  # type: ignore
            body=body,
            at=_unattr(attrs["at"]),
        ))
    return out


def _attr(s: str) -> str:
    """Escape a value for XML attribute (quotes + angle brackets only)."""
    return (s.replace("&", "&amp;")
             .replace('"', "&quot;")
             .replace("<", "&lt;")
             .replace(">", "&gt;"))


def _unattr(s: str) -> str:
    return (s.replace("&quot;", '"')
             .replace("&lt;", "<")
             .replace("&gt;", ">")
             .replace("&amp;", "&"))
