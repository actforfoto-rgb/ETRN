#!/usr/bin/env bash
set -euo pipefail

rm -rf dist
mkdir -p dist

if [ ! -f website/site.zip ]; then
  echo "ERROR: website/site.zip not found"
  exit 1
fi

unzip -q -o website/site.zip -d dist

# Remove internal review/audit files that must not be published.
rm -f   dist/AUDIT_*.txt   dist/RC1_AUDIT_*.txt   dist/REVIEW_*.txt   dist/README.md   dist/README.txt

# Text/code overrides maintained directly from ChatGPT/GitHub.
if [ -d website-overrides ]; then
  cp -R website-overrides/. dist/
fi

# Basic deploy guards.
test -f dist/index.html
test -f dist/app.js
test -f dist/data.js
test -f dist/styles.css
test -d dist/assets

echo "ETRN site prepared in dist/"
