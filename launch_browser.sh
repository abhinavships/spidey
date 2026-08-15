#!/usr/bin/env bash
# launch_browser.sh — start a real Chrome with CDP open.
set -euo pipefail

PORT="${WA_CDP_PORT:-9222}"
PROFILE="${WA_PROFILE:-$HOME/wa-profile}"
mkdir -p "$PROFILE"

for CHROME in \
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  "$(command -v google-chrome || true)" \
  "$(command -v google-chrome-stable || true)" \
  "$(command -v chromium || true)" ; do
  [ -x "$CHROME" ] && break
done

if [ ! -x "${CHROME:-}" ]; then
  echo "Could not find Chrome. Install it, or set CHROME=/path/to/chrome" >&2
  exit 1
fi

echo "Chrome: $CHROME"
echo "Profile: $PROFILE"
echo "Log into GitHub in the window that opens, then leave it open."

"$CHROME" \
  --remote-debugging-port="$PORT" \
  --user-data-dir="$PROFILE" \
  --no-first-run --no-default-browser-check \
  "https://github.com" &

sleep 2
curl -s "http://localhost:$PORT/json/version" | head -3 || \
  echo "CDP not responding yet on port $PORT — give it a few seconds."
