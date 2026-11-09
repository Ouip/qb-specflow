#!/usr/bin/env bash
# The core must not name any single product. Usage: lint-generic.sh [root] (default: repo root)
# Hits print as file:line:text and exit 1; otherwise prints lint-generic: ok.
set -u
self="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
root="${1:-$(cd "$(dirname "$0")/../../.." && pwd)}"
# F00-F16 and M00-M05 use ASCII boundaries: under UTF-8 CJK characters count as \w, so \b misses.
pat='[Tt]ea[Bb]rew|ESP-IDF|esp_|idf\.py|Codex|teawiki|testcat|motorsim|@TL|@MS|QEMU|test\.ps1|NimBLE|ILI9341|OV3660|U／I／Q／E／M|U/I/Q/E/M'
pat="$pat"'|(^|[^A-Za-z0-9_])(F(0[0-9]|1[0-6])|M0[0-5])([^0-9]|$)'
hits=$(grep -rnE --include='*.md' --include='*.json' --include='*.sh' --include='*.py' \
  --exclude-dir=.git --exclude-dir=__pycache__ "$pat" "$root" | grep -vF "$self:" || true)
# grep may print /c/... or C:/...; exclude this script again by file name
hits=$(printf '%s\n' "$hits" | grep -v 'scripts/lint-generic\.sh:' | grep -v '^$' || true)
if [ -n "$hits" ]; then printf '%s\n' "$hits"; exit 1; fi
echo "lint-generic: ok"
