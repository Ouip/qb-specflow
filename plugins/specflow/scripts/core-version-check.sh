#!/usr/bin/env bash
# SessionStart: compare the plugin version with core: in the product's specflow.md; print one line if they differ. Always exits 0.
# Usage: core-version-check.sh <plugin root> <project root>
plugin="${1:-}"; proj="${2:-}"
[ -n "$plugin" ] && [ -n "$proj" ] || exit 0
b=$(sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([0-9][0-9.]*\)".*/\1/p' "$plugin/.claude-plugin/plugin.json" 2>/dev/null | head -1)
f=$(find "$proj" -maxdepth 3 -name specflow.md -not -path '*/.git/*' 2>/dev/null | head -1)
[ -n "$b" ] && [ -n "$f" ] || exit 0
a=$(head -10 "$f" | sed -n 's/^core:[[:space:]]*\([0-9][0-9.]*\).*/\1/p' | head -1)
[ -n "$a" ] || exit 0
[ "$a" = "$b" ] && exit 0
IFS=. read -r a1 a2 a3 <<<"$a"; IFS=. read -r b1 b2 b3 <<<"$b"
if [ "${a1:-0}" -ne "${b1:-0}" ]; then
  echo "specflow: product pins $a, plugin is $b; major version differs, read the CHANGELOG migration notes before working"
elif [ $(( ${a2:-0} * 100000 + ${a3:-0} )) -lt $(( ${b2:-0} * 100000 + ${b3:-0} )) ]; then
  echo "specflow: product pins $a, plugin is $b; update available"
else
  echo "specflow: product pins $a, plugin is $b; the plugin is older than the pin, update the plugin first (marketplace update, /reload-plugins)"
fi
exit 0
