"""Live answer streaming for ``POST /chat/stream``.

The route sets a :class:`StreamSink` in a ContextVar for the duration of one
``ChatService.chat`` call. Providers push text into it as it is generated, and
the service pushes short status lines ("Searching the lesson"). Outside the
streaming route the ContextVar is unset and every helper here is a no-op, so
``POST /chat``, quizzes and tests behave exactly as before.

What streams is a *preview*. Citation validation still runs on the finished
reply, and the route's final ``done`` event carries the validated
:class:`ChatResponse`, which replaces the preview (a refusal replaces it
entirely). The preview can never contain anything after the playhead: it is
generated from the already time-gated passages, the same prompt as the final
answer.
"""

from __future__ import annotations

import asyncio
import re
from contextvars import ContextVar

from app.chat.prompts import strip_citation_markers

#: A trailing fragment that may be the start of a ``[chunk_id=`` marker
#: ("[", "[chu", "[chunk_id=res_ab"). Hidden until the marker closes.
_PARTIAL_MARKER_RE = re.compile(r"\s*\[(?:c(?:h(?:u(?:n(?:k(?:_(?:i(?:d(?:=[^\]]*)?)?)?)?)?)?)?)?)?$")


class StreamSink:
    """Collects one answer's events; safe to feed from a worker thread."""

    def __init__(self) -> None:
        self._loop = asyncio.get_running_loop()
        self.queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue()
        self._raw = ""
        self.deltas_since_reset = 0

    def _put(self, kind: str, value: str) -> None:
        self._loop.call_soon_threadsafe(self.queue.put_nowait, (kind, value))

    def status(self, text: str) -> None:
        self._put("status", text)

    def reset(self) -> None:
        """A new provider call starts; whatever was previewed is discarded."""
        self._raw = ""
        self.deltas_since_reset = 0
        self._put("text", "")

    def delta(self, piece: str) -> None:
        if not piece:
            return
        self._raw += piece
        self.deltas_since_reset += 1
        # The learner sees prose, never transport markers: strip complete ones
        # and hold back a marker that has not closed yet.
        shown = strip_citation_markers(_PARTIAL_MARKER_RE.sub("", self._raw))
        self._put("text", shown)


_SINK: ContextVar[StreamSink | None] = ContextVar("chat_stream_sink", default=None)


def current() -> StreamSink | None:
    return _SINK.get()


def bind(sink: StreamSink):
    return _SINK.set(sink)


def unbind(token) -> None:
    _SINK.reset(token)


def status(text: str) -> None:
    sink = _SINK.get()
    if sink is not None:
        sink.status(text)
