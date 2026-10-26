"""Devanagari transcripts -> Hinglish in Roman script.

Gemini transcribes spoken Hindi in Devanagari, which English-medium learners
find hard to read. ``transliterate`` is an instant, free rule-based pass (used
for live captions); ``romanize`` asks the text model for natural Hinglish
spelling (used for dictation finals, which land in the input box) and falls
back to ``transliterate``. Text without Devanagari is returned unchanged.
"""

from __future__ import annotations

import asyncio
import logging
import re

from app.config import settings

log = logging.getLogger(__name__)

ROMANIZE_TIMEOUT_S = 6.0

_DEVANAGARI_RE = re.compile("[ऀ-ॿ]")
_WORD_RE = re.compile("[ऀ-ॣ०-ॿ]+")

_VOWELS = {
    "अ": "a", "आ": "aa", "इ": "i", "ई": "ee", "उ": "u", "ऊ": "oo", "ऋ": "ri",
    "ए": "e", "ऐ": "ai", "ओ": "o", "औ": "au", "ऑ": "o", "ऍ": "e",
}
_MATRAS = {
    "ा": "aa", "ि": "i", "ी": "ee", "ु": "u", "ू": "oo", "ृ": "ri",
    "े": "e", "ै": "ai", "ो": "o", "ौ": "au", "ॉ": "o", "ॅ": "e",
}
_CONSONANTS = {
    "क": "k", "ख": "kh", "ग": "g", "घ": "gh", "ङ": "n", "च": "ch", "छ": "chh",
    "ज": "j", "झ": "jh", "ञ": "n", "ट": "t", "ठ": "th", "ड": "d", "ढ": "dh",
    "ण": "n", "त": "t", "थ": "th", "द": "d", "ध": "dh", "न": "n", "प": "p",
    "फ": "ph", "ब": "b", "भ": "bh", "म": "m", "य": "y", "र": "r", "ल": "l",
    "व": "v", "श": "sh", "ष": "sh", "स": "s", "ह": "h", "ळ": "l",
}
#: Consonant + nukta.
_NUKTA = {"क": "q", "ख": "kh", "ग": "g", "ज": "z", "ड": "r", "ढ": "rh", "फ": "f"}
_PRECOMPOSED = {"क़": "q", "ख़": "kh", "ग़": "g", "ज़": "z", "ड़": "r", "ढ़": "rh", "फ़": "f"}
_SIGNS = {"ं": "n", "ँ": "n", "ः": "h"}
_DIGITS = {chr(0x0966 + i): str(i) for i in range(10)}
_VIRAMA = "्"
_NUKTA_SIGN = "़"


def has_devanagari(text: str) -> bool:
    return bool(text) and _DEVANAGARI_RE.search(text) is not None


def _word(word: str) -> str:
    # Units of [consonant-or-vowel text, vowel, inherent?, sign]
    units: list[list] = []
    chars = list(word)
    i = 0
    while i < len(chars):
        ch = chars[i]
        if ch in _PRECOMPOSED or ch in _CONSONANTS:
            cons = _PRECOMPOSED.get(ch) or _CONSONANTS[ch]
            if i + 1 < len(chars) and chars[i + 1] == _NUKTA_SIGN:
                cons = _NUKTA.get(ch, cons)
                i += 1
            units.append([cons, "a", True, ""])
        elif ch in _VOWELS:
            units.append(["", _VOWELS[ch], False, ""])
        elif ch in _MATRAS and units:
            units[-1][1], units[-1][2] = _MATRAS[ch], False
        elif ch == _VIRAMA and units:
            units[-1][1], units[-1][2] = "", False
        elif ch in _SIGNS and units:
            units[-1][3] += _SIGNS[ch]
        elif ch in _DIGITS:
            units.append([_DIGITS[ch], "", False, ""])
        i += 1
    n = len(units)
    # Schwa deletion: the final inherent "a", then right to left V C(a) C V.
    if n > 1 and units[-1][2] and not units[-1][3]:
        units[-1][1] = ""
    for k in range(n - 2, 0, -1):
        u, prev, nxt = units[k], units[k - 1], units[k + 1]
        if u[2] and not u[3] and prev[1] and nxt[1] and u[0]:
            u[1] = ""
    out = "".join(c + v + s for c, v, _, s in units)
    # Hinglish spelling: a long vowel at the end of a word is written short.
    if len(out) > 2:
        if out.endswith("aa"):
            out = out[:-1]
        elif out.endswith("ee"):
            out = out[:-2] + "i"
        elif out.endswith("een"):
            out = out[:-3] + "in"
    return out


def transliterate(text: str) -> str:
    """Rule-based Devanagari -> Roman, word by word; other text untouched."""
    if not has_devanagari(text):
        return text
    out = _WORD_RE.sub(lambda m: _word(m.group(0)), text)
    return out.replace("।", ".").replace("॥", ".")


_ROMANIZE_PROMPT = (
    "Rewrite this speech transcript in Hinglish using only Roman (Latin) "
    "letters, the way English-medium students in India text each other. Write "
    "English words (technical terms especially) in normal English spelling. Do "
    "not translate, answer, explain or add anything. Output only the rewritten "
    "text.\n\nTranscript:\n"
)


def _romanize_sync(text: str) -> str:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=settings.llm_api_key)
    resp = client.models.generate_content(
        model=settings.llm_model,
        contents=_ROMANIZE_PROMPT + text,
        config=types.GenerateContentConfig(temperature=0),
    )
    return (resp.text or "").strip()


async def romanize(text: str) -> str:
    """Natural Hinglish spelling via the text model; rule-based on any failure."""
    if not has_devanagari(text):
        return text
    if settings.llm_api_key:
        try:
            out = await asyncio.wait_for(
                asyncio.to_thread(_romanize_sync, text), timeout=ROMANIZE_TIMEOUT_S
            )
            if out and not has_devanagari(out):
                # Keep the transcript's spacing at the edges (finals are appended).
                lead = text[: len(text) - len(text.lstrip())]
                trail = text[len(text.rstrip()) :]
                return lead + out + trail
        except Exception as exc:  # noqa: BLE001 - captions must never fail
            log.warning("romanize failed: %s", type(exc).__name__)
    return transliterate(text)
