"""Behavior checks for the Python DT surface-check wrapper."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / "surface_check.py"


class SurfaceCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.assertTrue(SCRIPT.is_file(), "The Python surface-check entry point is missing")
        spec = importlib.util.spec_from_file_location("surface_check", SCRIPT)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.input_path = self.root / "surface with spaces.vtk"
        self.input_path.write_text("original surface", encoding="utf-8")
        self.executable = self.root / "DT with spaces.exe"
        self.executable.write_bytes(b"fake executable")
        self.log_path = self.root / "logs" / "check.log"
        self.original_cwd = Path.cwd()

    def arguments(self, *extra: str) -> list[str]:
        return [
            str(self.input_path),
            "--dt-executable",
            str(self.executable),
            "--log-path",
            str(self.log_path),
            *extra,
        ]

    def invoke(self, arguments: list[str], side_effect=None) -> tuple[int, str, mock.Mock]:
        output = io.StringIO()
        with (
            contextlib.redirect_stdout(output),
            contextlib.redirect_stderr(output),
            mock.patch.object(self.module.subprocess, "run", side_effect=side_effect) as run,
        ):
            result = self.module.main(arguments)
        self.assertEqual(self.original_cwd, Path.cwd())
        return result, output.getvalue(), run

    def fake_dt(self, log: str = "", return_code: int = 0):
        def run(arguments, **kwargs):
            self.assertEqual(self.original_cwd, Path.cwd())
            self.assertEqual(str(self.executable.resolve()), str(arguments[0]))
            self.assertEqual(str(self.input_path.resolve()), str(arguments[1]))
            self.assertEqual("--no-bounding-box", arguments[4])
            self.assertEqual(5, len(arguments))
            self.mesh_path = Path(arguments[2])
            self.mesh_path.write_text("temporary mesh", encoding="utf-8")
            self.addCleanup(lambda path=self.mesh_path: path.unlink(missing_ok=True))
            Path(arguments[3]).write_text(log, encoding="utf-8")
            return subprocess.CompletedProcess(arguments, return_code)

        return run

    def test_help_has_no_filesystem_or_process_side_effects(self) -> None:
        output = io.StringIO()
        before = set(self.root.rglob("*"))
        with (
            contextlib.redirect_stdout(output),
            mock.patch.object(self.module.subprocess, "run") as run,
            mock.patch.object(self.module.Path, "mkdir") as mkdir,
        ):
            with self.assertRaises(SystemExit) as caught:
                self.module.main(["--help"])
        self.assertEqual(0, caught.exception.code)
        self.assertIn("--log-path", output.getvalue())
        self.assertIn("--dt-executable", output.getvalue())
        run.assert_not_called()
        mkdir.assert_not_called()
        self.assertEqual(before, set(self.root.rglob("*")))

    def test_success_keeps_log_and_input_and_removes_temporary_mesh(self) -> None:
        log = (
            "[read] 1 ms\n[tetrahedralize] 2 ms\n[total] 3 ms\nHave 1 SubDomain\nFinal tet num: 4\n"
        )
        code, output, run = self.invoke(self.arguments(), self.fake_dt(log))
        self.assertEqual(0, code)
        self.assertIn("PASS:", output)
        self.assertIn("DT exit code: 0", output)
        self.assertIn("Final tet num: 4", output)
        self.assertEqual(log, self.log_path.read_text(encoding="utf-8"))
        self.assertEqual("original surface", self.input_path.read_text(encoding="utf-8"))
        self.assertFalse(self.mesh_path.exists())
        run.assert_called_once()

    def test_logged_errors_fail_even_when_dt_exits_zero(self) -> None:
        log = "[ERROR] Invalid mesh\nIntersection Face: 7 8 9 | Edge: 3 4\n"
        code, output, _ = self.invoke(self.arguments(), self.fake_dt(log))
        self.assertEqual(1, code)
        self.assertIn("FAIL:", output)
        self.assertIn("DT face identifiers: 7 8 9", output)
        self.assertIn("DT edge vertex identifiers: 3 4", output)
        self.assertFalse(self.mesh_path.exists())
        self.assertTrue(self.log_path.is_file())

    def test_native_crash_prints_last_ten_log_lines(self) -> None:
        log = "\n".join(f"trace-{index:02d}" for index in range(14))
        code, output, _ = self.invoke(self.arguments(), self.fake_dt(log, 3221226505))
        self.assertEqual(1, code)
        self.assertIn("DT exit code: 3221226505", output)
        self.assertIn("DT stopped without an error diagnostic", output)
        self.assertNotIn("trace-03", output)
        self.assertIn("trace-04", output)
        self.assertIn("trace-13", output)
        self.assertFalse(self.mesh_path.exists())

    def test_missing_log_preserves_native_exit_code_semantics(self) -> None:
        for native_code, expected in [(0, 0), (5, 1)]:
            with self.subTest(native_code=native_code):
                result = subprocess.CompletedProcess([], native_code)
                code, _, _ = self.invoke(self.arguments(), lambda *args, **kwargs: result)
                self.assertEqual(expected, code)

    def test_launch_error_is_wrapper_error(self) -> None:
        code, output, _ = self.invoke(self.arguments(), OSError("cannot launch DT"))
        self.assertEqual(2, code)
        self.assertIn("cannot launch DT", output)

    def test_wrapper_error_is_reported_only_on_stderr(self) -> None:
        self.input_path.unlink()
        output = io.StringIO()
        errors = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = self.module.main(self.arguments())
        self.assertEqual(2, code)
        self.assertEqual("", output.getvalue())
        self.assertIn("Error:", errors.getvalue())
        self.assertIn("Log:", errors.getvalue())

    def test_missing_input_does_not_launch_or_create_log_directory(self) -> None:
        self.input_path.unlink()
        code, output, run = self.invoke(self.arguments())
        self.assertEqual(2, code)
        self.assertIn("Error:", output)
        run.assert_not_called()
        self.assertFalse(self.log_path.parent.exists())

    def test_missing_dt_does_not_launch_or_create_log_directory(self) -> None:
        self.executable.unlink()
        code, _, run = self.invoke(self.arguments())
        self.assertEqual(2, code)
        run.assert_not_called()
        self.assertFalse(self.log_path.parent.exists())

    def test_input_or_executable_directory_is_rejected(self) -> None:
        for argument_index in [0, 2]:
            with self.subTest(argument_index=argument_index):
                arguments = self.arguments()
                arguments[argument_index] = str(self.root)
                code, output, run = self.invoke(arguments)
                self.assertEqual(2, code)
                self.assertIn("directory", output)
                run.assert_not_called()

    def test_log_cannot_overwrite_input_or_executable(self) -> None:
        for protected in [self.input_path, self.executable]:
            with self.subTest(protected=protected):
                previous = protected.read_bytes()
                arguments = self.arguments()
                arguments[4] = str(protected)
                code, output, run = self.invoke(arguments)
                self.assertEqual(2, code)
                self.assertIn("Log path must differ", output)
                run.assert_not_called()
                self.assertEqual(previous, protected.read_bytes())

    def test_existing_log_is_not_overwritten(self) -> None:
        self.log_path.parent.mkdir()
        self.log_path.write_text("retain me", encoding="utf-8")
        code, output, run = self.invoke(self.arguments())
        self.assertEqual(2, code)
        self.assertIn("Log already exists", output)
        run.assert_not_called()
        self.assertEqual("retain me", self.log_path.read_text(encoding="utf-8"))

    def test_legacy_option_aliases(self) -> None:
        arguments = self.arguments()
        arguments[1] = "-DtExecutable"
        arguments[3] = "-LogPath"
        code, _, _ = self.invoke(arguments, self.fake_dt())
        self.assertEqual(0, code)

    def test_defaults_resolve_from_tool_directory(self) -> None:
        self.executable = self.root / "resources" / "dt" / "DtTetrahedralize.exe"
        self.executable.parent.mkdir(parents=True)
        self.executable.write_bytes(b"fake executable")
        with mock.patch.object(self.module, "TOOL_ROOT", self.root):
            code, _, _ = self.invoke([str(self.input_path)], self.fake_dt())
        self.assertEqual(0, code)
        logs = list((self.root / "output" / "surfacecheck").glob("*.log"))
        self.assertEqual(1, len(logs))
        self.assertTrue(logs[0].name.startswith(self.input_path.stem + "_"))

    def test_cleanup_error_overrides_pass(self) -> None:
        with mock.patch.object(self.module.Path, "unlink", side_effect=PermissionError("locked")):
            code, output, _ = self.invoke(self.arguments(), self.fake_dt())
        self.assertEqual(2, code)
        self.assertIn("Temporary mesh cleanup failed", output)
        self.assertIn("locked", output)
        self.mesh_path.unlink()

    def test_cleanup_error_uses_stderr_while_dt_report_uses_stdout(self) -> None:
        output = io.StringIO()
        errors = io.StringIO()
        with (
            contextlib.redirect_stdout(output),
            contextlib.redirect_stderr(errors),
            mock.patch.object(self.module.subprocess, "run", side_effect=self.fake_dt()),
            mock.patch.object(self.module.Path, "unlink", side_effect=PermissionError("locked")),
        ):
            code = self.module.main(self.arguments())
        self.assertEqual(2, code)
        self.assertIn("PASS:", output.getvalue())
        self.assertNotIn("cleanup", output.getvalue())
        self.assertIn("Error: Temporary mesh cleanup failed", errors.getvalue())
        self.assertIn("locked", errors.getvalue())
        self.mesh_path.unlink()


if __name__ == "__main__":
    unittest.main()
