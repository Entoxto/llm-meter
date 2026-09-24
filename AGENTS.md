# Навигация для агентов

## Начать здесь

Модельная студия — Windows desktop-приложение для локальных LLM: Python,
PySide6 / Qt Quick, SQLite; Ollama и llama.cpp — внешние runtime.
Рабочий каталог команд — корень этого репозитория, а не его родительская папка.

- [README.md](README.md): установка, запуск и пользовательские сценарии.
- [docs/IMPLEMENTATION.md](docs/IMPLEMENTATION.md): что реализовано и что проверялось.
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): границы, инварианты и текущая карта (§11).
- [docs/BUILD.md](docs/BUILD.md): воспроизводимая Windows-сборка.
- [docs/design-v2/design-qa.md](docs/design-v2/design-qa.md): снимки настоящего Qt UI;
  `screens/` рядом — исходные дизайн-макеты, не доказательство поведения приложения.

При расхождении первоначального плана и реализации проверяйте код и
IMPLEMENTATION.md; не создавайте отсутствующие слои ради совпадения с планом.

## Где менять

| Задача | Начальные точки | Релевантные тесты в `tests/` |
|---|---|---|
| Старт приложения, ресурсы, окно | `src/model_studio/bootstrap.py`, `desktop/qml/Main.qml`, `Theme.qml` | `test_studio_desktop.py`, QML lint и захват окна |
| Выбор модели, кнопки и формы | `desktop/controllers.py` → соответствующий `desktop/qml/*Page.qml` | `test_studio_desktop.py` |
| Запуск/выгрузка, контекст, отмена | `configuration.py`, `session.py`, `backends/`, `platform/windows_process.py` | `test_studio_session.py`, `test_studio_end_to_end.py`, `test_management.py` |
| Каталог, алиасы, runtime-профиль | `catalog.py`; корневые `inventory.py`, `model_aliases.py`, `runtime_profiles.py` | `test_studio_catalog.py`, `test_management.py` |
| Доступность ручных параметров runtime | `backends/capabilities.py`, `desktop/controllers.py` (`_load` → `_profile`) | `test_studio_capabilities.py`, `test_studio_desktop.py`, `test_studio_session.py` |
| Чат, mmproj, картинки | `chat.py`, `attachments.py`, `backends/images.py`, `desktop/qml/ChatPage.qml` | `test_studio_chat.py`, `test_studio_attachments.py`, `test_studio_vision.py` |
| Исследование, результаты, рекомендации | `benchmarks/research.py`, `recommendations.py`, `reports.py`; измерительный runner в корневом `engine.py` | `test_studio_research.py`, `test_studio_reports.py`, `test_engine.py`, `test_studio_vision.py` |
| БД, миграции, импорт, backup | `storage/store.py`, `platform/paths.py` | `test_studio_storage.py`, `test_studio_attachments.py` |
| OpenCode и папка проекта | `integrations/opencode.py`, [docs/OPENCODE.md](docs/OPENCODE.md) | `test_studio_opencode.py` |

В таблице пути без `src/model_studio/` относятся к этому пакету, кроме явно
отмеченных корневых файлов. `desktop/workers.py` выполняет задачи; Qt-мост
получает их результаты через очередь. Схема SQLite и миграции находятся прямо
в `Store._migrate`, отдельного каталога SQL-миграций сейчас нет.

Старые `app.py`, `models_tab.py`, `launch.pyw` — Tkinter UI для сверки миграции.
Новый интерфейс находится только в `src/model_studio/desktop/`. Корневые
`engine.py`, `llama_cpp.py`, `inventory.py`, `managed_server.py`,
`runtime_profiles.py`, `model_aliases.py` всё ещё используются; они не мёртвый код.

## Проверки и команды

PowerShell, локальная `.venv` после установки из README:

```powershell
# Один затронутый набор (замените имя файла по таблице)
.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_studio_research.py' -q

# Полный набор; обычный запуск не требует GPU или загруженной модели
.venv\Scripts\python.exe -m unittest discover -s tests -q

# QML после правок интерфейса
$qmlFiles = (Get-ChildItem src/model_studio/desktop/qml -Filter *.qml).FullName
& .venv\Scripts\pyside6-qmllint.exe @qmlFiles

# Изолированный снимок, page: 0 Запуск, 1 Чат, 2 Модели, 3 Исследование, 4 Настройки
.venv\Scripts\python.exe -m model_studio --data-dir .test-data/preview --demo --page 0 --capture .test-artifacts/launch.png
```

`--demo` требует одновременно `--data-dir` и `--capture`; это тестовые данные.
Для среды без дисплея задайте `QT_QPA_PLATFORM=offscreen` только процессу
проверки и восстановите окружение после неё. Отдельного type checker или
Python-линтера в проекте пока нет. Live OpenCode-проверка включается явно через
`MODEL_STUDIO_LIVE_OPENCODE=1`; условия описаны в OPENCODE.md.

После изменения поведения выбирайте тесты по риску; документация не требует
пересборки EXE. Сборка `tools/build_windows.ps1` заменяет `dist/ModelStudio`:
не запускайте её поверх работающего из этого каталога приложения. Нужен
контролируемый перезапуск с сохранением текущей работы, не принудительное убийство.

## Инварианты

- Выбор модели и черновик настроек не меняют действующую сессию. Запуск сервера,
  чат и OpenCode — отдельные действия.
- Единственный `SessionController` владеет занятостью; чат, benchmark и
  исследование не выполняются одновременно. Исследование само запускает
  конфигурации и восстанавливает исходную сессию либо выгружает свою модель.
- QML не делает I/O. Блокирующая работа идёт через Workers; сервисы не импортируют Qt.
- Не завершать внешние серверы. Windows-служебные subprocess должны работать
  без консоли; интерактивное окно OpenCode открывается намеренно.
- Не смешивать запрос, фактический контекст, старый замер и текущую телеметрию.
  Рекомендации требуют проверенной идентичности модели/проектора и условий теста.
- SQLite использует короткие соединения. Картинки — файлы `attachments/`,
  сообщения содержат относительные ссылки; переносимая копия — `.studio-backup`.
- Имена моделей, пути конкретного компьютера и бренд не определяют поведение.

## Локальные и генерируемые данные

Данные пользователя: `%LOCALAPPDATA%/ModelStudio`, переопределение —
`MODEL_STUDIO_DATA_DIR`. Для тестов используйте отдельный каталог; не правьте
пользовательскую SQLite и не запускайте GPU-проверки поверх его текущей работы.

При работе из MSIX-версии Codex доступ к AppData и дочерние процессы могут
перенаправляться в `Packages/OpenAI.Codex_*/LocalCache/Local`. Это отдельная
копия данных, даже если путь выглядит как обычный `%LOCALAPPDATA%`.
Пользовательское приложение запускайте через `tools/start_windows.ps1`
(Проводник открывает существующий ярлык), а не прямым `Start-Process` EXE.
При расхождении истории сначала проверьте фактический путь открытого файла;
не восстанавливайте поверх пользовательской БД старую виртуализированную копию.

Не коммитить `.venv/`, `dist/`, `build/`, `.test-data/`, `.test-artifacts/`,
веса, скачанные runtime, логи, резервные копии и настройки с личными путями.
`docs/design-v2/qa/` — намеренно сохранённые снимки; перед публикацией проверить
их на личные данные. Не редактировать зависимости внутри `.venv` или сборку
вместо исходников. Сторонние SVG-иконки имеют лицензию в `desktop/qml/icons/LICENSE`.
