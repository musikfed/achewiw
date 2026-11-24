# Аппаратный паспорт ACHEWIW / собственный AI-runtime: 2×Xeon + RTX 3060

**Снято на сервере /1tbdisk/achewiw, 24.11.2026.** Источник цифр — фактический вывод пользователя `lscpu`, `lscpu -C`, `numactl --hardware`, `nvidia-smi topo -m`, `nvidia-smi --query-gpu`. Это **паспорт наблюдения**, а не обещание производительности и не разрешение менять BIOS, ядро, службы и работающие AI-проекты.

## 1. Зафиксированная топология

| Компонент | Реальные данные |
| --- | --- |
| CPU | **2 × Intel Xeon E5-2650 v2 @ 2.60 GHz**, 8 ядер/16 потоков на сокет, суммарно **16 физических ядер, 32 логических CPU (0–31)** |
| ISA | `sse4_1 sse4_2 avx f16c`. **`avx2`, `fma`, `avx512f` не показаны** в выводе; не компилировать с `-mavx2` / `-mfma` |
| Кэш L1D | 32 КиБ на ядро; 16 × 32 КиБ = **512 КиБ** суммарно, не единый общий пул |
| Кэш L1I | 32 КиБ на ядро; **512 КиБ** суммарно |
| Кэш L2 | 256 КиБ на ядро; **4 МиБ** суммарно |
| Кэш L3 | 20 МиБ на сокет; **40 МиБ** суммарно, но физически **два независимых shared LLC** |
| RAM | `node 0 size: 48102 MB`, `free: 605 MB` **в момент снимка**; это не `MemAvailable` и само по себе не доказывает OOM, но сигнал проверить память до любого memory-pinning или обучения |
| NUMA ОС | `available: 1 nodes (0)`, `node0 CPU(s): 0-31`, расстояние до себя 10; Linux **не предоставляет два отдельных NUMA-домена**, хотя сокетов два |
| GPU | NVIDIA RTX 3060 **12 ГБ VRAM** |
| GPU CPU affinity | `0-31`, `NUMA Affinity 0`, `GPU NUMA ID N/A`; **нельзя определить**, к какому физическому сокету ближе PCIe-карта, по этому выводу |
| PCIe наблюдение | `pcie.link.gen.current=1`, `pcie.link.width.current=16`. **Gen1 x16 в простое может быть энергосбережением**, это не доказательство узкого интерфейса во время передачи |

### Что нельзя предполагать без измерения

- **NUMA interleaving** или скрытие двух NUMA-доменов BIOS/SRAT — гипотеза. Не переключать BIOS по догадке. Проверить `dmesg`/ACPI SRAT при необходимости.
- Фактическая доступная память неизвестна: нужны `free -h`, `grep -E "MemAvailable|SwapTotal|SwapFree" /proc/meminfo`, `vmstat 1 3`; `free 605 MB` может сопровождаться большим объёмом reclaimable page cache.
- Поколение PCIe под нагрузкой неизвестно: сравнить `current` и `max` в начале и во время **уже разрешённого** GPU вычисления, не запускать отдельную нагрузку в production.
- `nvidia-smi topo -m` на машине с единственным GPU и одним открытым NUMA-узлом **не подтверждает** конкретное QPI-плечо. Исследовать PCI bus/root port только диагностикой.
- CPU кэш не является программно адресуемой постоянной «VRAM для весов». Данные размещаются в RAM, оптимизация кэша идёт через укладку структур/тайлинг/affinity, без гарантий удержания в L3.

## 2. Как использовать в собственном «моторе» ИИ

| Уровень | Назначение | Техническая реализация и проверка |
| --- | --- | --- |
| Xeon регистры + L1 | короткие операции маршрутизации, SIMD обработка батчей индексов | C++ с feature-detection, `-march=ivybridge` *только после подтверждения флагов*; бенчмарк scalar vs AVX |
| L2/L3 | hot metadata, таблицы маршрутов, дескрипторы LoRA; небольшие блоки деквантования | bounded structs, измерять cache misses через `perf` при наличии разрешений, избегать ложного sharing |
| RAM 48 ГБ | staging весов/LoRA, CPU-side indexes/RAG, metadata, page cache | лимиты RSS, `MemAvailable`, mmap/read-ahead и LRU; не фиксировать в pinned RAM всё подряд |
| PCIe | передавать **только изменившийся LoRA-адаптер**, не веса всей базы на каждый токен | pinned host buffers + `cudaMemcpyAsync`, streams; сравнивать GB/s + время запуска и latency |
| VRAM 12 ГБ | активные сжатые веса, выбранная LoRA, KV cache, CUDA scratch buffers | память резервировать явно, GPU lease; исключить конкуренцию ComfyUI/Ollama и прочих проектов |
| RTX 3060 Tensor/CUDA cores | GEMM, квантованные матричные операции, внимание, LoRA | сперва специализированный зрелый backend и профилировщик; собственные CUDA kernels после честного baseline |
| Expert router | выбрать один/несколько совместимых LoRA на **общей** базовой модели | версии/base hashes, dimension check, admission/eviction, rollback, решение всегда воспроизводимо |

