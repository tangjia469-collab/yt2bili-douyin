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
BATCH_SIZE = 20

_SYSTEM_PROMPT = (
    "你是一名专业字幕翻译员。将英文字幕翻译成简体中文。"
    "保持每条字幕的简洁，适合屏幕显示。"
    "必须逐条翻译，不要合并、删除、改顺序。"
    "只输出 JSON 字符串数组，不要加解释，不要 Markdown。"
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

def _strip_json_fence(content: str) -> str:
    """Remove common Markdown code fences around a JSON response."""
    content = content.strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?\s*", "", content, flags=re.I)
        content = re.sub(r"\s*```$", "", content)
    return content.strip()


def _parse_minimax_translations(content: str) -> List[str]:
    """Parse MiniMax output as JSON array first, then numbered/plain lines."""
    raw = _strip_json_fence(content)

    # Preferred format: a JSON array of strings.
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("[")
        end = raw.rfind("]")
        if start != -1 and end > start:
            try:
                parsed = json.loads(raw[start:end + 1])
            except json.JSONDecodeError:
                parsed = None
        else:
            parsed = None
    if isinstance(parsed, list):
        result = []
        for item in parsed:
            if isinstance(item, str):
                result.append(item.strip())
            elif isinstance(item, dict):
                value = item.get("text") or item.get("translation") or item.get("zh")
                result.append(str(value).strip() if value is not None else "")
            else:
                result.append(str(item).strip())
        return result

    # Backward compatibility: numbered or one-translation-per-line output.
    lines = [l.strip() for l in content.strip().splitlines() if l.strip()]
    return [re.sub(r"^\d+[\.、]\s*", "", line).strip() for line in lines]


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
    user_content = (
        f"视频标题：{title}\n\n"
        f"请把下面 {len(texts)} 条字幕翻译成简体中文，并返回长度正好为 {len(texts)} 的 JSON 字符串数组。"
        "每个数组元素对应同序号的一条字幕；拟声词/音效也要保留或翻译，不要省略。\n\n"
        f"{numbered}"
    )

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

    return _parse_minimax_translations(content)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _translate_batch_resilient(texts: List[str], api_key: str, title: str = "") -> List[str]:
    """Translate a batch; split and retry if MiniMax returns the wrong count."""
    zh_texts = call_minimax(texts, api_key, title)
    if len(zh_texts) == len(texts):
        return zh_texts

    logger.warning(
        "MiniMax returned %d items for batch of %d",
        len(zh_texts), len(texts),
    )
    if len(texts) <= 1:
        logger.warning("Keeping English for one subtitle line after MiniMax count mismatch")
        return texts

    mid = len(texts) // 2
    return (
        _translate_batch_resilient(texts[:mid], api_key, title)
        + _translate_batch_resilient(texts[mid:], api_key, title)
    )


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

        zh_texts = _translate_batch_resilient(texts, api_key, title)

        for cue, zh in zip(batch, zh_texts):
            translated_cues.append({
                "index": cue["index"],
                "start": cue["start"],
                "end": cue["end"],
                "text": zh,
            })

    return build_srt(translated_cues)
