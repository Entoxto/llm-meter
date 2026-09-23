"""Small local model inventory; no general-purpose filesystem operations."""
from pathlib import Path
import os
import shutil
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from engine import Client, GIB
from llama_cpp import LlamaCppClient
from inventory import (scan_gguf, validate_gguf, local_model_processes,
                       delete_gguf, delete_ollama, ollama_loaded, testable_gguf)


def size(value):
    return f"{(value or 0) / GIB:.2f} GiB"


class ModelsTab(ttk.Frame):
    def __init__(self, app, parent):
        super().__init__(parent, padding=16)
        self.app = app
        self.rows = {"ollama": {}, "gguf": {}}
        self.tables = {}
        self.action_buttons = {}
        self.loaded = False
        self.loading = False
        self.ollama_host = None
        bar = ttk.Frame(self)
        bar.pack(fill="x")
        ttk.Label(bar, text="Локальные модели", style="Title.TLabel").pack(side="left")
        ttk.Button(bar, text="↻  Обновить", command=self.refresh).pack(side="right")
        ttk.Label(self, text="Выберите модель в таблице, чтобы открыть её папку, запустить тест или удалить.",
                  style="Muted.TLabel").pack(anchor="w", pady=(2, 8))
        self.total = tk.StringVar(value="Объём моделей: список ещё не получен")
        ttk.Label(self, textvariable=self.total, wraplength=1050).pack(anchor="w", pady=8)
        self.status = tk.StringVar(value="Загружаем модели…")
        ttk.Label(self, textvariable=self.status, wraplength=1050).pack(side="bottom", anchor="w", pady=(6, 0))
        for backend, title, columns in [
            ("ollama", "Ollama", [("name", "Имя / тег", 340), ("size", "Размер ↓", 100),
                                  ("quant", "Квант", 140), ("modified", "Изменена", 180)]),
            ("gguf", "llama.cpp / GGUF", [("name", "Файл", 220), ("size", "Размер ↓", 100),
                ("quant", "Квант", 110), ("architecture", "Архитектура / модель", 240),
                ("path", "Полный путь", 450)])]:
            line = ttk.Frame(self)
            line.pack(fill="x", pady=(10, 5))
            ttk.Label(line, text=title, style="Section.TLabel").pack(side="left")
            delete_button = ttk.Button(line, text="Удалить…", state="disabled",
                                       command=lambda b=backend: self.prepare_delete(b))
            delete_button.pack(side="right")
            buttons = [delete_button]
            if backend == "gguf":
                folder_button = ttk.Button(line, text="Открыть папку", state="disabled", command=self.open_folder)
                folder_button.pack(side="right", padx=8)
                test_button = ttk.Button(line, text="Тестировать", state="disabled", command=self.use_model)
                test_button.pack(side="right")
                buttons.extend((folder_button, test_button))
            self.action_buttons[backend] = buttons
            wrapper = ttk.Frame(self)
            wrapper.pack(fill="both", expand=True)
            tree = ttk.Treeview(wrapper, columns=[c[0] for c in columns], show="headings", height=3,
                                selectmode="browse")
            for key, label, width in columns:
                tree.heading(key, text=label)
                tree.column(key, width=width, minwidth=80, stretch=key != "size")
            tree.heading("size", command=lambda b=backend: self.sort(b))
            scroll = ttk.Scrollbar(wrapper, orient="vertical", command=tree.yview)
            horizontal = ttk.Scrollbar(wrapper, orient="horizontal", command=tree.xview)
            tree.configure(yscrollcommand=scroll.set, xscrollcommand=horizontal.set)
            tree.grid(row=0, column=0, sticky="nsew")
            scroll.grid(row=0, column=1, sticky="ns")
            horizontal.grid(row=1, column=0, sticky="ew")
            wrapper.columnconfigure(0, weight=1)
            wrapper.rowconfigure(0, weight=1)
            self.tables[backend] = tree
            tree.bind("<<TreeviewSelect>>", lambda event, b=backend: self.selection_changed(b))
        settings = ttk.LabelFrame(self, text="Где искать GGUF", padding=8)
        settings.pack(fill="x", pady=(12, 4))
        self.folders = tk.Listbox(settings, height=2, background="#192332", foreground="#edf3fa",
                                  selectbackground="#365475", exportselection=False)
        self.folders.pack(side="left", fill="x", expand=True)
        buttons = ttk.Frame(settings)
        buttons.pack(side="right", padx=(10, 0))
        ttk.Button(buttons, text="Добавить папку…", command=self.add_folder).pack(fill="x")
        ttk.Button(buttons, text="Убрать папку", command=self.remove_folder).pack(fill="x", pady=(4, 0))
        self.update_folders()

    def selection_changed(self, backend):
        row = self.selected(backend)
        for button in self.action_buttons[backend]:
            button.configure(state="normal" if row else "disabled")
        if row:
            if backend == "gguf" and not testable_gguf(row):
                self.action_buttons[backend][-1].configure(state="disabled")
                self.status.set(f"{row['name']} — вспомогательный файл для модели. Выберите основной GGUF для теста.")
            else:
                self.status.set(f"Выбрано: {row['name']} · {size(row.get('size'))}")

    def available(self):
        if self.app.busy or self.app.inventory_busy or self.loading or self.app.refreshing:
            self.status.set("Дождитесь завершения текущей операции или остановите тест.")
            return False
        return True

    def update_folders(self):
        self.folders.delete(0, "end")
        for folder in self.app.settings.get("model_dirs", []):
            self.folders.insert("end", folder)

    def add_folder(self):
        if not self.available():
            return
        folder = filedialog.askdirectory(title="Папка с GGUF-моделями")
        if folder:
            roots = self.app.settings.setdefault("model_dirs", [])
            if str(Path(folder).resolve()) not in roots:
                roots.append(str(Path(folder).resolve()))
            self.app.persist()
            self.update_folders()
            self.refresh()

    def remove_folder(self):
        if not self.available() or not self.folders.curselection():
            return
        self.app.settings["model_dirs"].pop(self.folders.curselection()[0])
        self.app.persist()
        self.update_folders()
        self.refresh()

    def refresh(self):
        if not self.available():
            return
        self.loading = True
        self.loaded = True
        host = self.app.host.get() if self.app.backend.get() == "Ollama" else self.app.backend_hosts["Ollama"]
        self.ollama_host = host
        directories = list(self.app.settings.get("model_dirs", []))
        known = list(self.app.settings.get("known_files", []))
        runtime_host = self.app.server.config[2] if self.app.server.running else None
        self.status.set("Чтение списка Ollama и metadata GGUF…")

        def work():
            errors, ollama = [], []
            try:
                ollama = Client(host).list_models()
            except Exception as exc:
                errors.append(str(exc))
            try:
                gguf, scan_errors = scan_gguf(directories, known)
                # Custom quantization names come from the compatible server, never a guessed filename.
                if runtime_host:
                    try:
                        client = LlamaCppClient(runtime_host)
                        for model in client.list_models():
                            client.metadata(model["name"])
                            info = client.model_info
                            for row in gguf:
                                if info.get("model_path") and Path(info["model_path"]).resolve() == Path(row["path"]).resolve():
                                    row["quant"] = info.get("quantization") or row.get("quant")
                    except (RuntimeError, OSError, ValueError):
                        pass
                self.app.emit("inventory_list", (ollama, gguf, errors + scan_errors))
            except Exception as exc:
                self.app.emit("inventory_list", (ollama, [], errors + [str(exc)]))
        threading.Thread(target=work, daemon=True).start()

    def selected(self, backend):
        selection = self.tables[backend].selection()
        return self.rows[backend].get(selection[0]) if selection else None

    def sort(self, backend):
        tree = self.tables[backend]
        reverse = getattr(tree, "size_descending", False)
        tree.size_descending = not reverse
        for index, iid in enumerate(sorted(self.rows[backend], key=lambda i: self.rows[backend][i].get("size", 0),
                                          reverse=not reverse)):
            tree.move(iid, "", index)
        tree.heading("size", text="Размер ↓" if not reverse else "Размер ↑")

    def open_folder(self):
        row = self.selected("gguf")
        if row:
            try:
                os.startfile(str(Path(row["path"]).parent))
            except OSError as exc:
                self.status.set(str(exc))

    def use_model(self):
        if self.available() and (row := self.selected("gguf")) and testable_gguf(row):
            self.app.use_gguf(row["path"])

    def prepare_delete(self, backend):
        if not self.available() or not (row := self.selected(backend)):
            return
        self.app.inventory_busy = True
        self.status.set("Проверка загрузки модели…")
        host = self.ollama_host
        directories = list(self.app.settings.get("model_dirs", []))
        known = list(self.app.settings.get("known_files", []))

        def work():
            try:
                if backend == "ollama":
                    loaded = bool(ollama_loaded(Client(host), row))
                else:
                    validate_gguf(row, directories, known)
                    pids = local_model_processes(row["path"])
                    owned = self.app.server.process.pid if self.app.server.running else None
                    if any(pid != owned for pid in pids):
                        raise RuntimeError("Модель используется внешним llama-процессом. Остановите его перед удалением. "
                                           "LLM Meter останавливает только сервер, который запустил сам.")
                    loaded = bool(pids) or self.app.server.model_path == row["path"]
                self.app.emit("inventory_confirm", (backend, row, loaded, host, directories, known))
            except Exception as exc:
                self.app.emit("inventory_error", str(exc))
        threading.Thread(target=work, daemon=True).start()

    def confirm_delete(self, data):
        backend, row, loaded, host, directories, known = data
        detail = "\nМодель загружена: сначала она будет выгружена / её управляемый сервер будет остановлен." if loaded else ""
        if backend == "ollama":
            detail += "\nОбщие слои других тегов сохраняются; освобождённое место может быть меньше размера модели."
        yes = messagebox.askyesno("Удалить модель?", f"{row['name']}\nРазмер: {size(row.get('size'))}"
            + (f"\n{row['path']}" if backend == "gguf" else f"\nOllama: {host}") + detail
            + "\n\nУдаление необратимо. Продолжить?", parent=self.app.root)
        if not yes:
            self.app.inventory_busy = False
            self.status.set("Удаление отменено.")
            return
        self.status.set("Выгрузка и удаление выбранной модели…")

        def work():
            try:
                if backend == "ollama":
                    client = Client(host)
                    store = Path(os.environ.get("OLLAMA_MODELS", str(Path.home() / ".ollama/models")))
                    free_before = shutil.disk_usage(store).free if client.local and store.is_dir() else None
                    delete_ollama(client, row, loaded)
                    freed = max(0, shutil.disk_usage(store).free - free_before) if free_before is not None else None
                else:
                    validate_gguf(row, directories, known)
                    pids = local_model_processes(row["path"])
                    owned = self.app.server.process.pid if self.app.server.running else None
                    if any(pid != owned for pid in pids):
                        raise RuntimeError("Модель загрузил внешний процесс. Удаление отменено.")
                    if pids or self.app.server.model_path == row["path"]:
                        if not loaded:
                            raise RuntimeError("Модель загрузилась после подтверждения. Повторите удаление.")
                        self.app.server.stop()
                    if local_model_processes(row["path"]):
                        raise RuntimeError("Модель всё ещё используется. Удаление отменено.")
                    freed = delete_gguf(row, directories, known)
                self.app.emit("inventory_deleted", (backend, row, freed))
            except Exception as exc:
                self.app.emit("inventory_error", str(exc))
        threading.Thread(target=work, daemon=True).start()

    def handle(self, event, value):
        if event == "inventory_list":
            self.loading = False
            ollama, gguf, errors = value
            for backend, rows in (("ollama", ollama), ("gguf", gguf)):
                tree = self.tables[backend]
                tree.delete(*tree.get_children())
                for button in self.action_buttons[backend]:
                    button.configure(state="disabled")
                self.rows[backend] = {}
                for row in sorted(rows, key=lambda r: r.get("size", 0), reverse=True):
                    if backend == "ollama":
                        values = (row["name"], size(row.get("size")),
                            (row.get("details") or {}).get("quantization_level") or "—", (row.get("modified_at") or "—")[:19])
                    else:
                        values = (row["name"], size(row["size"]), row.get("quant") or "—",
                            " / ".join(filter(None, (row.get("architecture"), row.get("model_name")))) or "—", row["path"])
                    iid = tree.insert("", "end", values=values)
                    self.rows[backend][iid] = row
                tree.size_descending = True
            a, b = sum(r.get("size", 0) for r in ollama), sum(r["size"] for r in gguf)
            self.total.set(f"Найдено: {len(ollama)} Ollama ({size(a)}) · {len(gguf)} GGUF ({size(b)})\n"
                           "Размер Ollama суммируется по тегам; общие данные могут учитываться несколько раз.")
            self.status.set(" | ".join(errors) or "Выберите модель. Нажмите «Размер», чтобы изменить порядок сортировки.")
            self.app.gguf_paths = [r["path"] for r in sorted(gguf, key=lambda r: r["size"], reverse=True)
                                   if testable_gguf(r)]
            self.app.update_local_models()
        elif event == "inventory_confirm":
            self.confirm_delete(value)
        elif event == "inventory_error":
            self.app.inventory_busy = False
            self.status.set("Ошибка: " + value)
        elif event == "inventory_deleted":
            self.app.inventory_busy = False
            backend, row, freed = value
            if backend == "gguf":
                self.app.settings["known_files"] = [p for p in self.app.settings.get("known_files", []) if p != row["path"]]
                self.app.persist()
            notice = f"Удалена {row['name']} ({size(row.get('size'))}). "
            notice += (f"Свободное место на диске увеличилось на {size(freed)}. Это изменение за время операции."
                       if freed is not None else "Сервер не сообщает фактически освобождённое место.")
            self.refresh()
            self.app.refresh()
            messagebox.showinfo("Модель удалена", notice, parent=self.app.root)
