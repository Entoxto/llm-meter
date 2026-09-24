# Визуальное задание для продолжения работы

Инструмент: встроенный ImageGen. Исходный визуальный референс: согласованная «Модельная студия», тёмная версия с раздельным запуском сервера и открытием OpenCode. Ниже — консолидированный набор промптов для воспроизведения направления; отдельные уточнения текстов отражены в CONCEPT.md.

## Общий промпт

Create a high-fidelity Russian native desktop app UI concept at a natural 1440×1024 target viewport. Use the attached approved screen as the visual reference: charcoal background, restrained violet accent, readable typography, quiet thin dividers, generous spacing. Preserve the same app shell and navigation: Запуск, Чат, Модели, Исследование, Настройки. Avoid model-specific art, marketing slogans, oversized decorative icons, tiny unreadable metadata and unnecessary nested cards. Show one focused screen per image. Numbers are illustrative mock data. Keep shared selected/running model state visible and distinguish saved test data from current live telemetry. Runtime capabilities must never be guessed from model names. Keep native folder picking as an explicit action rather than inventing a custom file browser.

## Дополнения по экранам

1. Launch: searchable model selector; three learned modes (speed, balanced, largest tested context); past benchmark values clearly labelled; independent server start, chat and OpenCode actions; searchable project selector and native folder button.
2. Running: green current session status and server address; live total-GPU VRAM, GPU load, temperature and system RAM labelled by source; confirmed runtime/context; test speed remains historical; unload action; independent clients.
3. Manual: context 32K/64K/96K/100K/128K/custom, reasoning, MTP and dependent Draft, advanced KV memory. Disabled unsupported controls show reasons. No exact test means an explicit empty state and test action; a nearby configuration is labelled different.
4. Chat: local conversation list, new chat, readable text messages, collapsed reasoning only if available, composer, send/stop state, response copy, context counter when available, shared running model. No required project or agent tools.
5. Models: installed/all/archive filters; neutral model list; profile capabilities distinguish confirmed, supported, unknown and unsupported; learned modes; selecting for launch and research actions; deleting model weights keeps tests. Select All when archived rows are visible.
6. Quick test: shared current configuration, no redundant setup; completed sample result automatically saved; median generation, prefill, TTFT, individual runs, actual input/output counts and resource-source labels. Copy report, export, repeat test.
7. Research setup: quick-test versus mode-search tabs; measured base, applicable MTP/Draft, context, memory and long-input checks; bounded time/context; clear effect on chat/OpenCode; no promise of answer quality scoring or exhaustive mathematical optimum.
8. Research progress: stage timeline, current configuration, number of completed measurements, measured readings and compact completed-results table; skipped candidates with reason; stop preserves completed work.
9. Results/history: learned modes are best among tested; applying a recommendation only selects launch configuration; results table filterable by model/environment; explicit old-runtime row; copy/export/folder actions; no manual save button or fabricated framework versions.
10. Settings: backend connections, runtime management, model directories, separately opened OpenCode, local chat/test history, backups and explicit exports; no automatic OpenCode opening or proliferation of user-maintained runtime presets.

## Принятые уточнения

- Память модели и всего компьютера подписывается раздельно; offload слоёв не равен загрузке GPU.
- По умолчанию данные из разных окружений не смешиваются в рекомендации. При показе такой истории фильтр обозначает все окружения.
- Быстрый тест с коротким входом не доказывает работу на полном контексте.
- Изменение конфигурации не создаёт новый профиль; запуск сервера и открытие клиента независимы.
- Старые варианты с удалением тестов вместе с весами и ручным сохранением одного теста больше не применяются.
