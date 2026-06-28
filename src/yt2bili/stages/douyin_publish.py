"""Publish stage: upload final.mp4 to Douyin Creator Center via ego-browser."""

from __future__ import annotations

import json
import logging
import shlex
import subprocess
from pathlib import Path

from ..config import DouyinConfig

logger = logging.getLogger(__name__)


def _ffprobe_binary() -> str:
    ffprobe_full = Path("/opt/homebrew/opt/ffmpeg-full/bin/ffprobe")
    if ffprobe_full.exists():
        return str(ffprobe_full)
    return "ffprobe"


def _media_streams(path: Path) -> list[dict]:
    result = subprocess.run(
        [
            _ffprobe_binary(),
            "-v", "error",
            "-show_entries", "stream=codec_type,codec_name,width,height",
            "-of", "json",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        logger.warning("ffprobe failed for %s: %s", path, result.stderr.strip())
        return []
    try:
        data = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        logger.warning("ffprobe JSON parse failed for %s: %s", path, exc)
        return []
    return data.get("streams", [])


def is_valid_upload_video(path: Path) -> bool:
    """Return True when the file has both video and audio streams."""
    streams = _media_streams(path)
    has_video = any(s.get("codec_type") == "video" for s in streams)
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    return has_video and has_audio


def _douyin_upload_script(upload_url: str, final_mp4: Path, title: str, tags: list[str]) -> str:
    # Keep selectors broad: Douyin changes DOM often; text checks gate success.
    title_text = title[:80]
    tag_text = " ".join(f"#{tag}" for tag in tags)
    desc_text = f"{title_text} {tag_text}".strip()
    return f"""
const task = await useOrCreateTaskSpace('douyin publish')
await openOrReuseTab({json.dumps(upload_url)}, {{ wait: true, timeout: 60 }})
await wait(3)
const before = await js(String.raw`document.body.innerText`)
if (/登录|扫码|验证码/.test(before) && !/发布|上传/.test(before)) {{
  cliLog(JSON.stringify({{ok:false, reason:'login_required', text: before.slice(0, 1000)}}))
  return
}}
const input = await js(String.raw`(() => {{
  const inputs = [...document.querySelectorAll('input[type="file"]')]
  return inputs.length ? 'input[type="file"]' : ''
}})()`)
if (!input) {{
  cliLog(JSON.stringify({{ok:false, reason:'file_input_not_found', text: before.slice(0, 1000)}}))
  return
}}
await uploadFile(input, {json.dumps(str(final_mp4))})
await wait(5)
await js(String.raw`(() => {{
  const title = {json.dumps(title_text)}
  const desc = {json.dumps(desc_text)}
  const editable = [...document.querySelectorAll('[contenteditable="true"], textarea, input')]
  for (const el of editable) {{
    const label = (el.getAttribute('placeholder') || el.getAttribute('aria-label') || '').toLowerCase()
    const text = (el.innerText || el.value || '')
    if (!text && /标题|title/.test(label)) {{ el.focus(); document.execCommand('insertText', false, title); break }}
  }}
  for (const el of editable) {{
    const label = (el.getAttribute('placeholder') || el.getAttribute('aria-label') || '').toLowerCase()
    const text = (el.innerText || el.value || '')
    if (!text && /简介|描述|desc|内容/.test(label)) {{ el.focus(); document.execCommand('insertText', false, desc); break }}
  }}
}})()`)
await wait(1)
const clicked = await js(String.raw`(() => {{
  const buttons = [...document.querySelectorAll('button, [role="button"], div, span')]
  const btn = buttons.find(el => /发布|投稿/.test((el.innerText || el.textContent || '').trim()) && !/定时|规则/.test((el.innerText || el.textContent || '').trim()))
  if (!btn) return false
  btn.scrollIntoView({{block:'center'}})
  btn.click()
  return true
}})()`)
await wait(8)
const after = await js(String.raw`document.body.innerText`)
const ok = /发布成功|提交成功|审核中|作品管理/.test(after) && !/失败|错误|重新上传/.test(after)
cliLog(JSON.stringify({{ok, clicked, text: after.slice(0, 2000)}}))
"""


def publish_to_douyin(warehouse_dir: Path, title: str, config: DouyinConfig) -> bool:
    """Upload final.mp4 to Douyin Creator Center using ego-browser.

    Returns False when login/manual verification is required or when the page
    does not show a success/audit-pending state after submission.
    """
    warehouse_dir = Path(warehouse_dir)
    final_mp4 = warehouse_dir / "final.mp4"
    if not final_mp4.exists():
        logger.warning("publish_to_douyin: final.mp4 not found in %s", warehouse_dir)
        return False
    if not is_valid_upload_video(final_mp4):
        logger.warning("publish_to_douyin: final.mp4 is not a valid video+audio file in %s", warehouse_dir)
        return False

    script = _douyin_upload_script(config.upload_url, final_mp4, title, config.tags)
    cmd = ["ego-browser", "nodejs"]
    logger.info("Invoking Douyin browser upload for %s", warehouse_dir.name)
    result = subprocess.run(cmd, input=script, capture_output=True, text=True)
    if result.returncode != 0:
        logger.warning("ego-browser failed (rc=%d): %s", result.returncode, result.stderr.strip())
        return False

    lines = [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]
    payload = None
    for line in reversed(lines):
        try:
            payload = json.loads(line)
            break
        except json.JSONDecodeError:
            continue
    if not payload:
        logger.warning("Douyin upload returned no JSON status. stdout=%s", result.stdout[-1000:])
        return False

    if payload.get("ok") is True:
        logger.info("Douyin upload succeeded/audit-pending for %s", warehouse_dir.name)
        return True

    logger.warning(
        "Douyin upload not completed for %s: %s",
        warehouse_dir.name,
        payload.get("reason") or payload.get("text", "")[:300],
    )
    return False
