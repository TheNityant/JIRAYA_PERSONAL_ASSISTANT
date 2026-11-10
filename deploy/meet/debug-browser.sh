#!/usr/bin/env bash
# Direct Chromium: no Playwright, credentials, database or saved Google profile.
set -euo pipefail
mode=${1:-}
[[ "$mode" != --inside ]] || shift
url=${1:-}
if [[ ! "$url" =~ ^https://meet\.google\.com/[a-z]{3}-[a-z]{4}-[a-z]{3}$ ]]; then
  echo 'Usage: bash deploy/meet/debug-browser.sh https://meet.google.com/abc-defg-hij' >&2
  exit 2
fi
if [[ "$mode" != --inside ]]; then
  cd "$(dirname "$0")/../.."
  echo 'Desktop requires SSH tunnel: http://127.0.0.1:6080/vnc.html?autoconnect=1&resize=scale'
  echo 'Close Chromium or press Ctrl+C to end; timeout is 15 minutes.'
  exec sudo docker run --rm --init --name jiraya-meet-debug \
    --memory=1536m --shm-size=512m --cpus=1.5 \
    -p 127.0.0.1:6080:6080 \
    -v "$PWD/deploy/meet/debug-browser.sh:/tmp/debug-browser.sh:ro" \
    --entrypoint /usr/bin/timeout jiraya-meet:local \
    --signal=TERM --kill-after=10s 15m bash /tmp/debug-browser.sh --inside "$url"
fi
profile=$(mktemp -d)
pids=()
cleanup() {
  trap - EXIT INT TERM
  for pid in "${pids[@]}"; do kill "$pid" 2>/dev/null || true; done
  wait 2>/dev/null || true
  rm -rf "$profile"
}
trap cleanup EXIT INT TERM
Xvfb :99 -screen 0 1280x800x24 -nolisten tcp >/tmp/display.log 2>&1 &
pids+=("$!")
for ((i=0;i<50;i++)); do
  [[ -S /tmp/.X11-unix/X99 ]] && break
  sleep .1
done
[[ -S /tmp/.X11-unix/X99 ]] || { echo 'Display failed to start.' >&2; exit 1; }
export DISPLAY=:99
x11vnc -display :99 -localhost -nopw -forever -rfbport 5900 >/tmp/vnc.log 2>&1 &
pids+=("$!")
websockify --web /usr/share/novnc 0.0.0.0:6080 127.0.0.1:5900 >/tmp/desktop.log 2>&1 &
pids+=("$!")
chrome=''
for candidate in /ms-playwright/chromium-*/chrome-linux*/chrome; do
  [[ -x "$candidate" ]] && chrome=$candidate
done
[[ -n "$chrome" ]] || { echo 'Chromium is missing.' >&2; exit 1; }
# Docker needs --no-sandbox. No debugging protocol, stealth or UA override.
"$chrome" --no-sandbox --user-data-dir="$profile" --no-first-run \
  --no-default-browser-check --disable-sync --lang=en-US --start-maximized "$url" \
  >/tmp/chromium.log 2>&1 &
pids+=("$!")
wait "${pids[-1]}"
