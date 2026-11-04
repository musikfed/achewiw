#!/usr/bin/env bash
# ACHEWIW-only SSH deployment and optional reset of obsolete literary projects.
# YadroKreator, Ollama, other Docker projects and SD camera remain untouched.
set -euo pipefail
umask 077
ROOT=/1tbdisk/achewiw
MODE=--check
RESET=false
NOCACHE=false
if (( $# > 0 )); then MODE="$1"; fi
shift $(( $# > 0 ? 1 : 0 ))
for arg in "$@"; do
  case "$arg" in
    --reset-library) RESET=true ;;
    --no-cache) NOCACHE=true ;;
    *) echo "Usage: bash scripts/rebuild-library.sh [--check|--apply] [--reset-library] [--no-cache]" >&2; exit 2 ;;
  esac
done
[[ "$MODE" == --check || "$MODE" == --apply ]] || {
  echo "Usage: bash scripts/rebuild-library.sh [--check|--apply] [--reset-library] [--no-cache]" >&2; exit 2;
}
[[ "$(id -u)" != 0 ]] || { echo "Use the unprivileged server user" >&2; exit 2; }
[[ -d "$ROOT/.git" && ! -L "$ROOT" ]] || { echo "ACHEWIW checkout missing" >&2; exit 3; }
cd "$ROOT"
[[ "$(git rev-parse --show-toplevel)" == "$ROOT" ]] || { echo "Wrong repository" >&2; exit 3; }
[[ "$(git branch --show-current)" == feature/technical-epub-first ]] || {
  echo "Expected ACHEWIW feature/technical-epub-first; main lacks the running Studio" >&2; exit 3;
}
case "$(git remote get-url origin)" in
  https://github.com/musikfed/achewiw|https://github.com/musikfed/achewiw.git|git@github.com:musikfed/achewiw|git@github.com:musikfed/achewiw.git) ;;
  *) echo "Untrusted Git origin" >&2; exit 3 ;;
esac
[[ -z "$(git status --porcelain)" ]] || {
  echo "Local changes present, refusing reset/clean" >&2
  git status --short
  exit 3
}
[[ -f scripts/library_admin.py && -f compose.yaml && -f scripts/achewiw-control.sh ]] || {
  echo "ACHEWIW control/library tools missing" >&2; exit 3;
}
if [[ "$MODE" == --check ]]; then
  echo "DRY RUN: only ACHEWIW status and the titles/IDs of projects scheduled for reset."
  bash scripts/achewiw-control.sh status
  if [[ -f data/achewiw.sqlite3 ]]; then
    python3 scripts/library_admin.py --list
  else
    echo "ACHEWIW SQLite not created; no books/stories to remove."
  fi
  if [[ "$RESET" == true ]]; then echo "RESET REQUESTED: only ACHEWIW projects, ai_jobs, ai_job_reviews (after backup)."; fi
  if [[ "$NOCACHE" == true ]]; then echo "NO-CACHE BUILD REQUESTED: own Compose studio only, without global prune."; fi
  exit 0
fi

echo "Step 1: stop ONLY managed ACHEWIW services/containers"
bash scripts/achewiw-control.sh stop
echo "Step 2: Git fast-forward update while Studio is stopped"
bash scripts/achewiw-control.sh update
if [[ "$RESET" == true ]]; then
  echo "Step 3: backup and clear ONLY ACHEWIW literary SQLite library"
  if [[ -f data/achewiw.sqlite3 ]]; then
    python3 scripts/library_admin.py --reset-all --confirm DELETE_ALL_ACHEWIW_LIBRARY
  else
    echo "No ACHEWIW library present; no database deleted."
  fi
else
  echo "Step 3: old books preserved (pass --reset-library to clear them after backup)"
fi
if [[ "$NOCACHE" == true ]]; then
  echo "Step 4: no-cache image build for exact ACHEWIW studio service only"
  [[ "$(docker compose --project-name achewiw --project-directory "$ROOT" --file "$ROOT/compose.yaml" config --services)" == studio ]] || {
    echo "Unexpected Compose services: refused" >&2; exit 3;
  }
  # No daemon-wide cache deletion (shared with other projects).
  docker compose --project-name achewiw --project-directory "$ROOT" --file "$ROOT/compose.yaml" build --no-cache studio
else
  echo "Step 4: reuse unchanged Docker layers"
fi
echo "Step 5: start/rebuild only the ACHEWIW Studio container"
bash scripts/achewiw-control.sh start
bash scripts/achewiw-control.sh auth-check
bash scripts/achewiw-control.sh status
echo "Finished. Factory databases, camera SD images, Ollama, other containers and Docker daemon untouched."
