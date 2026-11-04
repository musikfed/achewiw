#!/usr/bin/env bash
# Separate collector name avoids modifying pre-existing unknown user units.
set -euo pipefail
ROOT=/1tbdisk/achewiw
UNIT=achewiw-metrics-v2.service
FILE="$ROOT/ops/$UNIT"
SNAP="$ROOT/data/monitor/host.json"
ACTION="${1:-status}"
die(){ echo "ACHEWIW монитор: $*" >&2; exit 1; }
[[ "$ACTION" == status || "$ACTION" == start || "$ACTION" == once ]] || die "Использование: start | status | once"
[[ -d "$ROOT" && ! -L "$ROOT" && -f "$FILE" && ! -L "$FILE" ]] || die "Нет своего исходного unit"
[[ -d "$ROOT/data" && ! -L "$ROOT/data" ]] || die "Нет безопасного data/"
[[ -f "$ROOT/scripts/achewiw_host_monitor.py" && ! -L "$ROOT/scripts/achewiw_host_monitor.py" ]] || die "Нет сборщика"
[[ "$(id -u)" -ne 0 ]] || die "Не запускать от root: использовать обычного пользователя Ubuntu"
command -v systemctl >/dev/null || die "systemctl отсутствует"
show(){ systemctl --user show "$UNIT" -p "$1" --value 2>/dev/null || true; }
owned(){
  [[ "$(show LoadState)" == loaded &&
     "$(show FragmentPath)" == "$FILE" &&
     "$(show WorkingDirectory)" == "$ROOT" &&
     "$(show ExecStart)" == *"$ROOT/scripts/achewiw_host_monitor.py"* ]]
}
if [[ "$(show LoadState)" == loaded ]] && ! owned; then
  die "Одноимённый unit уже занят и не совпадает с ACHEWIW — чужие службы не трогаем"
fi
if [[ "$ACTION" == once ]]; then
  /usr/bin/python3 "$ROOT/scripts/achewiw_host_monitor.py" --once
elif [[ "$ACTION" == start ]]; then
  if [[ "$(show LoadState)" != loaded ]]; then
    systemctl --user link "$FILE" || die "Не удалось зарегистрировать свой unit"
    systemctl --user daemon-reload || die "Не удалось обновить systemd --user"
  fi
  owned || die "Unit после регистрации отличается от исходного файла"
  systemctl --user start "$UNIT" || die "Не запустился хостовый сборщик"
  systemctl --user --no-pager status "$UNIT" || die "Служба неактивна"
fi
if [[ -f "$SNAP" && ! -L "$SNAP" ]]; then
  age=$(( $(date +%s) - $(stat -c %Y "$SNAP") ))
  echo "ACHEWIW монитор: снимок $age сек. назад"
else
  echo "ACHEWIW монитор: нет host.json; запусти start или once"
fi
if [[ "$(show LoadState)" == loaded ]]; then
  echo "ACHEWIW монитор: ActiveState=$(show ActiveState), unit=$UNIT"
fi
