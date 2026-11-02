"""Build the standalone console EXE, including native runtime dependencies."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from mesh_convert import find_cgns_dll

ROOT = Path(__file__).resolve().parent


def main() -> int:
    local_tools = ROOT / ".devtools"
    if (local_tools / "PyInstaller").is_dir():
        sys.path.insert(0, str(local_tools))
        os.environ["PYTHONPATH"] = os.pathsep.join(
            filter(None, (str(local_tools), os.environ.get("PYTHONPATH", "")))
        )
    try:
        import pefile
        import PyInstaller.__main__
    except ImportError:
        print("Install build dependencies: python -m pip install -r requirements.txt")
        return 1

    cgns_path = find_cgns_dll(os.environ.get("MESHTOOLS_CGNS_DLL"))
    if cgns_path.name.lower() != "cgns.dll":
        raise RuntimeError("Select an official cgns.dll with MESHTOOLS_CGNS_DLL")
    native_files = {}

    def collect_native(path):
        key = path.name.lower()
        if key in native_files:
            return
        native_files[key] = path
        with pefile.PE(str(path)) as library:
            for entry in getattr(library, "DIRECTORY_ENTRY_IMPORT", []):
                adjacent = path.parent / entry.dll.decode("ascii")
                if adjacent.is_file():
                    collect_native(adjacent)

    collect_native(cgns_path)
    arguments = [
        "--noconfirm",
        "--onefile",
        "--console",
        "--name",
        "MeshTools",
        "--distpath",
        str(ROOT.parent / "release"),
        "--workpath",
        str(ROOT / ".build"),
        "--specpath",
        str(ROOT / ".build"),
        "--paths",
        str(ROOT),
        "--add-data",
        f"{ROOT / 'resources/bounding_box_template.json'};resources",
    ]
    for path in native_files.values():
        arguments.extend(["--add-binary", f"{path};native/cgns"])
    for path in sorted((ROOT / "resources/dt").iterdir()):
        if path.suffix.lower() in {".exe", ".dll"}:
            arguments.extend(["--add-binary", f"{path};resources/dt"])
    for module in ("numpy", "meshio", "vtk"):
        arguments.extend(["--hidden-import", module])
    for module in ("tkinter", "matplotlib", "pytest", "IPython", "numpy.tests", "vtkmodules.test"):
        arguments.extend(["--exclude-module", module])
    arguments.append(str(ROOT / "tool.py"))
    PyInstaller.__main__.run(arguments)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
