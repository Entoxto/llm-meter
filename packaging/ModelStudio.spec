"""Reproducible PyInstaller folder build, relative to this file."""
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).resolve().parent
ENTRY = ROOT / "packaging" / "launcher.py"

hiddenimports = collect_submodules("model_studio") + [
    "PySide6.QtSvg",
    "engine", "llama_cpp", "inventory", "managed_server",
    "runtime_profiles", "model_aliases",
]

analysis = Analysis(
    [str(ENTRY)],
    pathex=[str(ROOT / "src"), str(ROOT)],
    binaries=[],
    datas=collect_data_files("model_studio"),
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
APP_ICON = ROOT / "src" / "model_studio" / "desktop" / "icons" / "model-studio.ico"
pyz = PYZ(analysis.pure)
exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="ModelStudio",
    icon=str(APP_ICON),
    debug=False,
    strip=False,
    upx=False,
    console=False,
)
coll = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name="ModelStudio",
)
