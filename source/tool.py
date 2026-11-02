"""Command-line mesh conversion, quality, surface checks and bounding boxes."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Sequence

from bounding_box import main as bounding_box_main
from mesh_convert import main as convert_main
from quality import main as quality_main
from surface_check import main as surface_check_main

COMMANDS = {
    "convert": (convert_main, "Convert between VTK, VTU, NAS, PLY, STL, OBJ, MSH, CGNS and FACET."),
    "quality": (quality_main, "Report mesh quality and write a PNG histogram."),
    "surface-check": (surface_check_main, "Check a surface using bundled DT and retain its log."),
    "bounding-box": (bounding_box_main, "Append a triangulated bounding box."),
}


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    program = Path(sys.executable).name if getattr(sys, "frozen", False) else "python tool.py"
    if not arguments or arguments[0].lower() in {"list", "help", "--help", "-h"}:
        print(f"Usage: {program} <command> [arguments]")
        for name, (_, description) in COMMANDS.items():
            print(f"  {name:16} {description}")
        print(f"Run {program} <command> --help for arguments.")
        print("Relative paths use the current working directory.")
        return 0
    command, *forwarded = arguments
    command = command.lower()
    if command not in COMMANDS:
        print(f"Error: unknown tool: {command}. Run {program} --help.", file=sys.stderr)
        return 2
    previous_argv = sys.argv
    try:
        sys.argv = [f"{program} {command}", *forwarded]
        return COMMANDS[command][0](forwarded)
    except (OSError, ValueError, RuntimeError, ImportError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    finally:
        sys.argv = previous_argv


if __name__ == "__main__":
    raise SystemExit(main())
