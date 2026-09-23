"""Friendly Windows filename for the console-free launcher."""
from pathlib import Path
import runpy


if __name__ == "__main__":
    runpy.run_path(str(Path(__file__).with_name("launch.pyw")), run_name="__main__")
