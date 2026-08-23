#!/bin/sh
set -eu

forbidden_paths="$({
  git ls-files |
    grep -E '(^|/)(CLAUDE|AGENTS|GEMINI)\.md$|(^|/)\.(claude|cursor|codex|agents|opencode|captain|continue|windsurf|codegraph)(/|$)|(^|/)(spec|openspec)(/|$)|(^|/)\.aider|(^|/)\.env($|\.)|(^|/)(id_rsa|id_ed25519)|\.(pem|key|p12|pfx|jks|keystore)$|(^|/)(credentials|service-account[^/]*)\.json$|^data/knowledge_packs\.json$' |
    grep -Ev '(^|/)\.env\.(example|sample|template)$' || true
})"

failed=0

if [ -n "$forbidden_paths" ]; then
  echo "[repository-hygiene] Refusing to publish local-only or credential files:" >&2
  printf '  %s\n' "$forbidden_paths" >&2
  failed=1
fi

suspicious_content="$(
  git grep -Il -E -e "-----BEGIN (RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----|AKIA[0-9A-Z]{16}|ASIA[0-9A-Z]{16}|github_pat_[A-Za-z0-9_]{20,}|gh[opusr]_[A-Za-z0-9]{20,}|sk-(proj-)?[A-Za-z0-9_-]{20,}|xox[baprs]-[A-Za-z0-9-]{10,}|AIza[0-9A-Za-z_-]{30,}|(sk|rk)_live_[0-9A-Za-z]{16,}|(api[_-]?key|access[_-]?token|auth[_-]?token)[[:space:]]*[:=][[:space:]]*[\"'][A-Za-z0-9_./+=-]{24,}[\"']" HEAD -- \
    ':!*.lock' ':!frontend/package-lock.json' 2>/dev/null || true
)"

if [ -n "$suspicious_content" ]; then
  echo "[repository-hygiene] Refusing to publish likely credentials in:" >&2
  printf '  %s\n' "$suspicious_content" >&2
  failed=1
fi

for tainted_commit in \
  af28c05bb14210dca65eef642a9958985236fc08 \
  2eaf65d3aadfd5645fe9a89b807a317622c74530
do
  if git cat-file -e "${tainted_commit}^{commit}" 2>/dev/null && \
    git merge-base --is-ancestor "$tainted_commit" HEAD
  then
    echo "[repository-hygiene] Refusing to reintroduce tainted history: $tainted_commit" >&2
    failed=1
  fi
done

if [ "$failed" -ne 0 ]; then
  echo "Remove the flagged content or rebase onto the rewritten main before pushing." >&2
  exit 1
fi

echo "[repository-hygiene] OK."
