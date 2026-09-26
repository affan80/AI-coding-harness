#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$repo_root"

echo "[ci] repository hygiene"

if git grep -nE '^(<<<<<<<|=======|>>>>>>>)' -- . ':!scripts/ci.sh'; then
  echo "[ci] unresolved merge-conflict marker found" >&2
  exit 1
fi

if find . \( -path './.git' -o -path './.venv' -o -path './venv' -o -path './node_modules' \) -prune -o -type f -size +10M -print | grep -q .; then
  echo "[ci] file larger than 10 MiB found; store generated artifacts outside git" >&2
  exit 1
fi

echo "[ci] markdown files"
while IFS= read -r file; do
  test -s "$file" || { echo "[ci] empty markdown file: $file" >&2; exit 1; }
done < <(find . -path './.git' -prune -o -type f -name '*.md' -print)

if [[ -f pyproject.toml || -f requirements.txt || -d harness ]]; then
  echo "[ci] python"
  python_targets=()
  [[ -d harness ]] && python_targets+=(harness)
  [[ -d tests ]] && python_targets+=(tests)
  if ((${#python_targets[@]})); then
    python3 -m compileall -q "${python_targets[@]}"
  fi

  if [[ -d tests ]] && python3 -c 'import pytest' >/dev/null 2>&1; then
    python3 -m pytest -q
  fi

  if command -v ruff >/dev/null 2>&1; then
    ruff check .
  fi
fi

if [[ -f package.json ]]; then
  echo "[ci] node"
  if [[ -f package-lock.json ]]; then
    npm ci
  fi

  if node -e 'const p=require("./package.json"); process.exit(p.scripts?.test ? 0 : 1)'; then
    npm test
  fi

  if node -e 'const p=require("./package.json"); process.exit(p.scripts?.build ? 0 : 1)'; then
    npm run build
  fi
fi

echo "[ci] passed"
