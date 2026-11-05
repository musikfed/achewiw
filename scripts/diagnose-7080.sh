#!/usr/bin/env bash
# Read-only diagnostics: who owns ACHEWIW port 7080? Never stop anything.
set -euo pipefail
ROOT="/1tbdisk/achewiw"
cd "$ROOT" || exit 1
echo "=== ACHEWIW checkout (no changes) ==="
git status -sb 2>/dev/null || true
git log -1 --oneline 2>/dev/null || true
echo
echo "=== TCP listener port 7080 ==="
if command -v ss >/dev/null 2>&1; then
  ss -ltnp '( sport = :7080 )' || true
else
  echo "ss not installed"
fi
echo
echo "=== Containers by ACHEWIW Compose label ==="
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  docker ps -a --no-trunc --filter label=com.docker.compose.project=achewiw \
    --format 'ID={{.ID}} NAME={{.Names}} STATUS={{.Status}} IMAGE={{.Image}}'
  echo "=== Container currently named achewiw-studio ==="
  docker ps -a --no-trunc --filter 'name=^/achewiw-studio$' \
    --format 'ID={{.ID}} NAME={{.Names}} STATUS={{.Status}} IMAGE={{.Image}}'
  ids="$(docker ps -a --no-trunc --filter label=com.docker.compose.project=achewiw --format '{{.ID}}')"
  while IFS= read -r id; do
    [[ -n "$id" ]] || continue
    echo "=== ACHEWIW candidate $id: ownership metadata only ==="
    docker inspect --format 'compose={{index .Config.Labels "com.docker.compose.project"}}; service={{index .Config.Labels "com.docker.compose.service"}}; workdir={{index .Config.Labels "com.docker.compose.project.working_dir"}}; managed={{index .Config.Labels "org.achewiw.lifecycle"}}; workspace={{index .Config.Labels "org.achewiw.workspace"}}; network={{.HostConfig.NetworkMode}}' "$id" || true
    docker inspect --format '{{range .Mounts}}{{if eq .Destination "/data"}}data_mount={{.Source}}{{end}}{{end}}' "$id" || true
  done <<< "$ids"
else
  echo "Нет доступа к Docker (без остановки служб)"
fi
echo
echo "=== HTTP: no login/password sent ==="
for uri in http://127.0.0.1:7080/ http://192.168.0.100:7080/; do
  echo "$uri"
  if ! command -v curl >/dev/null 2>&1; then
    echo "curl unavailable"
    break
  fi
  curl -sS --max-time 5 --noproxy '*' -o /dev/null \
    --header 'Host: 192.168.0.100:7080' \
    -w 'HTTP=%{http_code}\n' "$uri" || true
  # Only print diagnostics headers, never Set-Cookie or application data.
  curl -sS --max-time 5 --noproxy '*' -D - -o /dev/null \
    --header 'Host: 192.168.0.100:7080' "$uri" 2>/dev/null |
    grep -Ei '^(HTTP/|www-authenticate:|x-achewiw-app:|x-achewiw-auth:|server:)' || true
done
echo
echo "=== End of read-only port 7080 diagnosis ==="
echo "Do not kill a PID or container unless exact ownership is confirmed."
