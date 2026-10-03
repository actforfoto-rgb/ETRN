#!/usr/bin/env bash
set -euo pipefail

rm -rf dist
mkdir -p dist

if [ ! -f website/site.zip ]; then
  echo "ERROR: website/site.zip not found"
  exit 1
fi

unzip -q -o website/site.zip -d dist

# Актуальная дата хранится прямо в основном app.js.
# Никакого DOM-наблюдателя для обновления даты на клиенте.
sed -i 's/Материалы актуализированы: 25\.09\.2026/Материалы актуализированы: 03.10.2026/g' dist/app.js

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
grep -q 'Материалы актуализированы: 03.10.2026' dist/app.js

echo "ETRN site prepared in dist/"
