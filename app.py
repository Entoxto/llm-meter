"""Desktop UI for the Ollama meter. Run with python app.py."""
from __future__ import annotations

import os
from pathlib import Path
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from engine import Client, CONTEXT, GIB, RUNS, TOKENS, Telemetry, placement, run_benchmark, gpu_summary
from llama_cpp import LlamaCppClient
from engine import Cancelled
from inventory import load_settings, save_settings, scan_gguf, model_labels, testable_gguf, gguf_metadata
from managed_server import ManagedServer
from models_tab import ModelsTab
from runtime_profiles import has_managed_runtime, runtime_for

ROOT = Path(__file__).resolve().parent
BG = "#101722"
PANEL = "#192332"
CARD = "#202d3e"
FG = "#edf3fa"
MUTED = "#a6b6ca"
ACCENT = "#6edcba"


def gib(value):
    return "—" if value is None else f"{value / GIB:.2f}"


def decimal(value, suffix=""):
    return "—" if value is None else f"{value:.2f}{suffix}"


class App:
    def __init__(self, root):
        self.root = root
        self.events = queue.Queue()
        self.busy = False
        self.refreshing = False
        self.inventory_busy = False
        self.settings = load_settings(ROOT / "settings.json")
        self.server = ManagedServer(ROOT / "results")
        self.gguf_paths = []
        self.model_paths = {}
        self.closing = False
        self.stop = threading.Event()
        self.client = None
        self.started = None
        self.last_report = None
        self.last_sample = None
        self.test_samples = []
        self.backend_hosts = {"Ollama": os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434"),
                              "llama.cpp": "http://127.0.0.1:8080"}
        self.current_backend = "Ollama"
        self.backend_hosts.update(self.settings.get("backend_hosts", {}))
        self.dpi_scale = max(1, root.winfo_fpixels("1i") / 96)
        root.title("LLM Meter · Ollama / llama.cpp")
        root.configure(bg=BG)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.style()
        self.build()
        root.update_idletasks()
        width = min(root.winfo_screenwidth() - 60, int(1120 * self.dpi_scale))
        height = min(root.winfo_screenheight() - int(80 * self.dpi_scale),
                     max(root.winfo_reqheight() + 40, int(870 * self.dpi_scale)))
        root.geometry(f"{width}x{height}")
        root.minsize(min(width, int(960 * self.dpi_scale)), min(height, int(700 * self.dpi_scale)))
        root.after(80, self.pump)
        saved_backend = self.settings.get("last_backend")
        local_llama = has_managed_runtime(self.settings)
        if saved_backend == "llama.cpp" or (not saved_backend and local_llama):
            self.backend.set("llama.cpp")
            self.change_backend()
        else:
            self.refresh()

    def style(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure(".", background=BG, foreground=FG, font=("Segoe UI", 10))
        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=BG, foreground=FG)
        style.configure("Muted.TLabel", foreground=MUTED)
        style.configure("Section.TLabel", font=("Segoe UI Semibold", 12), foreground=FG)
        style.configure("Title.TLabel", font=("Segoe UI Semibold", 22))
        style.configure("Metric.TLabel", font=("Segoe UI Semibold", 26), foreground=ACCENT)
        style.configure("Card.TFrame", background=CARD)
        style.configure("Card.Muted.TLabel", background=CARD, foreground=MUTED)
        style.configure("Card.Metric.TLabel", background=CARD, foreground=ACCENT,
                        font=("Segoe UI Semibold", 24))
        style.configure("Accent.TButton", background=ACCENT, foreground=BG,
                        font=("Segoe UI Semibold", 10), padding=(18, 10))
        style.map("Accent.TButton", background=[("active", "#91efd2"), ("disabled", PANEL)],
                  foreground=[("disabled", "#6e7a8c")])
        style.configure("TButton", background="#293c50", padding=(14, 9))
        style.map("TButton", background=[("active", "#3a526b"), ("disabled", PANEL)],
                  foreground=[("disabled", "#6e7a8c")])
        style.configure("TEntry", fieldbackground=PANEL, foreground=FG, padding=7)
        style.configure("TCombobox", fieldbackground=PANEL, foreground=FG, padding=7,
                        arrowcolor=FG)
        style.map("TCombobox", fieldbackground=[("readonly", PANEL)],
                  foreground=[("readonly", FG), ("disabled", MUTED)])
        self.root.option_add("*TCombobox*Listbox.background", PANEL)
        self.root.option_add("*TCombobox*Listbox.foreground", FG)
        style.configure("Treeview", background=PANEL, fieldbackground=PANEL,
                        foreground=FG, rowheight=int(30 * self.dpi_scale), borderwidth=0)
        style.configure("Treeview.Heading", background="#27384c", foreground=FG,
                        font=("Segoe UI Semibold", 10), padding=6)
        style.map("Treeview", background=[("selected", "#365475")])
        style.configure("Horizontal.TProgressbar", background=ACCENT, troughcolor=PANEL)
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", background=PANEL, foreground=MUTED, padding=(16, 7))
        style.map("TNotebook.Tab", background=[("selected", "#293c50")], foreground=[("selected", FG)])
        style.configure("TCheckbutton", background=BG, foreground=FG)
        style.map("TCheckbutton", background=[("active", PANEL)])
        style.configure("TLabelframe", background=BG, foreground=FG, bordercolor="#344559")
        style.configure("TLabelframe.Label", background=BG, foreground=FG,
                        font=("Segoe UI Semibold", 10))

    def build(self):
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True)
        self.test_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.test_tab, text="Тест")
        # Keep all metrics reachable on small screens and at Windows DPI > 100%.
        canvas = tk.Canvas(self.test_tab, background=BG, highlightthickness=0)
        scrollbar = ttk.Scrollbar(self.test_tab, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        frame = ttk.Frame(canvas, padding=20)
        window = canvas.create_window((0, 0), window=frame, anchor="nw")
        frame.bind("<Configure>", lambda event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(window, width=event.width))
        self.root.bind("<MouseWheel>", lambda event: canvas.yview_scroll(-int(event.delta / 120), "units")
                       if canvas.yview() != (0.0, 1.0) and event.widget.winfo_class() != "Treeview" else None)
        ttk.Label(frame, text="LLM Meter", style="Title.TLabel").pack(anchor="w")
        ttk.Label(frame, text="Выберите модель и размер контекста. Приложение загрузит её и измерит скорость.",
                  style="Muted.TLabel").pack(anchor="w", pady=(4, 16))
        setup = ttk.LabelFrame(frame, text="Настройки теста", padding=14)
        setup.pack(fill="x", pady=(0, 14))
        ttk.Label(setup, text="Источник моделей", style="Section.TLabel").pack(anchor="w", pady=(0, 6))
        server = ttk.Frame(setup)
        server.pack(fill="x")
        self.backend = tk.StringVar(value="Ollama")
        self.backend_select = ttk.Combobox(server, textvariable=self.backend,
                                          values=("Ollama", "llama.cpp"), width=13, state="readonly")
        self.backend_select.pack(side="left", padx=(0, 12))
        self.backend_select.bind("<<ComboboxSelected>>", self.change_backend)
        self.host_label = ttk.Label(server, text="Адрес сервера")
        self.host_label.pack(side="left", padx=(0, 8))
        self.host = tk.StringVar(value=self.backend_hosts["Ollama"])
        self.host_entry = ttk.Entry(server, textvariable=self.host, width=40)
        self.host_entry.pack(side="left", fill="x", expand=True)
        self.managed_hint = ttk.Label(server, text="Локальные модели GGUF", style="Muted.TLabel")
        self.refresh_button = ttk.Button(server, text="↻  Обновить", command=self.refresh)
        self.refresh_button.pack(side="left", padx=(12, 0))
        self.log_row = ttk.Frame(setup)
        self.log_path = tk.StringVar()
        ttk.Label(self.log_row, text="Лог запуска (необязательно)").pack(side="left", padx=(0, 10))
        self.log_entry = ttk.Entry(self.log_row, textvariable=self.log_path)
        self.log_entry.pack(side="left", fill="x", expand=True)
        self.log_button = ttk.Button(self.log_row, text="Выбрать…", command=self.choose_log)
        self.log_button.pack(side="left", padx=(10, 0))
        controls = ttk.Frame(setup)
        self.controls = controls
        controls.pack(fill="x", pady=(14, 8))
        self.model = tk.StringVar()
        model_box = ttk.Frame(controls)
        model_box.pack(side="left", fill="x", expand=True)
        ttk.Label(model_box, text="Модель").pack(anchor="w", pady=(0, 5))
        self.models = ttk.Combobox(model_box, textvariable=self.model, state="readonly")
        self.models.pack(fill="x", expand=True)
        self.runtime_note = tk.StringVar(value="Runtime: обычный llama.cpp")
        ttk.Label(setup, textvariable=self.runtime_note, style="Muted.TLabel").pack(anchor="w", pady=(0, 6))
        self.model.trace_add("write", lambda *_: self.update_runtime_note())
        context_box = ttk.Frame(controls)
        context_box.pack(side="left", padx=(14, 0))
        ttk.Label(context_box, text="Контекст").pack(anchor="w", pady=(0, 5))
        context_controls = ttk.Frame(context_box)
        context_controls.pack(anchor="w")
        self.context_choice = tk.StringVar(value=self.settings.get("context_choice", "32K"))
        self.context_select = ttk.Combobox(context_controls, textvariable=self.context_choice,
            values=("32K", "64K", "96K", "128K", "Свой"), state="readonly", width=7)
        self.context_select.pack(side="left")
        self.custom_context = tk.StringVar(value=str(self.settings.get("custom_context", 32768)))
        self.context_entry = ttk.Entry(context_controls, textvariable=self.custom_context, width=9)
        self.context_entry.pack(side="left", padx=(5, 0))
        self.context_select.bind("<<ComboboxSelected>>", lambda e: self.context_state())
        self.context_state()
        actions = ttk.Frame(setup)
        actions.pack(fill="x")
        self.start_button = ttk.Button(actions, text="▶  Запустить тест", style="Accent.TButton",
                                       command=self.start, state="disabled")
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(actions, text="Остановить", command=self.cancel, state="disabled")
        self.stop_button.pack(side="left", padx=(8, 0))
        ttk.Label(actions, text=f"Прогрев + {RUNS} замера по {TOKENS} токенов",
                  style="Muted.TLabel").pack(side="right")
        self.managed_row = ttk.LabelFrame(setup, text="Запуск llama.cpp", padding=8)
        local_setup = has_managed_runtime(self.settings)
        self.managed = tk.BooleanVar(value=(self.settings.get("managed_llama", local_setup)
                                             if self.settings.get("managed_mode_explicit") else local_setup))
        self.managed_check = ttk.Checkbutton(self.managed_row, text="Запускать сервер автоматически",
                                             variable=self.managed, command=self.change_mode)
        self.managed_check.grid(row=0, column=0, columnspan=2, sticky="w")
        self.server_exe = tk.StringVar(value=self.settings.get("server_exe", ""))
        self.gguf_button = ttk.Button(self.managed_row, text="Выбрать GGUF…", command=self.choose_gguf)
        self.gguf_button.grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.server_stop_button = ttk.Button(self.managed_row, text="Выгрузить модель из памяти", command=self.stop_server)
        self.server_stop_button.grid(row=1, column=1, sticky="e", pady=(6, 0))
        self.advanced_button = ttk.Button(self.managed_row, text="Настройки сервера ▾", command=self.toggle_advanced)
        self.advanced_button.grid(row=2, column=0, sticky="w", pady=(6, 0))
        self.managed_details = ttk.Frame(self.managed_row)
        ttk.Label(self.managed_details, text="Программа llama-server.exe", style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        self.exe_entry = ttk.Entry(self.managed_details, textvariable=self.server_exe)
        self.exe_entry.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        self.exe_button = ttk.Button(self.managed_details, text="Изменить…", command=self.choose_exe)
        self.exe_button.grid(row=1, column=1, padx=(8, 0), pady=(4, 0))
        ttk.Label(self.managed_details, text="Адрес управляемого сервера", style="Muted.TLabel").grid(row=2, column=0, sticky="w", pady=(8, 0))
        self.managed_host_entry = ttk.Entry(self.managed_details, textvariable=self.host)
        self.managed_host_entry.grid(row=3, column=0, sticky="ew", pady=(4, 0))
        self.managed_details.columnconfigure(0, weight=1)
        self.managed_row.columnconfigure(0, weight=1)
        self.status = tk.StringVar(value="Получаем список моделей…")
        self.status_label = ttk.Label(frame, textvariable=self.status, wraplength=1020)
        self.status_label.pack(anchor="w")
        self.progress = ttk.Progressbar(frame, mode="indeterminate")
        ttk.Label(frame, text="Результат", style="Section.TLabel").pack(anchor="w", pady=(4, 8))
        metrics = ttk.Frame(frame)
        metrics.pack(fill="x")
        self.speed = tk.StringVar(value="—")
        self.prompt_speed = tk.StringVar(value="—")
        self.first = tk.StringVar(value="—")
        self.elapsed = tk.StringVar(value="0 с")
        for col, (label, variable) in enumerate([
            ("Генерация · ток/с", self.speed), ("Запрос · ток/с", self.prompt_speed),
            ("Первый ответ · сек", self.first), ("Время теста", self.elapsed)]):
            box = ttk.Frame(metrics, style="Card.TFrame", padding=(12, 10))
            box.grid(row=0, column=col, sticky="nsew", padx=(0, 8) if col < 3 else 0)
            metrics.columnconfigure(col, weight=1)
            ttk.Label(box, text=label, style="Card.Muted.TLabel").pack(anchor="w")
            ttk.Label(box, textvariable=variable, style="Card.Metric.TLabel").pack(anchor="w")
        self.speed_note = tk.StringVar(value="Итог — медиана трёх замеров после прогрева.")
        ttk.Label(frame, textvariable=self.speed_note, style="Muted.TLabel").pack(anchor="w", pady=(2, 8))
        self.model_details = tk.StringVar(value="Квант, размер модели и фактический контекст появятся после запуска.")
        ttk.Label(frame, textvariable=self.model_details, wraplength=int(1050*self.dpi_scale)).pack(anchor="w", pady=(0, 6))
        self.gpu = tk.StringVar(value="GPU: получение показателей…")
        self.ram = tk.StringVar(value="RAM: —")
        ttk.Label(frame, textvariable=self.gpu, wraplength=1020).pack(anchor="w")
        ttk.Label(frame, textvariable=self.ram).pack(anchor="w", pady=(4, 0))
        self.gpu_stats = tk.StringVar(value="GPU за тест: средняя — · пиковая —")
        ttk.Label(frame, textvariable=self.gpu_stats).pack(anchor="w", pady=(4, 0))
        self.telemetry_note = tk.StringVar(value="GPU и системная RAM: весь компьютер. Показатели обновляются во время теста.")
        ttk.Label(frame, textvariable=self.telemetry_note, style="Muted.TLabel", wraplength=1020).pack(anchor="w", pady=(4, 8))
        self.memory_title = tk.StringVar(value="Модели, загруженные в память")
        ttk.Label(frame, textvariable=self.memory_title).pack(anchor="w", pady=(0, 6))
        self.memory = self.table(frame, [
            ("model", "Модель", 400), ("gpu", "GPU, GiB", 105),
            ("ram", "RAM ≈, GiB", 110), ("share", "Доля GPU", 100),
            ("context", "Контекст", 115)], height=2)
        self.memory_note = tk.StringVar(value="RAM ≈: оценка размещения Ollama; общая системная RAM показана отдельно.")
        ttk.Label(frame, textvariable=self.memory_note,
                  style="Muted.TLabel").pack(anchor="w", pady=(5, 8))
        ttk.Label(frame, text="Три замера", style="Section.TLabel").pack(anchor="w", pady=(2, 6))
        self.results = self.table(frame, [
            ("run", "№", 45), ("prompt", "Вход", 85), ("processed", "Обработано", 100),
            ("cached", "Из кеша", 80), ("tokens", "Ответ", 80), ("speed", "Ген., ток/с", 110),
            ("prompt_speed", "Запрос, ток/с", 120), ("first", "Первый ответ, с", 120),
            ("context", "Контекст", 100)], height=3)
        ttk.Label(frame, text="Подробности и замечания", style="Section.TLabel").pack(anchor="w", pady=(12, 0))
        self.notes = tk.Text(frame, height=3, background=PANEL, foreground=MUTED,
                             relief="flat", font=("Segoe UI", 10), padx=10, pady=8, wrap="word")
        self.notes.pack(fill="both", expand=True, pady=(6, 0))
        self.log("Контекст — выделенное окно; тест не заполняет его целиком. "
                 "Prompt всего / обработано / из кеша показаны отдельно. TTFT учитывает первый текст или thinking.")
        self.models_tab = ModelsTab(self, self.notebook)
        self.notebook.add(self.models_tab, text="Модели")
        self.notebook.bind("<<NotebookTabChanged>>", self.tab_changed)

    def tab_changed(self, event=None):
        if self.notebook.select() == str(self.models_tab) and not self.models_tab.loaded:
            self.models_tab.refresh()

    def context_state(self):
        if self.context_choice.get() == "Свой":
            self.context_entry.pack(side="left", padx=(5, 0), after=self.context_select)
            self.context_entry.configure(state="disabled" if self.busy else "normal")
        else:
            self.context_entry.pack_forget()

    def toggle_advanced(self):
        if self.managed_details.winfo_manager():
            self.managed_details.grid_forget()
            self.advanced_button.configure(text="Настройки сервера ▾")
        else:
            self.managed_details.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(6, 0))
            self.advanced_button.configure(text="Настройки сервера ▴")

    def connection_controls(self):
        if self.backend.get() == "llama.cpp" and self.managed.get():
            self.host_label.pack_forget()
            self.host_entry.pack_forget()
            self.managed_hint.pack(side="left", fill="x", expand=True, before=self.refresh_button)
        else:
            self.managed_hint.pack_forget()
            self.host_label.pack(side="left", padx=(0, 8), before=self.refresh_button)
            self.host_entry.pack(side="left", fill="x", expand=True, before=self.refresh_button)

    def selected_context(self):
        choice = self.context_choice.get()
        try:
            value = int(self.custom_context.get()) if choice == "Свой" else int(choice[:-1]) * 1024
            if not 1 <= value <= 2147483647:
                raise ValueError()
            return value
        except ValueError:
            raise ValueError("Введите контекст целым числом токенов от 1 до 2147483647.")

    def persist(self):
        self.backend_hosts[self.current_backend] = self.host.get()
        if self.current_backend == "llama.cpp":
            self.settings["managed_host" if self.managed.get() else "external_host"] = self.host.get()
        self.settings.update(backend_hosts=self.backend_hosts, last_backend=self.current_backend,
                             server_exe=self.server_exe.get(),
                             managed_llama=self.managed.get(),
                             context_choice=self.context_choice.get(), custom_context=self.custom_context.get())
        try:
            save_settings(ROOT / "settings.json", self.settings)
        except OSError as exc:
            self.status.set("Не удалось сохранить настройки: " + str(exc))

    def choose_exe(self):
        path = filedialog.askopenfilename(title="Выберите llama-server", filetypes=[("Программа", "*.exe")])
        if path:
            self.server_exe.set(path)
            self.persist()

    def choose_gguf(self):
        path = filedialog.askopenfilename(title="Выберите GGUF", filetypes=[("GGUF", "*.gguf")])
        if path:
            self.use_gguf(path)

    def use_gguf(self, path):
        if self.busy or self.refreshing or self.inventory_busy:
            return
        path = str(Path(path).resolve())
        try:
            meta = gguf_metadata(path)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Не удалось выбрать модель", str(exc), parent=self.root)
            return
        if not testable_gguf({"name": Path(path).name, "architecture": meta.get("general.architecture", "")}):
            messagebox.showinfo("Вспомогательный GGUF", "Это файл проектора, а не сама языковая модель. "
                                "Выберите основной GGUF без mmproj.", parent=self.root)
            return
        known = self.settings.setdefault("known_files", [])
        if path not in known and not any(Path(path).is_relative_to(Path(p).resolve()) for p in self.settings.get("model_dirs", [])):
            known.append(path)
        self.backend_hosts[self.current_backend] = self.host.get()
        self.current_backend = "llama.cpp"
        self.backend.set("llama.cpp")
        self.managed.set(True)
        self.settings["managed_mode_explicit"] = True
        self.host.set(self.settings.get("managed_host", "http://127.0.0.1:8081"))
        self.managed_row.pack(fill="x", pady=(8, 0), before=self.controls)
        self.log_row.pack_forget()
        self.connection_controls()
        if path not in self.gguf_paths:
            self.gguf_paths.append(path)
        self.update_local_models()
        self.model.set(next(label for label, value in self.model_paths.items() if value == path))
        self.model_details.set("Метаданные выбранной GGUF-модели появятся при запуске теста.")
        self.memory_note.set("llama.cpp: RAM/VRAM модели — буферы весов из лога управляемого сервера.")
        self.results.delete(*self.results.get_children())
        self.memory.delete(*self.memory.get_children())
        self.speed.set("—")
        self.prompt_speed.set("—")
        self.first.set("—")
        self.start_button.configure(state="normal")
        self.notebook.select(self.test_tab)
        self.persist()

    def update_local_models(self):
        if self.backend.get() == "llama.cpp" and self.managed.get() and not self.busy:
            previous = self.model_paths.get(self.model.get())
            self.model_paths = model_labels(self.gguf_paths)
            self.models.configure(values=list(self.model_paths), state="readonly")
            if previous in self.model_paths.values():
                self.model.set(next(label for label, path in self.model_paths.items() if path == previous))
            elif self.model.get() not in self.model_paths:
                self.model.set(next(iter(self.model_paths), ""))

    def update_runtime_note(self):
        if self.backend.get() != "llama.cpp" or not self.managed.get():
            self.runtime_note.set("")
            return
        model = self.model_paths.get(self.model.get())
        if not model:
            self.runtime_note.set("Runtime: выберите GGUF")
            return
        try:
            profile = runtime_for(self.settings, model, self.server_exe.get())
            suffix = "  ·  MTP: будет проверен во время теста" if "mtp" in profile["capabilities"] else ""
            self.runtime_note.set("Runtime: " + profile["name"] + suffix)
        except ValueError as exc:
            self.runtime_note.set(str(exc))

    def change_mode(self):
        self.settings["managed_mode_explicit"] = True
        self.models.configure(state="readonly" if self.managed.get() else "normal")
        if self.managed.get():
            self.settings["external_host"] = self.host.get()
            self.host.set(self.settings.get("managed_host", "http://127.0.0.1:8081"))
            self.log_row.pack_forget()
        else:
            self.settings["managed_host"] = self.host.get()
            self.host.set(self.settings.get("external_host", "http://127.0.0.1:8080"))
            self.log_row.pack(fill="x", pady=(8, 0), before=self.controls)
        self.model.set("")
        self.model_paths = {}
        self.connection_controls()
        self.persist()
        self.refresh()

    def stop_server(self):
        if self.busy or self.inventory_busy or self.refreshing:
            return
        self.inventory_busy = True
        self.status.set("Выгрузка управляемого llama-server…")
        def work():
            try:
                self.server.stop()
                self.emit("server_stopped", "Управляемый сервер выгружен.")
            except Exception as exc:
                self.emit("server_stopped", str(exc))
        threading.Thread(target=work, daemon=True).start()

    def choose_log(self):
        path = filedialog.askopenfilename(title="Лог текущего запуска llama-server",
                                         filetypes=[("Логи", "*.log *.txt"), ("Все файлы", "*")])
        if path:
            self.log_path.set(path)

    def make_client(self):
        if self.backend.get() == "llama.cpp":
            return LlamaCppClient(self.host.get(), self.log_path.get().strip(), self.selected_context())
        return Client(self.host.get(), self.selected_context())

    def change_backend(self, event=None):
        if self.busy or self.inventory_busy or self.refreshing:
            self.backend.set(self.current_backend)
            return
        if self.current_backend == "llama.cpp":
            self.settings["managed_host" if self.managed.get() else "external_host"] = self.host.get()
        self.backend_hosts[self.current_backend] = self.host.get()
        self.current_backend = self.backend.get()
        if self.current_backend == "llama.cpp" and self.managed.get():
            self.settings.setdefault("external_host", self.backend_hosts["llama.cpp"])
            self.host.set(self.settings.get("managed_host", "http://127.0.0.1:8081"))
        else:
            self.host.set(self.backend_hosts[self.current_backend])
        self.model.set("")
        self.model_paths = {}
        self.models["values"] = []
        self.models.configure(state="normal" if self.current_backend == "llama.cpp" and not self.managed.get() else "readonly")
        if self.current_backend == "llama.cpp":
            self.managed_row.pack(fill="x", pady=(8, 0), before=self.controls)
            if not self.managed.get():
                self.log_row.pack(fill="x", pady=(8, 0), before=self.controls)
            self.memory_note.set("llama.cpp: RAM/VRAM модели — буферы весов из лога; без лога API не сообщает размещение.")
        else:
            self.managed_row.pack_forget()
            self.log_row.pack_forget()
            self.memory_note.set("RAM ≈: оценка размещения Ollama; общая системная RAM показана отдельно.")
        self.connection_controls()
        self.model_details.set("Метаданные выбранной модели появятся при запуске теста.")
        self.results.delete(*self.results.get_children())
        self.speed.set("—")
        self.prompt_speed.set("—")
        self.first.set("—")
        self.gpu_stats.set("GPU за тест: средняя — · пиковая —")
        self.memory.delete(*self.memory.get_children())
        self.memory_title.set("Модели в памяти: получение данных…")
        self.gpu.set("GPU: получение данных выбранного сервера…")
        self.ram.set("Системная RAM: —")
        self.telemetry_note.set("")
        self.refresh()

    def show_model_info(self, info):
        path = info.get("model_path")
        if path and self.client and self.client.local:
            file = Path(path)
            if file.is_file() and file.suffix.lower() == ".gguf":
                canonical = str(file.resolve())
                known = self.settings.setdefault("known_files", [])
                if canonical not in known and not any(file.resolve().is_relative_to(Path(d).resolve())
                                                      for d in self.settings.get("model_dirs", [])):
                    known.append(canonical)
                    self.persist()
        context = info.get("context_limit")
        size = gib(info.get("model_size_bytes"))
        basis = "на диске" if self.backend.get() == "Ollama" else "веса"
        self.model_details.set(f"Квант: {info.get('quantization') or '—'}  ·  Размер: {size} GiB ({basis})  ·  "
                               f"Фактический контекст: {context if context is not None else 'неизвестен'} токенов\n"
                               f"Работа на GPU: {info.get('offload', '—')}" +
                               (f"\nRuntime: {info['runtime_profile']}  ·  MTP: {info.get('mtp_status', '—')}"
                                if info.get("runtime_profile") else ""))

    def show_gpu_summary(self, values):
        parts = [f"GPU {v['index']}: средняя {decimal(v['mean_utilization_percent'], '%')} / "
                 f"пик {decimal(v['peak_utilization_percent'], '%')} ({v['sample_count']} снимков)" for v in values]
        self.gpu_stats.set("За тест, включая прогрев · " + ("; ".join(parts) or "нет аппаратных данных"))

    def table(self, parent, columns, height):
        wrapper = ttk.Frame(parent)
        wrapper.pack(fill="x")
        tree = ttk.Treeview(wrapper, columns=[c[0] for c in columns], show="headings", height=height)
        for name, title, width in columns:
            tree.heading(name, text=title)
            tree.column(name, width=width, minwidth=70, anchor="w" if name == "model" else "center")
        scroll = ttk.Scrollbar(wrapper, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side="left", fill="x", expand=True)
        scroll.pack(side="right", fill="y")
        return tree

    def log(self, text):
        self.notes.configure(state="normal")
        self.notes.insert("end", text + "\n")
        self.notes.see("end")
        self.notes.configure(state="disabled")

    def emit(self, event, value):
        self.events.put((event, value))

    def refresh(self):
        if self.busy or self.refreshing or self.inventory_busy:
            return
        try:
            client = self.make_client()
        except ValueError as exc:
            self.status.set(str(exc))
            return
        self.client = client
        self.refreshing = True
        for widget in (self.managed_check, self.gguf_button):
            widget.configure(state="disabled")
        self.backend_select.configure(state="disabled")
        self.host_entry.configure(state="disabled")
        self.refresh_button.configure(state="disabled")
        self.start_button.configure(state="disabled")
        self.status.set("Получение списка моделей…")
        managed = self.backend.get() == "llama.cpp" and self.managed.get()
        directories = list(self.settings.get("model_dirs", []))
        known = list(self.settings.get("known_files", []))

        def work():
            try:
                if managed:
                    rows, errors = scan_gguf(directories, known)
                    names = [r["path"] for r in sorted(rows, key=lambda r: r["size"], reverse=True)
                             if testable_gguf(r)]
                    if errors and not names:
                        raise RuntimeError("; ".join(errors))
                else:
                    names = [m["name"] for m in client.list_models()]
                    self.emit("telemetry", Telemetry(client).snapshot())
                self.emit("models", names)
            except Exception as exc:
                self.emit("refresh_error", str(exc))

        threading.Thread(target=work, daemon=True).start()

    def start(self):
        if self.busy or self.refreshing or self.inventory_busy or not self.model.get():
            return
        try:
            self.client = self.make_client()
            model_path = self.model_paths.get(self.model.get(), self.model.get())
            managed = self.backend.get() == "llama.cpp" and self.managed.get()
            profile = runtime_for(self.settings, model_path, self.server_exe.get()) if managed else None
        except ValueError as exc:
            self.status.set(str(exc))
            return
        self.busy = True
        self.persist()
        self.stop.clear()
        self.started = time.perf_counter()
        self.speed.set("—")
        self.prompt_speed.set("—")
        self.test_samples = []
        self.gpu_stats.set("GPU за тест: сбор показателей…")
        self.model_details.set(f"Выбранный контекст: {self.client.context}. Фактический лимит проверяется…")
        self.first.set("—")
        self.speed_note.set("Прогрев не входит в итоговую скорость.")
        self.results.delete(*self.results.get_children())
        self.notes.configure(state="normal")
        self.notes.delete("1.0", "end")
        self.notes.configure(state="disabled")
        self.log(f"Выбран контекст {self.client.context:,} токенов. Фактический лимит проверяется на сервере. "
                 "Прогрев + 3 замера по 512 токенов. Короткий запрос не заполняет весь контекст.")
        self.context_select.configure(state="disabled")
        self.context_state()
        for widget in (self.managed_check, self.exe_entry, self.exe_button, self.gguf_button, self.server_stop_button,
                       self.advanced_button, self.managed_host_entry):
            widget.configure(state="disabled")
        self.backend_select.configure(state="disabled")
        self.log_entry.configure(state="disabled")
        self.log_button.configure(state="disabled")
        self.models.configure(state="disabled")
        self.host_entry.configure(state="disabled")
        self.start_button.configure(state="disabled")
        self.refresh_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.progress.pack(fill="x", pady=(6, 10), after=self.status_label)
        self.progress.start(12)
        model, executable, host = self.model_paths.get(self.model.get(), self.model.get()), self.server_exe.get(), self.host.get()
        context = self.client.context
        if managed:
            self.settings["managed_host"] = host
            self.persist()
        def work():
            try:
                selected = model
                if managed:
                    self.client, selected = self.server.ensure(executable, model, host, context, self.stop, self.emit,
                                                                profile=profile)
                run_benchmark(self.client, selected, self.emit, self.stop, ROOT / "results")
            except Exception as exc:
                self.emit("finished", {"status": "cancelled" if isinstance(exc, Cancelled) or self.stop.is_set() else "error",
                                       "error": str(exc), "warnings": [], "backend": self.client.backend})
        threading.Thread(target=work, daemon=True).start()

    def cancel(self):
        if self.busy:
            self.stop.set()
            self.client.cancel()
            self.status.set("Остановка запроса…")
            self.stop_button.configure(state="disabled")

    def close(self):
        if self.inventory_busy:
            self.status.set("Дождитесь окончания операции с моделями перед закрытием.")
            return
        if self.busy:
            self.closing = True
            self.cancel()
        else:
            self.shutdown()

    def shutdown(self):
        self.persist()
        self.root.withdraw()
        def work():
            try:
                self.server.stop()
            finally:
                self.emit("shutdown", None)
        threading.Thread(target=work, daemon=True).start()

    def show_telemetry(self, value):
        self.last_sample = value
        gpu_lines = []
        for gpu in value["gpus"]:
            utilization = gpu["utilization_percent"]
            load = "—" if utilization is None else f"{utilization:.0f}%"
            gpu_lines.append(f"{gpu['name']}  ·  GPU {load}  ·  VRAM {gib(gpu['used_bytes'])} / {gib(gpu['total_bytes'])} GiB")
        self.gpu.set("\n".join(gpu_lines) or "GPU: телеметрия недоступна (нужна локальная NVIDIA / nvidia-smi)")
        ram = value["ram"]
        self.ram.set(f"Системная RAM: {gib(ram['used_bytes'])} / {gib(ram['total_bytes'])} GiB  ·  {ram['percent']}%"
                     if ram else "Системная RAM: нет данных")
        self.telemetry_note.set("GPU и RAM: весь компьютер. " + (" | ".join(value["errors"]) if value["errors"] else ""))
        self.memory.delete(*self.memory.get_children())
        gpu_count = ram_count = unknown = 0
        for model in value["models"] or []:
            mem = placement(model)
            gpu_count += int((mem["vram_bytes"] or 0) > 0)
            ram_count += int((mem["ram_estimate_bytes"] or 0) > 0)
            unknown += int(mem["vram_bytes"] is None or mem["ram_estimate_bytes"] is None)
            self.memory.insert("", "end", values=(model.get("name", "?"),
                gib(mem["vram_bytes"]), gib(mem["ram_estimate_bytes"]),
                decimal(mem["gpu_percent"], "%"), model.get("context_length") or "—"))
        count = len(value["models"] or [])
        self.memory_title.set(f"В памяти: {count} моделей  ·  подтверждено GPU: {gpu_count}  ·  RAM ≈: {ram_count}"
                              + (f"  ·  без данных: {unknown}" if unknown else "")
                              + "  (одна модель может быть в обеих)")
        if value["models"] is None:
            self.memory_title.set("Модели в памяти: данные сервера временно недоступны")

    def finish(self, report):
        self.last_report = report
        self.busy = False
        self.context_select.configure(state="readonly")
        self.context_state()
        for widget in (self.managed_check, self.exe_entry, self.exe_button, self.gguf_button, self.server_stop_button,
                       self.advanced_button, self.managed_host_entry):
            widget.configure(state="normal")
        self.elapsed.set(f"{time.perf_counter() - self.started:.0f} с")
        self.progress.stop()
        self.progress.pack_forget()
        self.progress.configure(value=0)
        self.models.configure(state="normal" if self.backend.get() == "llama.cpp" and not self.managed.get() else "readonly")
        self.backend_select.configure(state="readonly")
        self.log_entry.configure(state="normal")
        self.log_button.configure(state="normal")
        self.host_entry.configure(state="normal")
        self.start_button.configure(state="normal")
        self.refresh_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        if report["status"] == "completed":
            summary = report["summary"]
            self.speed.set(f"{summary['median_tokens_per_second']:.2f}")
            self.prompt_speed.set(decimal(summary.get("median_prompt_tokens_per_second")))
            self.first.set(decimal(summary.get("median_ttft_seconds"), " с"))
            self.speed_note.set(f"Медиана 3 замеров · диапазон {summary['min_tokens_per_second']:.2f}–"
                                f"{summary['max_tokens_per_second']:.2f} токен/с · прогрев исключён")
            self.status.set("Тест завершён. Можно выбрать следующую модель.")
        else:
            self.speed_note.set("Тест не завершён. Показаны только выполненные замеры.")
            if not report.get("model_info"):
                self.model_details.set("Модель не загружена; фактический контекст не подтверждён.")
            self.status.set("Тест остановлен." if report["status"] == "cancelled" else
                            "Не удалось провести тест: " + report["error"].splitlines()[0])
            if report.get("error"):
                self.log(report["error"])
        for warning in report["warnings"]:
            self.log(warning)
        if report.get("saved_to"):
            self.log("Отчёт: " + report["saved_to"])
        self.show_gpu_summary(report.get("gpu_summary", []))
        if report.get("backend", "ollama") == "ollama":
            self.log("Модель остаётся загруженной до 2 минут. Другие модели автоматически не выгружаются.")
        else:
            self.log("Управляемый llama-server перезапускается при смене контекста; внешний требует перезапуска владельцем.")
        self.telemetry_note.set(self.telemetry_note.get() + "  Последний снимок теста; обновить — кнопкой выше.")
        if self.closing:
            self.shutdown()

    def pump(self):
        try:
            while True:
                event, value = self.events.get_nowait()
                if event == "shutdown":
                    self.root.destroy()
                    return
                elif event == "server_stopped":
                    self.inventory_busy = False
                    self.status.set(value)
                elif event.startswith("inventory_"):
                    self.models_tab.handle(event, value)
                elif event == "status" and not self.stop.is_set():
                    self.status.set(value)
                elif event == "telemetry":
                    self.show_telemetry(value)
                    if self.busy:
                        self.test_samples.append(value)
                        self.show_gpu_summary(gpu_summary(self.test_samples))
                elif event == "model_info":
                    self.show_model_info(value)
                elif event in ("models", "refresh_error"):
                    self.refreshing = False
                    for widget in (self.managed_check, self.gguf_button):
                        widget.configure(state="normal")
                    self.backend_select.configure(state="readonly")
                    self.host_entry.configure(state="normal")
                    self.refresh_button.configure(state="normal")
                    if event == "refresh_error":
                        self.status.set("Не удалось получить модели: " + value)
                        if self.backend.get() == "llama.cpp" and not self.managed.get():
                            self.start_button.configure(state="normal")
                        self.log(value)
                    else:
                        if self.backend.get() == "llama.cpp" and self.managed.get():
                            self.gguf_paths = value
                            self.update_local_models()
                        else:
                            self.models["values"] = value
                            if self.model.get() not in value:
                                self.model.set(value[0] if value else "")
                        self.start_button.configure(state="normal" if value or (self.backend.get() == "llama.cpp" and not self.managed.get()) else "disabled")
                        self.status.set(f"Найдено моделей: {len(value)}. Выберите модель и начните тест."
                                        if value else ("GGUF не найдены. Добавьте папку на вкладке «Модели» или выберите файл через «Открыть GGUF…»."
                                                       if self.backend.get() == "llama.cpp" and self.managed.get()
                                                       else "Моделей нет. Загрузите модель на сервере."))
                elif event == "run":
                    self.speed.set(f"{value['tokens_per_second']:.2f}")
                    self.first.set(decimal(value["first_output_seconds"], " с"))
                    self.prompt_speed.set(decimal(value.get("prompt_tokens_per_second")))
                    self.speed_note.set(f"Скорость последнего замера ({value['index']}/{RUNS}); итог после трёх.")
                    self.results.insert("", "end", values=(value["index"],
                        value.get("prompt_tokens") if value.get("prompt_tokens") is not None else "—",
                        value.get("prompt_processed_tokens") if value.get("prompt_processed_tokens") is not None else "—",
                        value.get("prompt_cached_tokens") if value.get("prompt_cached_tokens") is not None else "—",
                        value["output_tokens"], decimal(value["tokens_per_second"]),
                        decimal(value.get("prompt_tokens_per_second")), decimal(value["first_output_seconds"]),
                        value.get("context_limit") or "—"))
                elif event == "finished":
                    self.finish(value)
        except queue.Empty:
            pass
        if self.busy:
            self.elapsed.set(f"{time.perf_counter() - self.started:.0f} с")
        self.root.after(80, self.pump)


def main():
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
