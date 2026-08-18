#!/bin/sh
set -eu

forbidden_paths="$({
  git ls-files |
    grep -E '(^|/)(CLAUDE|AGENTS|GEMINI)\.md$|(^|/)\.(claude|cursor|codex|agents|opencode|captain|continue|windsurf|codegraph)(/|$)|(^|/)openspec(/|$)|(^|/)\.aider|(^|/)\.env($|\.)|(^|/)(id_rsa|id_ed25519)|\.(pem|key|p12|pfx|jks|keystore)$|(^|/)(credentials|service-account[^/]*)\.json$' |
    grep -Ev '(^|/)\.env\.(example|sample|template)$' || true
})"

if [ -z "$forbidden_paths" ]; then
  echo "[repository-hygiene] OK."
  exit 0
fi

echo "[repository-hygiene] Refusing to publish local-only or credential files:" >&2
printf '  %s\n' "$forbidden_paths" >&2
echo "Remove the files from Git tracking before pushing." >&2
exit 1
