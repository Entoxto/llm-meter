# Windows folder build

The tested build produces `dist/ModelStudio/ModelStudio.exe` with its Qt and
Python dependencies in the same folder. Copy the **whole** `ModelStudio` folder;
the `.exe` alone is not sufficient. GGUF weights, llama-server, Ollama, OpenCode,
user settings, and the SQLite database are not bundled. By default, user data
is written under `%LOCALAPPDATA%/ModelStudio`.

## Rebuild

Tested on Windows 11 x64 with Python **3.14.7**, PySide6 **6.11.2**,
PyInstaller **6.22.3**, and `pyinstaller-hooks-contrib` **2026.7**. The exact
build dependencies are in `packaging/requirements-build.txt`.

From the repository root in PowerShell:

```powershell
py -3.14 -m venv .venv
.venv\Scripts\python.exe -m pip install -r packaging\requirements-build.txt
.venv\Scripts\python.exe -m pip install -e .
powershell -NoProfile -ExecutionPolicy Bypass -File tools\build_windows.ps1
```

The script checks the pinned PySide6 and PyInstaller versions, builds from
`packaging/ModelStudio.spec`, then starts the packaged app with `--demo`, an
isolated `--data-dir`, software rendering, and `--capture`. It requires a
nonempty screenshot before declaring success. Use `-SkipSmoke` only when the
offscreen capture is intentionally unavailable. The smoke data and image live
in a unique `build/pyinstaller/smoke-*` directory; they are not placed in the
distribution.

The spec derives source paths from its own location, so the checkout folder may
be renamed or moved. The build script temporarily restricts `PATH` to the
venv, Python, and Windows directories. This prevents unrelated DLLs already on
the developer's `PATH` from being included in the distribution. It restores
the original environment afterward.

## Why PyInstaller

The architecture proposed `pyside6-deploy` as the first candidate. Its
standalone dry run on this machine required Nuitka and reported that `dumpbin`
was unavailable; no C compiler was present on `PATH`. PyInstaller provided a
Windows folder build using pinned wheels from the Python package index, without
installing a compiler or changing the system Python. The current folder is
about 404 MiB because it contains Qt Quick and QML modules. This is a verified
developer build, not a signed installer. Run a final build after UI/QML changes
before distributing it.

## Verification scope

The automated smoke proves that the packaged process starts, loads the QML
view and SVG/Qt resources, renders a page, writes to an isolated SQLite data
directory, captures an image, and exits. Live model startup and GPU behavior
must be checked separately on the target computer with its chosen runtime.
The executable was also smoke-launched with a working directory under Windows
`%TEMP%`, outside the Python checkout, with `PYTHONPATH` unset.
