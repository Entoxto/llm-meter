"""Composition root and a repeatable, isolated desktop capture mode."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(description="Модельная студия")
    parser.add_argument("--data-dir", type=Path, help="Отдельный каталог данных")
    parser.add_argument("--capture", type=Path, help="Сохранить снимок своего окна и закрыться")
    parser.add_argument("--page", type=int, default=0)
    parser.add_argument("--demo", action="store_true", help="Изолированные демонстрационные данные для проверки макетов")
    args = parser.parse_args(argv)
    if args.data_dir:
        os.environ["MODEL_STUDIO_DATA_DIR"] = str(args.data_dir.resolve())
    if args.demo and (not args.data_dir or not args.capture):
        parser.error("--demo требует отдельный --data-dir и --capture")

    from PySide6.QtCore import QLockFile, QTimer, QUrl
    from PySide6.QtGui import QFont, QFontDatabase
    from PySide6.QtWidgets import QApplication, QMessageBox
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtQuickControls2 import QQuickStyle
    from model_studio.platform.paths import ensure_data_dirs
    from model_studio.storage import Store
    from model_studio.desktop.controllers import Studio

    QQuickStyle.setStyle("Fusion")
    app = QApplication([sys.argv[0]])
    if args.capture and os.name == "nt":
        for font in ("segoeui.ttf", "segoeuib.ttf", "seguisb.ttf"):
            QFontDatabase.addApplicationFont(str(Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / font))
    app.setOrganizationName("ModelStudio")
    app.setApplicationName("Модельная студия")
    app.setFont(QFont("Segoe UI", 10))
    paths = ensure_data_dirs()
    lock = QLockFile(str(paths["database"].with_suffix(".lock")))
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        QMessageBox.information(None, "Модельная студия", "Приложение с этим каталогом данных уже открыто.")
        return 1
    store = Store(paths["database"])
    store.recover_interrupted()
    if args.demo:
        store.save_settings({"backend_hosts": {"Ollama": "http://127.0.0.1:1"}})
    legacy_root = Path(__file__).resolve().parents[2]
    # Only a verified source checkout may auto-import the adjacent old app.
    auto_import = not args.data_dir and not getattr(sys, "frozen", False) and (legacy_root / "engine.py").is_file()
    bridge = Studio(store, paths, legacy_root, initialize=not args.demo, auto_import=auto_import)
    bridge.page = args.page
    if args.demo:
        from model_studio.desktop.preview import seed_preview
        seed_preview(bridge, args.page)
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("studio", bridge)
    engine.load(QUrl.fromLocalFile(str(Path(__file__).parent / "desktop" / "qml" / "Main.qml")))
    if not engine.rootObjects():
        bridge.workers.shutdown()
        return 2
    window = engine.rootObjects()[0]
    if args.capture:
        def capture():
            bridge._hash_cancel.set()
            args.capture.parent.mkdir(parents=True, exist_ok=True)
            if not window.grabWindow().save(str(args.capture.resolve())):
                print("Could not capture window", file=sys.stderr)
                app.exit(3)
            else:
                bridge._closed = True
                bridge.workers.shutdown()
                app.quit()
        QTimer.singleShot(2200, capture)
    result = app.exec()
    bridge.workers.shutdown()
    lock.unlock()
    return result


if __name__ == "__main__":
    raise SystemExit(main())
