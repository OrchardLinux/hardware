#!/bin/sh
set -eu
cd "$(dirname "$0")"
case "${1:-}" in deny|reset|firmware) stage=$1;; *) exit 2;; esac
STATE=/run/touch-start
test -d "$STATE"
mkdir "$STATE/$stage-launched"
(
 rc=0
 sh ./run-on-ipad.sh "$stage" || rc=$?
 printf '%s\n' "$rc" > "$STATE/$stage.exit.tmp"
 mv "$STATE/$stage.exit.tmp" "$STATE/$stage.exit"
) < /dev/null > "$STATE/$stage.log" 2>&1 &
printf '%s\n' "$!" > "$STATE/$stage.pid"
echo "LAUNCHED $stage; inspect terminal receipt before advancing"
