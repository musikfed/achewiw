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
# systemctl --user link creates ~/.config/systemd/user/<name>.service -> 
# /1tbdisk/achewiw/ops/<name>.service. FragmentPath usually reports the
# LINK, not its original destination. Verify resolved target, not text equality.
# Never follow links outside this exact ACHEWIW service file.
fragment_target(){
  local fragment
  fragment="$(show FragmentPath)"
  [[ -n "$fragment" && -f "$fragment" ]] || return 1
  readlink -e -- "$fragment"
}
owned(){
  local resolved
  resolved="$(fragment_target)" || return 1
  [[ "$(show LoadState)" == loaded &&
     "$resolved" == "$FILE" &&
     "$(show WorkingDirectory)" == "$ROOT" &&
     "$(show ExecStart)" == *"$ROOT/scripts/achewiw_host_monitor.py"* ]]
}
guard_unit(){
  if [[ "$(show LoadState)" == loaded ]] && ! owned; then
    echo "ACHEWIW монитор: конфликт имени unit; не трогаем неподтверждённую службу." >&2
    echo "Файл systemd: $(show FragmentPath)" >&2
    echo "Исполняемый файл: $(show ExecStart)" >&2
    return 1
  fi
}
if [[ "$ACTION" == start ]]; then
  guard_unit || die "Не запускаем чужой или неподтверждённый unit"
fi
if [[ "$ACTION" == once ]]; then
  /usr/bin/python3 "$ROOT/scripts/achewiw_host_monitor.py" --once
elif [[ "$ACTION" == start ]]; then
  if [[ "$(show LoadState)" != loaded ]]; then
    systemctl --user link "$FILE" || die "Не удалось зарегистрировать свой unit"
    systemctl --user daemon-reload || die "Не удалось обновить systemd --user"
  fi
  if ! owned; then
    echo "ACHEWIW монитор: systemd FragmentPath=$(show FragmentPath)" >&2
    echo "ACHEWIW монитор: ожидаемая цель=$FILE" >&2
    die "Проверка источника unit, рабочей папки или ExecStart не прошла — службу не запускаем"
  fi
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
  if owned; then
    echo "ACHEWIW монитор: ActiveState=$(show ActiveState), unit=$UNIT (подтверждён)"
  else
    echo "ACHEWIW монитор: найден неподтверждённый unit $UNIT — ничего не трогаем"
    echo "ACHEWIW монитор: systemd FragmentPath=$(show FragmentPath)"
    echo "ACHEWIW монитор: исходник=$(fragment_target 2>/dev/null || echo недоступен)"
  fi
fi
