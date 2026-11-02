"""Check a VTK surface with DT and retain its diagnostic log.

Exit codes are 0 for PASS, 1 for a DT failure, and 2 for a wrapper,
path, or cleanup error. This is DT acceptance testing, not an independent
geometric quality audit. Reported identifiers belong to the DT mesh.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Sequence

TOOL_ROOT = Path(__file__).resolve().parent
ERROR_PATTERN = re.compile(r"\[error\]|Intersection Face:", re.IGNORECASE)
SUMMARY_PATTERN = re.compile(
    r"^\[(read|tetrahedralize|total)\]|Have \d+ SubDomain|Final tet num",
    re.IGNORECASE,
)
INTERSECTION_PATTERN = re.compile(r"Intersection Face:\s*(.*?)\s*\|\s*Edge:\s*(.*)$", re.IGNORECASE)


# Command-line arguments.


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Check a VTK surface with DT (no bounding box, layer={2}, refine=0).",
        epilog="Exit codes: 0=PASS, 1=DT failure, 2=wrapper/path/cleanup error.",
    )
    parser.add_argument("input", help="Input VTK surface; the input is not modified.")
    parser.add_argument(
        "--log-path",
        "-LogPath",
        help="New diagnostic log path (default: output/surfacecheck under this tool).",
    )
    parser.add_argument(
        "--dt-executable",
        "-DtExecutable",
        help="DT executable (default: resources/dt/DtTetrahedralize.exe under this tool).",
    )
    return parser


# Input validation and diagnostic reporting.


def resolve_file(value: str, label: str) -> Path:
    path = Path(value).resolve(strict=True)
    if path.is_dir():
        raise ValueError(f"{label} is a directory: {value}")
    if not path.is_file():
        raise ValueError(f"{label} is not a regular file: {value}")
    return path


def report_result(dt_exit_code: int, log_lines: list[str]) -> int:
    errors = [line for line in log_lines if ERROR_PATTERN.search(line)]
    result_code = 0 if dt_exit_code == 0 and not errors else 1
    if result_code == 0:
        print("PASS: DT completed successfully.")
    else:
        print("FAIL: DT reported an error or did not complete successfully.")
    print(f"DT exit code: {dt_exit_code}")
    for line in log_lines:
        if SUMMARY_PATTERN.search(line):
            print(line)
    for line in errors:
        print(line)
        match = INTERSECTION_PATTERN.search(line)
        if match:
            print(f"DT face identifiers: {match.group(1)}")
            print(f"DT edge vertex identifiers: {match.group(2)}")
    if result_code != 0 and not errors:
        print("DT stopped without an error diagnostic; inspect the retained log.")
        for line in log_lines[-10:]:
            print(line)
    return result_code


# DT execution and temporary-file cleanup.


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    temporary_mesh: Path | None = None
    log_path: Path | None = None
    result_code = 2
    try:
        input_path = resolve_file(args.input, "Input")
        executable_value = args.dt_executable
        if not executable_value or not executable_value.strip():
            executable_value = str(TOOL_ROOT / "resources" / "dt" / "DtTetrahedralize.exe")
        executable = resolve_file(executable_value, "DT executable")

        run_id = uuid.uuid4().hex
        if args.log_path and args.log_path.strip():
            requested_log = Path(args.log_path)
        else:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            log_name = f"{input_path.stem}_{timestamp}_{run_id}.log"
            requested_log = TOOL_ROOT / "output" / "surfacecheck" / log_name
        log_path = requested_log.resolve()
        # Resolve aliases before comparison; never replace an existing log or link.
        if log_path in (input_path, executable):
            raise ValueError("Log path must differ from the input and executable paths.")
        if requested_log.exists() or requested_log.is_symlink():
            raise ValueError(f"Log already exists; choose a new log path: {log_path}")
        log_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_mesh = Path(tempfile.gettempdir()) / f"dt_surfacecheck_{run_id}.vtk"

        print(f"Input: {input_path}")
        print("Mode: no bounding box, layer={2}, refine=0")
        print(f"Log: {log_path}")
        # Inherit the caller's working directory and native stdout/stderr. In
        # particular, a Windows fast-fail exit is a diagnostic result, not an
        # exception from the wrapper.
        completed = subprocess.run(
            [
                str(executable),
                str(input_path),
                str(temporary_mesh),
                str(log_path),
                "--no-bounding-box",
            ],
            check=False,
            stdout=sys.stdout,
            stderr=sys.stderr,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        log_lines = []
        if log_path.is_file():
            log_lines = log_path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
        result_code = report_result(completed.returncode, log_lines)
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Error: {error}", file=sys.stderr)
        if log_path is not None or args.log_path:
            print(f"Log: {log_path if log_path is not None else args.log_path}", file=sys.stderr)
    finally:
        if temporary_mesh is not None and temporary_mesh.is_file():
            try:
                temporary_mesh.unlink()
            except OSError as error:
                print(f"Error: Temporary mesh cleanup failed: {temporary_mesh}", file=sys.stderr)
                print(error, file=sys.stderr)
                result_code = 2
    return result_code


if __name__ == "__main__":
    raise SystemExit(main())
