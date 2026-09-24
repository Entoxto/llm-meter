"""Explicit screenshot fixtures; never imported in normal application mode."""
def seed_preview(bridge, page=0):
    model = {"id": "preview-model", "name": "Bonsai 27B MTP", "path": "preview.gguf",
             "backend": "gguf", "size_bytes": 12_000_000_000, "quantization": "PQ2_0",
             "available": True, "identity_verified": True, "capabilities": ["mtp", "reasoning", "kv-cache"],
             "runtime_name": "Prism MTP"}
    project = {"id": "preview-project", "name": "Локальные модели", "path": "E:/AI Projects/Локальные модели"}
    result = {"id": "preview-result", "model_id": model["id"], "model_name": model["name"],
              "created_at": "2026-09-24T20:30:00", "status": "completed", "context": 98304,
              "speed": 74.8, "ttft": .42, "vram_gb": 14.2, "prompt_speed": 1350,
              "summary": {"median_tokens_per_second": 74.8, "median_ttft_seconds": .42,
                          "median_prompt_tokens_per_second": 1350},
              "report": "Демонстрационный результат для проверки интерфейса. Не является измерением."}
    recs = [{"key": key, "title": title, "context": ctx, "speed": speed,
             "result_id": result["id"], "available": True, "reason": "По сохранённым тестам"}
            for key, title, ctx, speed in (("speed", "Максимальная скорость", 32768, 82),
                ("balanced", "Сбалансированный", 98304, 74.8), ("context", "Максимальный контекст", 131072, 58))]
    bridge._values.update(models=[model], selectedModel=model, projects=[project], selectedProject=project,
        draft={"context": 98304, "mtp": True, "draft": 2, "reasoning": "auto", "kv_type": "f16", "gpu_layers": 99},
        selectedResult=result, matchingResult=result, results=[result], recommendations=recs,
        settings={"server_exe": "C:/Models/Prism/llama-server.exe", "managed_host": "http://127.0.0.1:8081",
                  "backend_hosts": {"Ollama": "http://127.0.0.1:11434"}, "model_dirs": ["C:/Models"], "opencode_exe": "opencode.exe"},
        conversations=[{"id": "preview-chat", "title": "Как выбрать режим модели"}],
        messages=[{"id": "1", "role": "user", "text": "Объясни, как выбрать контекст для работы над проектом.", "reasoning": "", "status": "complete", "metadata": {}},
                  {"id": "2", "role": "assistant", "text": "Начните с объёма задачи.\n\nДля коротких вопросов достаточно небольшого контекста. При работе с несколькими файлами полезно большее окно: в него поместятся код и история обсуждения.\n\nСбалансированный режим — удобная отправная точка. Сравните его с результатами тестов на вашем компьютере.", "reasoning": "Доступные рассуждения модели.", "status": "complete", "metadata": {"tokens_per_second": 74.8}}],
        page=page, error="", notice="")
    bridge.changed.emit()
