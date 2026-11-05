#!/usr/bin/env bash
# Compila src/App.tsx -> index.html (un solo archivo para pegar en Apps Script)
set -euo pipefail
cd "$(dirname "$0")"
[ -d node_modules ] || npm install --no-audit --no-fund
npx esbuild src/App.tsx --bundle --minify --format=iife \
  --define:process.env.NODE_ENV='"production"' --outfile=build/app.js
npx tailwindcss -c tailwind.config.js -i src/input.css -o build/app.css --minify
node build-html.js
echo "Listo: index.html"
