#!/usr/bin/env bash
# Explicit opt-in ComfyUI runtime for ACHEWIW / RTX3060 / NVIDIA550 / CUDA12.4.
# No sudo, no driver changes, no Docker or Ollama changes, no global cleanup.
set -euo pipefail
ROOT=/1tbdisk/achewiw
BASE="$ROOT/data/comfyui-runtime"
SRC="$BASE/src"
PY="$BASE/venv/bin/python"
PID="$BASE/comfyui.pid"
LOG="$BASE/comfyui.log"
MODEL="$SRC/models/checkpoints/sd_xl_base_1.0.safetensors"
SHA=31e35c80fc4829d14f90153f4c74cd59c90b779f6afe05a74cd6120b893f7e5b
UPSTREAM=c5de4955bb91a2b136027a698aaecb8d19e3d892
URL=https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0/resolve/main/sd_xl_base_1.0.safetensors
ACTION=doctor
if [[ $# -gt 0 ]]; then ACTION="$1"; fi
die(){ echo "ACHEWIW ComfyUI: ОСТАНОВЛЕНО — $*" >&2; exit 1; }
msg(){ echo "ACHEWIW ComfyUI: $*"; }
[[ -d "$ROOT" && ! -L "$ROOT" && -d "$ROOT/data" && ! -L "$ROOT/data" ]] || die "Недопустимое расположение ACHEWIW"
for path in "$BASE" "$SRC" "$BASE/venv"; do
  [[ ! -L "$path" ]] || die "Символическая ссылка в пути: $path"
done
own_pid(){
  [[ -f "$PID" && ! -L "$PID" ]] || return 1
  local pid commandline cwd
  pid="$(cat "$PID")"
  [[ "$pid" =~ ^[0-9]+$ && -d "/proc/$pid" ]] || return 1
  cwd="$(readlink -e "/proc/$pid/cwd" 2>/dev/null || :)" 
  commandline="$(ps -p "$pid" -o args= 2>/dev/null || :)" 
  [[ "$cwd" == "$SRC" && "$commandline" == *"$SRC/main.py"* &&
     "$commandline" == *"--listen 127.0.0.1"* && "$commandline" == *"--port 8188"* ]] || return 1
  echo "$pid"
}
port_busy(){ ss -ltnH '( sport = :8188 )' 2>/dev/null | grep -q .; }
responding(){ curl -fsS --noproxy '*' --max-time 2 http://127.0.0.1:8188/object_info/CheckpointLoaderSimple >/dev/null 2>&1; }
case "$ACTION" in
  doctor|status)
    nvidia-smi --query-gpu=name,driver_version,memory.total,memory.used,utilization.gpu --format=csv,noheader || :
    if command -v ollama >/dev/null; then ollama ps || :; fi
    [[ -d "$SRC/.git" ]] && msg "ComfyUI установлен в $SRC" || msg "В каталоге ACHEWIW нет ComfyUI"
    [[ -f "$MODEL" ]] && msg "SDXL присутствует" || msg "SDXL ещё не загружен"
    if responding; then msg "API ComfyUI на localhost:8188 отвечает"
    elif port_busy; then msg "Порт 8188 занят, процесс не идентифицирован"
    else msg "API ComfyUI не отвечает"; fi
    if p="$(own_pid)"; then msg "Наш ComfyUI работает PID=$p"; fi
    ;;
  install)
    command -v uv >/dev/null || die "На Ubuntu должен быть uv"
    command -v python3.12 >/dev/null || die "Нужен python3.12"
    command -v nvidia-smi >/dev/null || die "Нет драйвера NVIDIA"
    mkdir -p "$BASE"
    if [[ ! -e "$SRC" ]]; then
      git clone --depth 1 --branch v0.3.44 https://github.com/Comfy-Org/ComfyUI.git "$SRC"
    fi
    [[ -d "$SRC/.git" ]] || die "В каталоге src находится не ComfyUI"
    [[ "$(git -C "$SRC" rev-parse HEAD)" == "$UPSTREAM" ]] ||
      die "Неподтверждённая версия исходников ComfyUI, ничего не обновляем"
    if [[ ! -x "$PY" ]]; then uv venv --python python3.12 "$BASE/venv"; fi
    uv pip install --python "$PY" --index-url https://download.pytorch.org/whl/cu124 \
      'torch==2.6.0' 'torchvision==0.21.0' 'torchaudio==2.6.0'
    uv pip install --python "$PY" -r "$SRC/requirements.txt" 'transformers<5'
    "$PY" - <<'PY'
import torch
print("Torch:", torch.__version__, "CUDA:", torch.version.cuda)
assert torch.__version__.split("+")[0] == "2.6.0"
assert torch.version.cuda and torch.version.cuda.startswith("12.4")
assert torch.cuda.is_available(), "PyTorch не видит RTX3060"
print("GPU:", torch.cuda.get_device_name(0))
PY
    msg "Установлен только локальный runtime. Следующая отдельная команда: model-sdxl"
    ;;
  model-sdxl)
    [[ -d "$SRC/.git" && -d "$(dirname "$MODEL")" ]] || die "Сначала install"
    if [[ -f "$MODEL" ]]; then
      echo "$SHA  $MODEL" | sha256sum --check --status || die "Существующий checkpoint не совпадает по SHA-256"
      msg "SDXL уже загружен и проверен"
      exit 0
    fi
    msg "Скачивание SDXL Base (~7 ГБ) только после явного вызова model-sdxl."
    msg "Лицензия: https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0"
    [[ ! -L "$MODEL.part" ]] || die "Нельзя загружать через ссылку"
    curl -fL --retry 3 --connect-timeout 20 -C - -o "$MODEL.part" "$URL" ||
      die "Загрузка прервана; часть сохранена, можно продолжить"
    echo "$SHA  $MODEL.part" | sha256sum --check --status ||
      die "Неверная контрольная сумма; файл НЕ установлен"
    mv -n -- "$MODEL.part" "$MODEL"
    msg "Настоящий SDXL checkpoint установлен и проверен"
    ;;
  start)
    [[ -x "$PY" && -f "$SRC/main.py" && -f "$MODEL" ]] ||
      die "Сначала install и model-sdxl"
    if p="$(own_pid)"; then msg "Уже работает PID=$p"; exit 0; fi
    if port_busy || responding; then die "8188 уже занят; чужие процессы не трогаем"; fi
    (
      cd "$SRC"
      nohup "$PY" "$SRC/main.py" --listen 127.0.0.1 --port 8188 \
        --disable-auto-launch --disable-all-custom-nodes >>"$LOG" 2>&1 </dev/null &
      echo "$!" > "$PID"
    )
    for n in $(seq 1 45); do
      if responding; then
        p="$(own_pid)" || die "API отвечает, но владелец процесса не подтверждён"
        msg "ComfyUI готов: localhost:8188, PID=$p"
        exit 0
      fi
      own_pid >/dev/null || { tail -n 30 "$LOG" >&2; die "ComfyUI завершился"; }
      sleep 1
    done
    msg "Долгий запуск, журнал: tail -n 40 $LOG"
    ;;
  stop)
    p="$(own_pid)" || die "Наш ComfyUI не найден, никого не выключаем"
    kill -TERM "$p"
    msg "SIGTERM отправлен только подтверждённому ComfyUI PID=$p"
    for n in $(seq 1 15); do
      if ! own_pid >/dev/null; then rm -f -- "$PID"; msg "ComfyUI остановлен"; exit 0; fi
      sleep 1
    done
    die "Процесс ещё завершается; kill -9 не используем"
    ;;
  *) die "Команды: doctor | install | model-sdxl | start | status | stop" ;;
esac
