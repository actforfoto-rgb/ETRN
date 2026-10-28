#!/usr/bin/env bash
set -euo pipefail

rm -rf dist
mkdir -p dist

if [ ! -f website/site.zip ]; then
  echo "ERROR: website/site.zip not found"
  exit 1
fi

unzip -q -o website/site.zip -d dist

# Apply current approved business-process content before publishing.
python website/patch_current_content.py

# Text/code overrides maintained directly from ChatGPT/GitHub.
if [ -d website-overrides ]; then
  cp -R website-overrides/. dist/
fi

# Internal project files must never be published.
rm -f   dist/AUDIT_*.txt   dist/RC1_AUDIT_*.txt   dist/REVIEW_*.txt   dist/README.md   dist/README.txt

# Basic deploy guards.
test -f dist/index.html
test -f dist/app.js
test -f dist/data.js
test -f dist/styles.css
test -d dist/assets
grep -q 'Материалы актуализированы: 28.10.2026' dist/app.js
grep -q 'После подписания ЭЗЗ не появились ЭТрН' dist/data.js
grep -q 'система автоматически создаст ЭТрН' dist/data.js

echo "ETRN site prepared in dist/"
