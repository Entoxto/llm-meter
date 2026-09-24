# Подключение OpenCode

Кнопка запускает OpenCode с моделью текущей работающей сессии и выбранной папкой проекта. Для OpenCode V2 студия создаёт отдельный `opencode.json` в `%LOCALAPPDATA%/ModelStudio/opencode/launches/<id>/opencode/` (или под `MODEL_STUDIO_DATA_DIR`). Каждому запуску назначаются свои `XDG_CONFIG_HOME` и сервер `--standalone`; общие настройки OpenCode и файлы проекта не изменяются. Файлы запуска остаются на диске, пока приложение OpenCode может работать и перечитывать их.

Студия задаёт `OPENCODE_DISABLE_PROJECT_CONFIG=1`, чтобы проектные `opencode.json(c)` не переопределили выбранную модель и сервер. В этом запуске OpenCode также пропускает проектные инструкции `AGENTS.md`. Глобальные настройки OpenCode изолированы через `XDG_CONFIG_HOME`. Это поведение относится только к процессу, открытому из студии.

Для Ollama используется встроенный провайдер `ollama`; для llama.cpp создаётся провайдер `studio-local` с OpenAI-совместимым адресом `/v1`. OpenCode V1 получает временную конфигурацию через `OPENCODE_CONFIG_CONTENT`. Выбор версии выполняется по `opencode --version`.

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
