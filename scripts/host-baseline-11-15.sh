#!/usr/bin/env bash
# ACHEWIW current-host read-only baseline. No checkout, rebuild, restart or data edits.
# Designed to execute from an existing repository tree at /1tbdisk/achewiw.
set -Eeuo pipefail
ROOT=/1tbdisk/achewiw
REPORT="/tmp/achewiw-host-baseline-$(date +%Y%m%d-%H%M%S)-$$.txt"
umask 077
: > "$REPORT"
exec > >(tee "$REPORT") 2>&1
fail=0
pass(){ printf 'PASS %s\n' "$*"; }
warn(){ printf 'WARN %s\n' "$*"; }
bad(){ printf 'FAIL %s\n' "$*"; fail=$((fail+1)); }
echo "ACHEWIW HOST BASELINE: $(date -Is)"
echo "Report: $REPORT"
[[ "$(id -u)" -ne 0 ]] || { bad "Do not run as root"; exit 2; }
[[ -d "$ROOT/.git" && ! -L "$ROOT" && -d "$ROOT/data" && ! -L "$ROOT/data" ]] || { bad "Missing or unsafe workspace"; exit 2; }
cd "$ROOT"
[[ "$(git rev-parse --show-toplevel)" == "$ROOT" ]] || { bad "Wrong git root"; exit 2; }
origin="$(git remote get-url origin)"
case "$origin" in
  https://github.com/musikfed/achewiw|https://github.com/musikfed/achewiw.git|git@github.com:musikfed/achewiw|git@github.com:musikfed/achewiw.git) pass "Repository origin verified";;
  *) bad "Unexpected repository origin"; exit 2;;
esac
echo "Current branch: $(git symbolic-ref --short -q HEAD || echo detached)"
echo "Current commit: $(git rev-parse --short=12 HEAD)"
if [[ -z "$(git status --porcelain --untracked-files=normal)" ]]; then pass "Clean tracked working tree"
else warn "Local changes exist; DO NOT switch branches or update until inspected"; git status --short | head -30; fi
if command -v df >/dev/null; then df -h / "$ROOT" | tail -n +2; fi
if command -v python3 >/dev/null; then python3 --version; fi
if command -v docker >/dev/null && docker info >/dev/null 2>&1; then
  pass "Docker accessible"
  ids="$(docker ps -q --no-trunc --filter 'label=com.docker.compose.project=achewiw' --filter 'label=com.docker.compose.service=studio')"
  if [[ "$(echo "$ids" | grep -c . || true)" -ne 1 ]]; then
    bad "Expected exactly one running ACHEWIW Studio container; observed $(echo "$ids" | grep -c . || true)"
    ids=""
  fi
  if [[ -n "$ids" ]]; then
    info="$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project"}}|{{index .Config.Labels "com.docker.compose.service"}}|{{index .Config.Labels "com.docker.compose.project.working_dir"}}|{{range .Mounts}}{{if eq .Destination "/data"}}{{.Source}}{{end}}{{end}}' "$ids")"
    if [[ "$info" == "achewiw|studio|$ROOT|$ROOT/data" ]]; then
      pass "Studio owns expected workspace and persistent data mount"
      echo "Studio container image: $(docker inspect --format '{{.Image}}' "$ids" | cut -c1-18)"
      echo "Studio health: $(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}not-configured{{end}}' "$ids")"
      docker exec "$ids" python -c "import fastapi,pydantic,fitz,reportlab; print('PASS Studio Python libraries') " || bad "Studio missing PDF dependencies"
      docker exec -i "$ids" python - <<'PY' || bad "Container module import failed"
import sys
sys.path.insert(0,"/app")
names=["chat_api","chat_outline_builder","semantic_page_fit","page_fit_editorial_api","print_proof_preflight","print_release_gate"]
for name in names:
 try:
  __import__(name)
  print("PASS import",name)
 except Exception as exc:
  print("WARN import",name,type(exc).__name__)
PY
    else bad "Studio container ownership / data mount differs; refusing further container probes"
    fi
  fi
else bad "Docker unavailable"; fi
echo "--- HTTP local service ---"
if curl --noproxy '*' --silent --show-error --max-time 5 -o /tmp/achewiw-baseline-health-$$ \
 -w 'http=%{http_code}\n' 'http://127.0.0.1:7080/api/health'; then
  python3 - "/tmp/achewiw-baseline-health-$$" <<'PY'
import json,sys
try:
 d=json.load(open(sys.argv[1]))
 print("Health status:",d.get("status"))
 if d.get("status")!="ok":raise ValueError("not ok")
except Exception as exc:print("WARN health response",type(exc).__name__)
PY
  rm -f "/tmp/achewiw-baseline-health-$$"
else bad "HTTP /api/health not available on localhost 7080"; fi
echo "--- SQLite read-only, no book contents ---"
python3 - "$ROOT/data/achewiw.sqlite3" <<'PY' || true
import sqlite3,sys,os
from pathlib import Path
p=Path(sys.argv[1])
if not p.is_file() or p.is_symlink():
 print("FAIL canonical SQLite absent or symlink");raise SystemExit(2)
with sqlite3.connect(p.as_uri()+"?mode=ro",uri=True,timeout=8) as db:
 tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
 for name in ("projects","ai_jobs","ai_book_plans","ai_page_fit_suggestions","ai_page_fit_reviews"):
  print(("PASS" if name in tables else "WARN"),"table",name)
 for name,col in (("ai_jobs","status"),("production_jobs","state")):
  if name in tables:
   try:
    rows=db.execute("SELECT "+col+",count(*) FROM "+name+" GROUP BY "+col).fetchall()
    print("Job states",name,dict(rows))
   except sqlite3.Error as exc:print("WARN jobs state unavailable",name,type(exc).__name__)
 print("SQLite quick_check:",db.execute("PRAGMA quick_check(1)").fetchone()[0])
PY
echo "--- GPU and Ollama ---"
if command -v nvidia-smi >/dev/null; then
 nvidia-smi --query-gpu=name,memory.total,memory.used --format=csv,noheader | head -3
else warn "nvidia-smi not found"; fi
if curl --noproxy '*' -fsS --max-time 5 'http://127.0.0.1:11434/api/tags' >/dev/null 2>&1;then
 pass "Ollama API responds locally"
else warn "Ollama may be remote/container-only; inspect application configuration, not a failure"; fi
if [[ -x /usr/bin/libreoffice ]] || command -v libreoffice >/dev/null;then
 pass "LibreOffice executable exists on host"
else warn "LibreOffice not installed on host; office preflight cannot run here yet"; fi
echo "--- Live Studio synthetic smoke (read-only) ---"
if [[ -n "${ids:-}" && "${info:-}" == "achewiw|studio|$ROOT|$ROOT/data" ]]; then
  if git cat-file -e refs/remotes/origin/feature/final-print-gate-step15-20261125:scripts/host-smoke-11-15.py 2>/dev/null; then
    if git show refs/remotes/origin/feature/final-print-gate-step15-20261125:scripts/host-smoke-11-15.py |
       docker exec -i -w /app "$ids" python -; then
      pass "Full real-container synthetic smoke"
    else
      bad "Full real-container synthetic smoke failed: current running image may be older"
    fi
  else
    warn "Origin branch not fetched; fetch origin/feature/final-print-gate-step15-20261125 to enable smoke"
  fi
else
  warn "Studio ownership was not verified; no code injected into container"
fi
echo "=== HOST BASELINE FINISHED: $fail hard failure(s) ==="
echo "Only report this summary; NEVER share .env, tokens, database or unrestricted logs."
exit "$((fail>0))"
