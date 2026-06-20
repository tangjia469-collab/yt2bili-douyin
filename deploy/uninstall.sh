#!/usr/bin/env bash
#
# Uninstall the yt2bili launchd jobs.
#
# Unloads all four jobs and removes their plists from ~/Library/LaunchAgents/.
# Leaves your data (~/yt2bili: db, warehouse, config, logs) untouched.

set -euo pipefail

LAUNCH_AGENTS="${HOME}/Library/LaunchAgents"
JOBS=(discover worker publish web)

for job in "${JOBS[@]}"; do
  dst="${LAUNCH_AGENTS}/com.yt2bili.${job}.plist"
  if [[ -f "${dst}" ]]; then
    launchctl unload "${dst}" 2>/dev/null || true
    rm -f "${dst}"
    echo "removed com.yt2bili.${job}"
  fi
done

echo
echo "Uninstalled. Your data in ~/yt2bili was left untouched."
