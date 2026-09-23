"""Open LLM Meter without a console window (Windows Python Launcher / pyw.exe)."""
from pathlib import Path
import ctypes
import traceback


if __name__ == "__main__":
    try:
        from app import main
        main()
    except Exception:
        log = Path(__file__).resolve().parent / "results" / "launcher-error.log"
        try:
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text(traceback.format_exc(), encoding="utf-8")
            detail = f"Подробности: {log}"
        except OSError:
            detail = traceback.format_exc()[-1200:]
        ctypes.windll.user32.MessageBoxW(0, "Не удалось открыть LLM Meter.\n\n" + detail,
                                         "LLM Meter", 0x10)
