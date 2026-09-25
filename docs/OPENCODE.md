# Подключение OpenCode

Кнопка запускает OpenCode с моделью текущей работающей сессии и выбранной папкой проекта. Для OpenCode V2 студия создаёт отдельный `opencode.json` в `%LOCALAPPDATA%/ModelStudio/opencode/launches/<id>/opencode/` (или под `MODEL_STUDIO_DATA_DIR`). Каждому запуску назначаются свои `XDG_CONFIG_HOME` и сервер `--standalone`; общие настройки OpenCode и файлы проекта не изменяются. Файлы запуска остаются на диске, пока приложение OpenCode может работать и перечитывать их.

Студия задаёт `OPENCODE_DISABLE_PROJECT_CONFIG=1`, чтобы проектные `opencode.json(c)` не переопределили выбранную модель и сервер. В этом запуске OpenCode также пропускает проектные инструкции `AGENTS.md`. Глобальные настройки OpenCode изолированы через `XDG_CONFIG_HOME`. Это поведение относится только к процессу, открытому из студии.

Для Ollama используется встроенный провайдер `ollama`; для llama.cpp создаётся провайдер `studio-local` с OpenAI-совместимым адресом `/v1`. OpenCode V1 получает временную конфигурацию через `OPENCODE_CONFIG_CONTENT`. Выбор версии выполняется по `opencode --version`.

## Контекст Ollama

OpenAI-совместимый API Ollama не принимает `num_ctx` из запроса. Перед открытием OpenCode `SessionController.prepare_external_client` создаёт служебный профиль через `/api/create` (`from` — исходная модель, `parameters.num_ctx` — выбранный контекст). Затем студия проверяет сохранённый параметр через `/api/show`, выгружает свой исходный runner и загружает профиль с проверкой `/api/ps`. Веса и шаблон наследуются; исходный тег и пользовательские настройки не изменяются. Исследование и история продолжают ссылаться на исходный UUID каталога, фактический runtime-тег хранится отдельно.

Профиль `model-studio-session/<uuid>:ctx-<tokens>` принадлежит сессии, скрыт из каталога и удаляется при её штатном завершении. Повторное открытие OpenCode в той же сессии переиспользует профиль. При ошибке создания исходный runner остаётся работающим; при ошибке загрузки статус не объявляется готовым. Студия удаляет только собственный созданный тег. При аварийном завершении процесса может остаться скрытый служебный тег; автоматическое удаление тегов других сессий не выполняется.

В OpenCode V2 выбор модели использует стабильное имя исходного тега, а `modelID` указывает на профиль текущей сессии. Это позволяет продолжить диалог после следующего запуска студии. Явно заданный пакет `@opencode/ai/providers/openai-compatible` устраняет зависимость от завершения фонового обнаружения моделей. Возможности текста, изображений и инструментов берутся из ответа Ollama. Уже открытые до обновления окна OpenCode сохраняют старую конфигурацию: их нужно заново открыть кнопкой студии. Студия должна оставаться открытой, пока используется её сессия модели.

Проверка установленного OpenCode V2 на тестовом HTTP-сервере, без GPU и пользовательских данных:

```powershell
$env:MODEL_STUDIO_LIVE_OPENCODE_CONTEXT = '1'
.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_studio_opencode_context.py' -v
Remove-Item Env:MODEL_STUDIO_LIVE_OPENCODE_CONTEXT
```

Проверка воспроизводит сброс к 32K у исходного тега, затем подтверждает 64K у служебного профиля, его очистку и продолжение диалога установленного OpenCode после смены профиля. Обычные тесты этого файла не требуют установленного OpenCode.

Ограничение API и создание профиля описаны в [документации Ollama](https://docs.ollama.com/api/openai-compatibility#setting-the-local-context-size); сопоставление `modelID` — в [документации OpenCode V2](https://opencode.ai/v2/docs/models#aliases).

Проверка установленной OpenCode V2 без отправки запроса модели: нужна работающая Ollama и доступный OpenCode. Из корня репозитория в PowerShell:

```powershell
$previousLive = $env:MODEL_STUDIO_LIVE_OPENCODE
try {
    $env:MODEL_STUDIO_LIVE_OPENCODE = '1'
    .venv\Scripts\python.exe -m unittest discover -s tests -p 'test_studio_opencode.py' -v
} finally {
    $env:MODEL_STUDIO_LIVE_OPENCODE = $previousLive
}
```

Тест поднимает частный сервер и проверяет `/api/model` для обоих режимов провайдера.

Формат конфигурации и провайдеров следует [документации OpenCode V2](https://opencode.ai/v2/docs/providers) и [описанию расположения конфигурации](https://opencode.ai/v2/docs/config).
