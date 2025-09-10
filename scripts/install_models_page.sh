#!/usr/bin/env bash
set -euo pipefail
# Simple install: ensure docs/ exists and place the page.
mkdir -p docs
# If running from the extracted package root, this file is already at docs/index_models_m2bin.html
# This step is a no-op but kept for clarity.
cp -f docs/index_models_m2bin.html docs/index_models_m2bin.html || true
echo "✓ Installed docs/index_models_m2bin.html"
echo "Open your page at: docs/index_models_m2bin.html (GitHub Pages URL)"
