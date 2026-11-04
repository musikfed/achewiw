# ACHEWIW: реальные изображения на RTX 3060 12 ГБ (CUDA 12.4)

Результат диагностики 04.11.2026: NVIDIA 550.163.01, RTX 3060 12 ГБ,
на момент проверки VRAM занято 19 МиБ; Ollama не держит модели в GPU.
На 127.0.0.1:8188 никто не ответил — ComfyUI **не запущен на этом порту**.
Это не означает, что ComfyUI вообще нигде не установлен.

## Подготовленный безопасный вариант

На драйвере 550 нельзя без проверки устанавливать новейший ComfyUI
с рекомендуемым в 2026 году CUDA 13. Поэтому сценарий ниже использует
изолированный стек без изменения драйвера:

- Python 3.12 и uv;
- официальный ComfyUI v0.3.44, фиксированный commit
  c5de4955bb91a2b136027a698aaecb8d19e3d892;
- PyTorch 2.6.0, CUDA 12.4, torchvision 0.21.0;
- отдельный каталог /1tbdisk/achewiw/data/comfyui-runtime;
- SDXL Base 1.0, ~7 ГБ, скачать только отдельной командой и сверить SHA-256;
- доступ к графическому движку **только** по loopback 127.0.0.1:8188.

Это совместимый по версиям **план первого SDXL-прогона**, а не
уже протестированный на твоём железе результат. Поскольку ComfyUI старее
текущей версии, новый FLUX workflow не обещается.

## Шаг 1. Только получить код в Git и проверить без установки

```bash
cd /1tbdisk/achewiw
git status -sb
git fetch --no-tags origin
git merge --ff-only origin/feature/technical-epub-first

bash -n scripts/comfyui-local.sh
bash scripts/comfyui-local.sh doctor
```

Команды выше ничего не скачивают сверх обычных изменений GitHub и
не изменяют службу NVIDIA, Ollama или контейнеры.

## Шаг 2. Только после решения установить ComfyUI

Зависимости потребуют несколько гигабайт диска и доступ к интернету.

```bash
cd /1tbdisk/achewiw
bash scripts/comfyui-local.sh install
```

Устанавливается только отдельный виртуальный Python в data/comfyui-runtime.
Подтверждается, что PyTorch видит RTX 3060.

## Шаг 3. Отдельно загрузить модель SDXL

Официальная модель:
https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0

Ознакомься с лицензией OpenRAIL++ до применения для публикации.
Загрузка модели не происходит автоматически при установке ComfyUI.

```bash
bash scripts/comfyui-local.sh model-sdxl
```

Скрипт загружает sd_xl_base_1.0.safetensors по HTTPS с Hugging Face,
поддерживает продолжение загрузки и сверяет опубликованный SHA-256:
31e35c80fc4829d14f90153f4c74cd59c90b779f6afe05a74cd6120b893f7e5b.

## Шаг 4. Запустить локальный движок и проверить

```bash
bash scripts/comfyui-local.sh start
bash scripts/comfyui-local.sh status

curl --noproxy '*' -fsS --max-time 5 \
  http://127.0.0.1:8188/object_info/CheckpointLoaderSimple \
  | python3 -m json.tool | head -30
```

После этого войти в ACHEWIW на http://192.168.0.100:7080,
открыть «04 · Иллюстрации», выбрать главу и создать изображение.
Веб-приложение обращается к ComfyUI через network_mode: host,
при этом сам Docker-контейнер не получает CUDA/GPU.

## Остановка и диагностика

```bash
bash scripts/comfyui-local.sh stop
bash scripts/comfyui-local.sh status
tail -n 40 /1tbdisk/achewiw/data/comfyui-runtime/comfyui.log
ss -ltnp '( sport = :8188 )'
```

Остановка посылает SIGTERM **только собственному подтверждённому процессу**,
проверяя PID, рабочую папку и точную команду запуска. Нет killall/kill -9.
Другие сервисы, включая Ollama и YadroKreator, не затрагиваются.

Обычный update-editorial.sh не ставит и не включает ComfyUI.
Не запускай сценарий полной очистки SQLite ради ComfyUI.
Если возникнет CUDA OOM, не делай глобальный сброс видеокарты:
сначала проверь команду ollama ps и другие процессы в nvidia-smi.

Возможности ACHEWIW после настройки: SDXL (реальный workflow),
автоматическое обнаружение подходящего checkpoint, привязка рисунка
к выбранной главе, EPUB/DOCX/PDF. Постоянная внешность персонажей
в серии картинок потребует отдельной разработки: текущий workflow
не гарантирует согласованность лиц, меха и одежды между рисунками.
