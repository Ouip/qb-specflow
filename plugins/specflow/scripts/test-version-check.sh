#!/usr/bin/env bash
# Tests for core-version-check.sh. Usage: bash test-version-check.sh
set -u
here="$(cd "$(dirname "$0")" && pwd)"
hook="$here/core-version-check.sh"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
mkplugin() { mkdir -p "$tmp/$1/.claude-plugin"; printf '{\n  "name": "specflow",\n  "version": "%s"\n}\n' "$2" > "$tmp/$1/.claude-plugin/plugin.json"; }
mkproj() { mkdir -p "$tmp/$1/docs"; printf -- '---\nformat: 2\ncore: %s\npython: python\n---\n# Settings\n' "$2" > "$tmp/$1/docs/specflow.md"; }
mkplugin p010 0.1.0; mkplugin p020 0.2.0; mkplugin p100 1.0.0
mkdir -p "$tmp/empty"; mkproj proj 0.1.0; mkproj "my proj" 0.1.0; mkproj ahead 0.2.0
pass=0; total=0
check() { # name, expected (empty = no output, else a substring), plugin, project
  total=$((total+1)); out="$(bash "$hook" "$tmp/$3" "$tmp/$4" 2>&1)"; rc=$?
  if [ "$rc" -ne 0 ]; then echo "FAIL $1: exit $rc"; return; fi
  if [ -z "$2" ]; then [ -z "$out" ] && pass=$((pass+1)) || echo "FAIL $1: expected no output, got '$out'"
  else case "$out" in *"$2"*) pass=$((pass+1)) ;; *) echo "FAIL $1: expected '$2', got '$out'" ;; esac; fi
}
check "(a) no specflow.md" "" p010 empty
check "(b) same version" "" p010 proj
check "(c) minor behind" "update available" p020 proj
check "(d) major differs" "major version differs" p100 proj
check "(e) path with a space" "update available" p020 "my proj"
check "(f) plugin older than product" "update the plugin first" p010 ahead
echo "test-version-check: $pass/$total pass"
[ "$pass" -eq "$total" ]
