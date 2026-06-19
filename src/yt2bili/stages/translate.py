"""Translation stage: translate English SRT to Chinese via MiniMax API."""

from __future__ import annotations

import json
import logging
import re
from typing import Dict, List, Optional

import urllib.request
import urllib.error

logger = logging.getLogger(__name__)

MINIMAX_URL = "https://api.minimax.chat/v1/text/chatcompletion_v2"
MINIMAX_MODEL = "abab6.5s-chat"
BATCH_SIZE = 50

_SYSTEM_PROMPT = (
    "你是一名专业字幕翻译员。将英文字幕翻译成简体中文。"
    "保持每条字幕的简洁，适合屏幕显示。"
    "只输出翻译结果，每行对应一条字幕，不要加编号或解释。"
)


# ---------------------------------------------------------------------------
# SRT parsing/building
# ---------------------------------------------------------------------------

def parse_srt(srt: str) -> List[Dict]:
    """Parse SRT text into a list of cue dicts with index, start, end, text."""
    cues = []
    blocks = re.split(r"\n\s*\n", srt.strip())
    for block in blocks:
        lines = block.strip().splitlines()
        if len(lines) < 3:
            continue
        # First line: sequence number
        # Second line: timestamp
        # Remaining: text
        timing_match = re.match(
            r"(\d{2}:\d{2}:\d{2},\d{3})\s+-->\s+(\d{2}:\d{2}:\d{2},\d{3})",
            lines[1],
        )
        if not timing_match:
            continue
        cues.append({
            "index": lines[0].strip(),
            "start": timing_match.group(1),
            "end": timing_match.group(2),
            "text": "\n".join(lines[2:]),
        })
    return cues


def build_srt(entries: List[Dict]) -> str:
    """Rebuild SRT text from a list of cue dicts."""
    blocks = []
    for i, e in enumerate(entries, 1):
        blocks.append(f"{i}\n{e['start']} --> {e['end']}\n{e['text']}\n")
    return "\n".join(blocks) + "\n"


# ---------------------------------------------------------------------------
# MiniMax API call
# ---------------------------------------------------------------------------

def call_minimax(texts: List[str], api_key: str, title: str = "") -> List[str]:
    """Send a batch of subtitle lines to MiniMax and return translated lines.

    Args:
        texts: List of English subtitle strings to translate.
        api_key: MiniMax API key.
        title: Video title for context (optional).

    Returns:
        List of translated strings, same length as input if successful.
        Returns empty list on API error.
    """
    numbered = "\n".join(f"{i+1}. {t}" for i, t in enumerate(texts))
    user_content = f"视频标题：{title}\n\n请翻译以下字幕：\n{numbered}" if title else f"请翻译以下字幕：\n{numbered}"

    payload = {
        "model": MINIMAX_MODEL,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
    }

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        MINIMAX_URL,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        logger.error("MiniMax API request failed: %s", exc)
        return []
    except json.JSONDecodeError as exc:
        logger.error("MiniMax API returned invalid JSON: %s", exc)
        return []

    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as exc:
        logger.error("Unexpected MiniMax response structure: %s — %s", exc, body)
        return []

    # Parse numbered lines from response; strip leading "N. " or "N、"
    lines = [l.strip() for l in content.strip().splitlines() if l.strip()]
    cleaned = []
    for line in lines:
        cleaned.append(re.sub(r"^\d+[\.、]\s*", "", line))

    return cleaned


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def translate_srt(en_srt: str, api_key: str, title: str = "") -> str:
    """Translate an English SRT string to Chinese SRT.

    Processes cues in batches of BATCH_SIZE. If a batch response has a
    different count than expected, falls back to the original English text
    for that batch to preserve timeline integrity.

    Args:
        en_srt: English SRT text.
        api_key: MiniMax API key.
        title: Video title passed to MiniMax for context.

    Returns:
        Chinese SRT text with identical timestamps as input.
    """
    cues = parse_srt(en_srt)
    if not cues:
        return en_srt

    translated_cues = []
    for batch_start in range(0, len(cues), BATCH_SIZE):
        batch = cues[batch_start: batch_start + BATCH_SIZE]
        texts = [c["text"] for c in batch]

        zh_texts = call_minimax(texts, api_key, title)

        if len(zh_texts) != len(batch):
            logger.warning(
                "MiniMax returned %d items for batch of %d; keeping English",
                len(zh_texts), len(batch),
            )
            zh_texts = texts  # fallback: keep English

        for cue, zh in zip(batch, zh_texts):
            translated_cues.append({
                "index": cue["index"],
                "start": cue["start"],
                "end": cue["end"],
                "text": zh,
            })

    return build_srt(translated_cues)
