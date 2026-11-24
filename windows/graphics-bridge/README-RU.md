## Проверено на Acer: Windows capability probe (24.11.2026)

Пользователь действительно выполнил `Probe-GraphicsEditors.ps1` в PowerShell 5.1. Зафиксированы только технические сведения, **без имени компьютера и путей пользователя**:

- CorelDRAW Technical Suite 2026 **27.0.0.121**, компонент **VBA (x64)** установлен, ProgID `CorelDRAW.Application`, `CorelDRAW.Application.27`.
- Adobe Photoshop 2026 **27.1.0.17**, ProgID `Photoshop.Application`, `Photoshop.Application.200`, `Photoshop.Application.200.1`.
- PowerShell 5.1; read-only probe выполнился, работа COM automation ещё **не подтверждена**.
- CorelDRAW VBA sandbox и тест PowerTRACE — **следующий этап**; Windows Graphics Agent, сетевой bridge, Photoshop UXP и возвращение в DOCX ещё **не реализованы**.

# Windows Graphics Bridge — первая практическая ступень

**СТАТУС:** файлы созданы в GitHub; **не испытывались на локальной Windows**. До первого отчёта Windows нельзя утверждать, что CorelDRAW/Photoshop интегрированы с сервером.

## Шаг 1. Проверка установленных редакторов — только чтение

На Windows открой PowerShell **обычного пользователя** в каталоге клонированного проекта:

```powershell
powershell.exe -NoProfile -File .\windows\graphics-bridge\Probe-GraphicsEditors.ps1
```

Скрипт выводит JSON с приложениями, версией и зарегистрированными COM ProgID. Он ничего не устанавливает, не запускает CorelDRAW/Photoshop, не изменяет реестр и не подключается к серверу. Перед публикацией результата в открытом GitHub убери machine name и личные пути — JSON может содержать название ПК и пути установок.

## Шаг 2. Реальный локальный CorelDRAW PowerTRACE на копии файла

1. На Windows вручную создай отдельный **тестовый** документ CorelDRAW и сохрани его с `ACHEWIW_SANDBOX` в названии. **Не использовать открытый оригинал книги**.
2. Импортируй в него собственный PNG со схемой (при желании из синтетического теста R3.8). **Выдели ровно один bitmap**.
3. В редакторе VBA CorelDRAW импортируй **локально проверенный** `AchewiwCorelTrace.bas` и запусти `AchewiwTraceSelectedBitmap`. Наличие COM/VBA, доступа к API и точного поведения надо подтвердить в этой версии CorelDRAW 2026; при ошибке сохранить текст ошибки.
4. Макрос проверяет обязательное слово `ACHEWIW_SANDBOX` в имени документа, затем вызывает `Bitmap.Trace(cdrTraceTechnical)` и `TraceSettings.Finish()`. **Он не сохраняет документ и не экспортирует автоматически**. Проверь глазами результат. PowerTRACE создаёт векторные *контуры*, но **не гарантирует** отдельные осмысленные прямоугольники, стрелки и редактируемые надписи.
5. При хорошем результате вручную экспортируй SVG на Windows, затем верни в ACHEWIW как **новый** рисунок после проверки. Исходный документ не редактировать.

Поддержка этих методов описана в официальной документации CorelDRAW API:
- https://community.coreldraw.com/sdk/api/draw/27.2/e/cdrtracetype
- https://community.coreldraw.com/sdk/api/draw/27/m/tracesettings.finish
- https://community.coreldraw.com/sdk/api/draw/27/m/bitmap.trace?lang=cs

**На этом этапе нет автоматической сетевой связи между Windows и Linux**. Её добавляем только после демонстрации рабочего CorelDRAW-цикла на одном тестовом изображении.

## Шаг 3. Photoshop и настоящий серверный мост

Photoshop 2026 поддерживает UXP/PSJS, но доступ к файлам и выполнение команд ограничены песочницей UXP; нельзя просто посылать произвольный JS из DOCX в Photoshop.

- Windows-agent (позже) получает из Linux **копию изображения** через авторизованный API, умеет выполнить ровно операции из allowlist, возвращает версию с SHA256, mime/size и отдельным идентификатором job.
- Corel adapter: `vectorize` / `svg export` в проверенном графическом редакторе.
- Photoshop adapter: `normalize raster`, `resize`, `correct contrast`, `png export`. Не требовать от Photoshop автоматического понимания логических связей схемы.
- ACHEWIW: статусы, retry без удвоения задания, отмена, очередь, передача через локальную сеть/VPN; auth и TLS/VPN; **никакой команды shell из описания книги**.
- UI: исходный рисунок и версия «после», ручное подтверждение, новый DOCX с сохранением hash оригинала.

## Чек-лист приемки

- [ ] На Windows прочитан и проверен JSON capability probe.
- [ ] На копии `ACHEWIW_SANDBOX` PowerTRACE реально создаёт векторы.
- [ ] SVG читается обратно в CorelDRAW и рендерится одинаково.
- [ ] Принятая версия импортируется DOCWORK, источник DOCX остаётся побайтно прежним.
- [ ] Только после этого добавить agent + protected job API + Photoshop adapter.

Ссылка на основной маршрут: `docs/ACHEWIW-MASTER-CHECKLIST-RU.md`.
