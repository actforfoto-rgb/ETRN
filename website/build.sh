#!/usr/bin/env bash
set -euo pipefail

rm -rf dist
mkdir -p dist

if [ ! -f website/site.zip ]; then
  echo "ERROR: website/site.zip not found"
  exit 1
fi

unzip -q -o website/site.zip -d dist

# Text/code overrides maintained directly from ChatGPT/GitHub.
if [ -d website-overrides ]; then
  cp -R website-overrides/. dist/
fi

# Basic deploy guard.
test -f dist/index.html

echo "ETRN site prepared in dist/"
