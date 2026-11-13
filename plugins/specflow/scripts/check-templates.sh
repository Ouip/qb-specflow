#!/usr/bin/env bash
# Template self-check: all 17 present, the three header lines of specflow.md, at least one [FILL] in each fill-in template (settings.project.json and components.json are pre-filled).
set -u
t="$(cd "$(dirname "$0")/../templates" 2>/dev/null && pwd)" || t="$(dirname "$0")/../templates"
files="specflow.md settings.project.json workflow.json components.json CLAUDE.docs.md CLAUDE.code.md AGENTS.md spec.md scenarios.md couplings.md deferred.md decisions.md gates-ext.md manual-acceptance.md reviews/g2.md reviews/g5.md reviews/gt.md"
bad=0
for f in $files; do
  if [ ! -f "$t/$f" ]; then echo "missing: templates/$f"; bad=1; continue; fi
  case "$f" in manual-acceptance.md|settings.project.json|components.json|reviews/*) ;; *)
    grep -q '\[FILL' "$t/$f" || { echo "no [FILL]: templates/$f"; bad=1; } ;;
  esac
done
if [ -f "$t/specflow.md" ]; then
  head -5 "$t/specflow.md" | grep -qx 'format: 2' || { echo "specflow.md header lacks format: 2"; bad=1; }
  head -5 "$t/specflow.md" | grep -qE '^core: [0-9]+\.[0-9]+\.[0-9]+$' || { echo "specflow.md header lacks core:"; bad=1; }
  head -5 "$t/specflow.md" | grep -qE '^python: .+' || { echo "specflow.md header lacks python:"; bad=1; }
fi
[ "$bad" -eq 0 ] && echo "check-templates: ok"
exit "$bad"