**Важно:** набор независимых LLM + роутер — multi-model ensemble, не обученная MoE. Для echte MoE требуются согласованные эксперты и маршрутизатор в единой архитектуре/обучении. LoRA не гарантирует увеличение «обученности» от простого роста ранга.

### Предлагаемая цепочка, без дополнительного OpenAI/Ollama wrapper

`author request → planner (CPU) → LoRA catalog (RAM + file) → selective staging → CUDA backend → token sampler → QA → DOCWORK/StoryEngine`

Старт с **собственного ABI/планировщика** и поддерживаемого compute backend: GGUF/llama.cpp CUDA как базовая контрольная линия; затем самостоятельное C++/CUDA ядро для отдельной операции (например, FP16/INT4 GEMM) и тесты численной корректности. Собственный движок и лицензирование **отдельны** от прав на чужие веса/датасеты/CUDA API.

## 3. Безопасные диагностические команды — отдельным ручным запуском, когда нужно

```bash
printf '\n=== RAM ===\n'; free -h
grep -E 'MemAvailable|SwapTotal|SwapFree|Cached:' /proc/meminfo
printf '\n=== MEMORY PRESSURE ===\n'; cat /proc/pressure/memory
printf '\n=== CPU-to-PCIe ===\n'; lspci -nn | grep -iE 'nvidia|vga|3d'
printf '\n=== PCIe CAPABILITY (idle) ===\n'
nvidia-smi --query-gpu=pcie.link.gen.current,pcie.link.gen.max,pcie.link.width.current,pcie.link.width.max --format=csv
printf '\n=== NUMA TOPOLOGY ===\n'; numactl --hardware
printf '\n=== THREAD SIBLINGS ===\n'; lscpu -e=CPU,CORE,SOCKET,NODE | head -40
```

Не вызывать `numactl --membind=1`: NUMA node 1 сейчас **не существует** в представлении ОС. Не советовать выключать Hyper-Threading/NUMA interleave без измерения. Сравнивать `pcie.link.gen.current` именно **под разрешённой рабочей GPU-нагрузкой**.

## 4. Применение в дорожной карте

**Сейчас (типография):** этот сервер запускает ACHEWIW и модели для редакторских задач; на Windows отдельно используют CorelDRAW Technical Suite 2026 / Photoshop 2026 для иллюстраций через Graphics Bridge. Никакого блокирования основной книжной работы ожиданием тестов Vision.

**После работающего Graphics Bridge:** изолированный эксперимент с CUDA backend и одиночным LoRA-переключением, метрики RAM/VRAM/PCIe, сохранение кодов ошибок и benchmark по tokens/s; не менять действующий Ollama.

**Позже («Верфь»):** обучение/дистилляция с облачными квотами, checkpoint, сохранением метрик, затем импорт только совместимых и лицензированных весов и LoRA в локальный движок. **Верфь не блокирует типографию и не захватывает общий GPU.**

## 5. Сохранённые промпты для следующих AI-агентов

**PROMPT-A / CPU topology**

> Прочти `docs/HUANANZHI-E5-2650V2-RTX3060-TOPOLOGY-RU.md`. Не придумывай 2 NUMA-узла: ОС показывает один. Предложи read-only эксперимент cache locality, CPU affinity, L2/L3 miss rate без BIOS изменений, без CUDA jobs и без остановки Ollama. Сначала проверь MemAvailable и отделяй реальные измерения от гипотез.

**PROMPT-B / собственный GPU runtime**

> Опирайся на паспорт топологии. Создай самостоятельный модульный AI-runtime: weight storage INT4/NF4, LoRA catalog, GPU lease, memory planner, CUDA execution interface и testbench. Не запускай код GPU без подтверждения; не копируй чужой proprietary code; для сравнения используй llama.cpp как reference, а не runtime dependency. Не обещай ускорения, пока нет профиля PCIe под нагрузкой. Докажи корректность вычислений и memory bounds.

**PROMPT-C / LoRA эксперты**

> Для общей модели с зафиксированным hash/base и 12GB GPU добавь совместимые LoRA-эксперты, явные правила выбора, очередь, TTL/eviction, rollback, метрики swap CPU-RAM↔GPU, warm/cold latency. Не смешивай разные базовые модели в один LoRA и не называй ансамбль настоящей Mixture-of-Experts.

**PROMPT-D / типография + Верфь**

> Не отходи от основного ACHEWIW маршрута: надёжная генерация и продолжение книги → редактура DOCWORK → типографская вёрстка/экспорт → проверка публикационных файлов. Windows Graphics Bridge для CorelDRAW/Photoshop — независимый сервис рисунков. «Верфь» облачного обучения — отдельная очередь позже; квоты, checkpoints, train curves и датасеты не должны тормозить книгу. Везде защищай оригинальный DOCX и сохранённые главы.

## Источники для сверки оборудования/разработки

- Intel ARK E5-2650 v2: https://www.intel.com/content/www/us/en/products/sku/75269/intel-xeon-processor-e52650-v2-20m-cache-2-60-ghz/specifications.html
- NVIDIA CUDA async transfers: https://docs.nvidia.com/cuda/cuda-programming-guide/02-basics/asynchronous-execution.html
- NVIDIA CUDA best practices: https://docs.nvidia.com/cuda/cuda-c-best-practices-guide/
