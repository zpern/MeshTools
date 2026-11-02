"""Integration checks for the Python CLI and relocated tools.

Run from any directory: python -B -m unittest discover -s source/tests -v
"""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
ALIASES = (
    "convert",
    "quality",
    "surface-check",
    "bounding-box",
)
TETRA = """# vtk DataFile Version 3.0
tetrahedron
ASCII
DATASET UNSTRUCTURED_GRID
POINTS 4 double
0 0 0
1 0 0
0 1 0
0 0 1
CELLS 1 5
4 0 1 2 3
CELL_TYPES 1
10
"""


class LauncherTests(unittest.TestCase):
    def run_tool(self, *arguments, cwd):
        entry = TOOLS / "tool.py"
        self.assertTrue(entry.is_file(), f"Launcher missing: {entry}")
        command = [sys.executable, "-X", "utf8", "-B", str(entry)]
        return subprocess.run(
            command + list(arguments),
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    def test_list_and_all_alias_help_are_read_only(self):
        with tempfile.TemporaryDirectory(prefix="launcher help ", dir=TOOLS / "tests") as folder:
            result = self.run_tool("list", cwd=folder)
            self.assertEqual(0, result.returncode, result.stdout)
            for alias in ALIASES:
                self.assertIn(alias, result.stdout)
                help_result = self.run_tool(alias, "--help", cwd=folder)
                self.assertEqual(0, help_result.returncode, help_result.stdout)
                self.assertIn(alias, help_result.stdout)
            self.assertEqual([], list(Path(folder).iterdir()))

    def test_forwards_flags_and_spaced_relative_paths_from_caller(self):
        with (
            tempfile.TemporaryDirectory(prefix="caller directory ", dir=TOOLS / "tests") as folder,
        ):
            root = Path(folder)
            (root / "input mesh.vtk").write_text(TETRA, encoding="ascii")
            (root / "output folder").mkdir()
            output = root / "output folder" / "plot with spaces.png"
            result = self.run_tool(
                "quality",
                "input mesh.vtk",
                "--output",
                "output folder/plot with spaces.png",
                cwd=folder,
            )
            self.assertEqual(0, result.returncode, result.stdout)
            self.assertTrue(output.is_file(), result.stdout)
            self.assertEqual(b"\x89PNG\r\n\x1a\n", output.read_bytes()[:8])
            self.assertFalse((root / "input mesh_skewness_histogram.png").exists())

    def test_propagates_child_exit_codes(self):
        with tempfile.TemporaryDirectory(prefix="launcher error ", dir=TOOLS / "tests") as folder:
            missing = self.run_tool("quality", "missing file.vtk", cwd=folder)
            self.assertEqual(1, missing.returncode, missing.stdout)
            bad_args = self.run_tool("quality", "--unknown-option", cwd=folder)
            self.assertEqual(2, bad_args.returncode, bad_args.stdout)
            unknown = self.run_tool("unknown-tool", cwd=folder)
            self.assertEqual(2, unknown.returncode, unknown.stdout)

    def test_python_status_output_handles_unicode_when_redirected(self):
        with tempfile.TemporaryDirectory(prefix="unicode output ", dir=TOOLS / "tests") as folder:
            root = Path(folder)
            (root / "输入网格.vtk").write_text(TETRA, encoding="ascii")
            result = self.run_tool(
                "convert",
                "输入网格.vtk",
                "输出网格.nas",
                "--input-format",
                "VTK",
                "--output-format",
                "NAS",
                cwd=folder,
            )
            self.assertEqual(0, result.returncode, result.stdout)
            self.assertIn("CTETRA", (root / "输出网格.nas").read_text(encoding="ascii"))

    def test_conversion_missing_input_fails_without_creating_output(self):
        with tempfile.TemporaryDirectory(
            prefix="conversion missing ", dir=TOOLS / "tests"
        ) as folder:
            result = self.run_tool(
                "convert",
                "missing.vtk",
                "输出网格.nas",
                "--input-format",
                "VTK",
                "--output-format",
                "NAS",
                cwd=folder,
            )
            self.assertEqual(1, result.returncode, result.stdout)
            self.assertFalse((Path(folder) / "输出网格.nas").exists())


if __name__ == "__main__":
    unittest.main()
