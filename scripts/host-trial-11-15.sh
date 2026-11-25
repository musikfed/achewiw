#!/usr/bin/env bash
# ACHEWIW host trial: guarded code-only upgrade + real-container smoke + rollback.
# Run ONLY as the ordinary host user after reviewing read-only baseline.
# Usage: bash host-trial-11-15.sh preview|apply
# Does not run under GitHub hosted runners; never manages Ollama or other projects.
set -Eeuo pipefail
ROOT=/1tbdisk/achewiw
TARGET_BRANCH=feature/final-print-gate-step15-20261125
ACTION="${1:-preview}"
[[ $# -le 1 && ( "$ACTION" == preview || "$ACTION" == apply ) ]] || { echo "Use preview|apply"; exit 2; }
die(){ echo "BLOCKED: $*" >&2; exit 2; }
[[ "$(id -u)" -ne 0 ]] || die "Never execute as root"
[[ -d "$ROOT/.git" && ! -L "$ROOT" && -f "$ROOT/compose.yaml" && ! -L "$ROOT/compose.yaml" && -d "$ROOT/data" && ! -L "$ROOT/data" ]] || die "Unexpected or symlink workspace"
cd "$ROOT"
[[ "$(git rev-parse --show-toplevel)" == "$ROOT" ]] || die "Unexpected checkout"
case "$(git remote get-url origin)" in
  https://github.com/musikfed/achewiw|https://github.com/musikfed/achewiw.git|git@github.com:musikfed/achewiw|git@github.com:musikfed/achewiw.git) ;;
  *) die "Unknown repository origin";;
esac
command -v docker >/dev/null && docker info >/dev/null 2>&1 || die "Docker unavailable"
command -v curl >/dev/null || die "curl required"
[[ -z "$(git status --porcelain --untracked-files=normal)" ]] || {
 git status --short | head -30
 die "Local files modified; do not reset/clean"
}
CURRENT="$(git rev-parse HEAD)"
BRANCH="$(git symbolic-ref -q --short HEAD || echo detached)"
RUNNING="$(docker ps --no-trunc --filter label=com.docker.compose.project=achewiw --filter label=com.docker.compose.service=studio --format '{{.ID}}')"
[[ "$(printf '%s\n' "$RUNNING" | grep -c . || true)" -eq 1 ]] || die "Expected exactly one running ACHEWIW Studio"
LABELS="$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project"}}|{{index .Config.Labels "com.docker.compose.service"}}|{{index .Config.Labels "com.docker.compose.project.working_dir"}}|{{range .Mounts}}{{if eq .Destination "/data"}}{{.Source}}{{end}}{{end}}' "$RUNNING")"
[[ "$LABELS" == "achewiw|studio|$ROOT|$ROOT/data" ]] || die "Studio ownership/mount not verified"
command -v python3 >/dev/null || die "Python required"
python3 - "$ROOT/data/achewiw.sqlite3" <<'PY' || die "SQLite cannot be checked or busy jobs exist"
import sqlite3,sys,time
from pathlib import Path
p=Path(sys.argv[1])
if not p.is_file() or p.is_symlink():raise SystemExit("Canonical SQLite missing")
with sqlite3.connect(p.as_uri()+"?mode=ro",uri=True,timeout=6) as db:
 names={x[0] for x in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
 if "ai_jobs" not in names:raise SystemExit("No ai_jobs table")
 n=db.execute("SELECT count(*) FROM ai_jobs WHERE status IN ('queued','running')").fetchone()[0]
 if "production_jobs" in names:
  n+=db.execute("SELECT count(*) FROM production_jobs WHERE state IN ('queued','running')").fetchone()[0]
 if "achewiw_gpu_lease" in names:
  n+=db.execute("SELECT count(*) FROM achewiw_gpu_lease WHERE expires_at>?",(time.time(),)).fetchone()[0]
 print("Active book/production/GPU lease:",n)
 if n:raise SystemExit("Active job: deployment refused")
PY
free_kb="$(df -Pk / | awk 'END{print $4}')"
[[ "$free_kb" =~ ^[0-9]+$ && "$free_kb" -ge 4194304 ]] || die "Need >= 4 GiB free on root before Docker build"
free_data="$(df -Pk "$ROOT" | awk 'END{print $4}')"
[[ "$free_data" =~ ^[0-9]+$ && "$free_data" -ge 2097152 ]] || die "Need >= 2 GiB free on data filesystem"
echo "Host trial: current=$CURRENT branch=$BRANCH; disk space adequate; jobs idle."
if [[ "$ACTION" == preview ]]; then
 echo "PREVIEW ONLY. No fetch/checkout/build/restart/DB changes."
 echo "Verified currently running Studio: ${RUNNING:0:12}"
 echo "For apply: fetch origin/$TARGET_BRANCH and execute this script with apply."
 exit 0
fi
# Get the verified GitHub branch; no reset, no merge into main.
git fetch --no-tags origin "+refs/heads/$TARGET_BRANCH:refs/remotes/origin/$TARGET_BRANCH"
TARGET="$(git rev-parse "refs/remotes/origin/$TARGET_BRANCH")"
for path in scripts/host-smoke-11-15.py app/print_release_gate.py app/page_fit_editorial_api.py app/semantic_page_fit_api.py; do
 git cat-file -e "$TARGET:$path" || die "Missing target component: $path"
done
[[ "$TARGET" != "$CURRENT" ]] || die "Already on the target commit; use read-only host smoke"
# SQLite online backup; protected under the existing project data subtree.
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP="$ROOT/data/backups/host-trial-$STAMP"
mkdir -m 700 -p "$BACKUP"
python3 - "$ROOT/data" "$BACKUP" <<'PY'
import sys,sqlite3
from pathlib import Path
src,dst=map(Path,sys.argv[1:])
for basename in ("achewiw.sqlite3","work.sqlite3"):
 original=src/basename
 if not original.exists():continue
 if original.is_symlink() or not original.is_file():raise SystemExit("Unsafe DB "+basename)
 backup=dst/basename
 with sqlite3.connect(original.as_uri()+"?mode=ro",uri=True,timeout=20) as a:
  with sqlite3.connect(backup) as b:a.backup(b)
 backup.chmod(0o600)
 print("PASS SQLite online backup:",basename,backup.stat().st_size)
PY
printf '%s\n' "$CURRENT" > "$BACKUP/pre-deploy-commit.txt"
printf '%s\n' "$BRANCH" > "$BACKUP/pre-deploy-branch.txt"
chmod 600 "$BACKUP/"*.txt
SWITCHED=0
SUCCESS=0
recover(){
 status=$?
 trap - EXIT
 if [[ "$SUCCESS" == 1 ]]; then
  echo "HOST TRIAL PASS: current code=$TARGET, verified on the real Studio."
  echo "Backup path=$BACKUP"
  exit 0
 fi
 echo "HOST TRIAL FAILURE stage/status=$status. Attempting code rollback only." >&2
 if [[ "$SWITCHED" == 1 ]]; then
  if git switch --detach "$CURRENT" && bash scripts/achewiw-control.sh rebuild;then
   echo "Previous Studio code restored and rebuilt. Verify health. DB backup=$BACKUP"
  else
   echo "AUTOMATIC CODE ROLLBACK FAILED. DB backup remains: $BACKUP" >&2
  fi
 fi
 echo "No automatic DB restore (would risk overwriting legitimate concurrent writes)." >&2
 exit 1
}
trap recover EXIT
SWITCHED=1
git switch --detach "$TARGET"
python3 scripts/verify-versioned-assets.py
bash -n scripts/achewiw-control.sh
# Changes only owner-verified Studio; does not manage other projects.
bash scripts/achewiw-control.sh rebuild
ID="$(docker ps --no-trunc --filter label=com.docker.compose.project=achewiw --filter label=com.docker.compose.service=studio --format '{{.ID}}')"
[[ "$(printf '%s\n' "$ID" | grep -c . || true)" -eq 1 ]] || die "Studio did not restart as a unique container"
curl --noproxy '*' -fsS --max-time 6 -H 'Host: 192.168.0.100:7080' http://127.0.0.1:7080/api/health >/dev/null || die "New Studio health failed"
docker exec -i -w /app "$ID" python - < "$ROOT/scripts/host-smoke-11-15.py" || die "New Studio functional smoke failed"
bash scripts/achewiw-control.sh auth-check
SUCCESS=1
