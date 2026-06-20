#!/usr/bin/env bash
#
# Install the yt2bili launchd jobs.
#
# Substitutes the path placeholders in deploy/*.plist with this machine's
# real paths, writes them to ~/Library/LaunchAgents/, and loads them.
#
# Jobs installed:
#   com.yt2bili.discover  hourly        scan channels for new videos
#   com.yt2bili.worker    every 10 min  advance the pipeline
#   com.yt2bili.publish   daily 19:00   upload ready videos
#   com.yt2bili.web       always on     dashboard at http://127.0.0.1:8080
#
# Re-running is safe: existing jobs are unloaded first.

set -euo pipefail

# --- resolve paths ---------------------------------------------------------
PROJECT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PY="${PROJECT}/.venv/bin/python"
YT2BILI_HOME="${YT2BILI_HOME:-${HOME}/yt2bili}"
LAUNCH_AGENTS="${HOME}/Library/LaunchAgents"
DEPLOY="${PROJECT}/deploy"

# launchd jobs get a minimal PATH; include common locations for ffmpeg,
# yt-dlp, whisper-cli, biliup (Homebrew + ~/.local/bin).
JOB_PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:${HOME}/.local/bin"

if [[ ! -x "${VENV_PY}" ]]; then
  echo "ERROR: venv python not found at ${VENV_PY}" >&2
  echo "Run: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi

echo "Project:      ${PROJECT}"
echo "Data home:    ${YT2BILI_HOME}"
echo "Python:       ${VENV_PY}"
echo

mkdir -p "${YT2BILI_HOME}/logs" "${YT2BILI_HOME}/warehouse" "${LAUNCH_AGENTS}"

JOBS=(discover worker publish web)

for job in "${JOBS[@]}"; do
  src="${DEPLOY}/com.yt2bili.${job}.plist"
  dst="${LAUNCH_AGENTS}/com.yt2bili.${job}.plist"

  # Render placeholders → real paths.
  sed \
    -e "s|__VENV_PY__|${VENV_PY}|g" \
    -e "s|__PROJECT__|${PROJECT}|g" \
    -e "s|__YT2BILI_HOME__|${YT2BILI_HOME}|g" \
    -e "s|__PATH__|${JOB_PATH}|g" \
    "${src}" > "${dst}"

  # Reload: unload if present (ignore errors), then load.
  launchctl unload "${dst}" 2>/dev/null || true
  launchctl load "${dst}"
  echo "loaded com.yt2bili.${job}"
done

echo
echo "Done. Dashboard: http://127.0.0.1:8080"
echo "Logs: ${YT2BILI_HOME}/logs/"
echo
echo "Next steps if you haven't yet:"
echo "  1. Edit ${YT2BILI_HOME}/config.yaml (channels + MiniMax key)"
echo "  2. Run 'biliup login' once to authenticate Bilibili"
