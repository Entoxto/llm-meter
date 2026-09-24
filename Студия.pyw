"""Launch the new desktop from a relocatable checkout, without a console."""
from pathlib import Path
import ctypes
import os
import subprocess
import sys
import traceback

root = Path(__file__).resolve().parent
pythonw = root / ".venv" / "Scripts" / "pythonw.exe"
try:
    if pythonw.is_file() and Path(sys.executable).resolve() != pythonw.resolve():
        subprocess.Popen([str(pythonw), str(Path(__file__).resolve())], cwd=root,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    else:
        sys.path.insert(0, str(root / "src"))
        from model_studio.bootstrap import main
        main()
except Exception:
    data = Path(os.environ.get("LOCALAPPDATA", str(root))) / "ModelStudio" / "logs"
    data.mkdir(parents=True, exist_ok=True)
    log = data / "launcher-error.log"
    log.write_text(traceback.format_exc(), encoding="utf-8")
    ctypes.windll.user32.MessageBoxW(0, "Не удалось открыть Модельную студию.\nПодробности: " + str(log), "Модельная студия", 0x10)
