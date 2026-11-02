"""Shared CLI contracts for the maintained Python entry points."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = (
    "mesh_convert.py",
    "quality.py",
    "surface_check.py",
    "bounding_box.py",
)


class CliContractTests(unittest.TestCase):
    def run_script(self, path, *arguments, cwd):
        return subprocess.run(
            [sys.executable, "-X", "utf8", "-B", "-S", str(ROOT / path), *arguments],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    def test_help_without_site_packages_has_no_side_effects(self):
        with tempfile.TemporaryDirectory() as folder:
            for path in SCRIPTS:
                with self.subTest(path=path):
                    result = self.run_script(path, "--help", cwd=folder)
                    self.assertEqual(0, result.returncode, result.stderr)
                    self.assertIn("usage:", result.stdout.lower())
            self.assertEqual([], list(Path(folder).iterdir()))

    def test_expected_file_errors_do_not_print_tracebacks(self):
        with tempfile.TemporaryDirectory() as folder:
            commands = (
                (
                    "mesh_convert.py",
                    "missing.vtk",
                    "out.nas",
                    "--input-format",
                    "VTK",
                    "--output-format",
                    "NAS",
                ),
            )
            for path, *arguments in commands:
                with self.subTest(path=path):
                    result = self.run_script(path, *arguments, cwd=folder)
                    self.assertEqual(1, result.returncode)
                    self.assertNotIn("Traceback", result.stderr)
                    self.assertTrue(result.stderr)
            self.assertEqual([], list(Path(folder).iterdir()))

    def test_vtk_to_nas_refuses_to_overwrite_input(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "mesh.vtk"
            original = b"# vtk DataFile Version 3.0\ntetra\nASCII\nDATASET UNSTRUCTURED_GRID\nPOINTS 1 double\n0 0 0\nCELLS 0 0\nCELL_TYPES 0\n"
            source.write_bytes(original)
            result = self.run_script(
                "mesh_convert.py",
                str(source),
                str(source),
                "--input-format",
                "VTK",
                "--output-format",
                "NAS",
                cwd=folder,
            )
            self.assertEqual(1, result.returncode)
            self.assertEqual(original, source.read_bytes())

    def test_quality_refuses_to_overwrite_input_with_histogram(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "mesh.vtk"
            original = (
                b"# vtk DataFile Version 3.0\ntetra\nASCII\nDATASET UNSTRUCTURED_GRID\n"
                b"POINTS 4 double\n0 0 0\n1 0 0\n0 1 0\n0 0 1\n"
                b"CELLS 1 5\n4 0 1 2 3\nCELL_TYPES 1\n10\n"
            )
            source.write_bytes(original)
            result = self.run_script(
                "quality.py",
                str(source),
                "--output",
                str(source),
                cwd=folder,
            )
            self.assertEqual(1, result.returncode)
            self.assertEqual(original, source.read_bytes())


if __name__ == "__main__":
    unittest.main()
