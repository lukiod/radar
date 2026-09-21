#!/usr/bin/env bash
# Link this directory's units into the user systemd and enable the timer.
#
#   bash scheduling/install.sh
#
# Safe to rerun. It enables the timer and deliberately does not start it:
# Persistent=true means a missed run fires as soon as the timer starts, so on a
# machine that has been off this sends the whole day's queue in one go. Start it
# yourself when that is what you want.

set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

mkdir -p "$DEST"

for unit in radar-daily.service radar-daily.timer; do
  if [ -e "$DEST/$unit" ] && [ ! -L "$DEST/$unit" ]; then
    echo "refusing to replace $DEST/$unit: it is a real file, not a link." >&2
    echo "move it aside first if you want this to own it." >&2
    exit 1
  fi
  ln -sfn "$SRC/$unit" "$DEST/$unit"
  echo "unit     -> $unit"
done

systemctl --user daemon-reload
systemctl --user enable radar-daily.timer
echo
systemctl --user list-timers radar-daily.timer --all
echo
echo "Not started. Run 'systemctl --user start radar-daily.timer' when you want"
echo "a missed run to be caught up."
