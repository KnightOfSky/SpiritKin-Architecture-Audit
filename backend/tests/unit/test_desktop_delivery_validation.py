import argparse
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import validate_desktop_delivery as validator


def delivery_args(**overrides):
    values = dict(
        python="",
        node="node",
        dotnet="dotnet",
        quick=True,
        pytest_target=[],
        html_file=[],
        skip_dotnet=True,
        skip_wpf_structure=True,
        skip_wpf_smoke=True,
        skip_pytest=True,
        skip_manifest=True,
        skip_js=True,
        skip_launcher_smoke=True,
        with_launcher_smoke=False,
        dotnet_timeout=1,
        wpf_smoke_timeout=1,
        pytest_timeout=1,
        launcher_smoke_timeout=1,
    )
    values.update(overrides)
    return argparse.Namespace(**values)


class DesktopDeliveryValidationTests(unittest.TestCase):
    def test_extract_inline_scripts_skips_src_and_non_javascript_and_marks_modules(self):
        with tempfile.TemporaryDirectory() as tmp:
            html_path = Path(tmp) / "panel.html"
            html_path.write_text(
                """
                <script src="app.js"></script>
                <script type="application/json">{"ok": true}</script>
                <script>const answer = 42;</script>
                <script type="module">import x from './x.js';</script>
                """,
                encoding="utf-8",
            )

            scripts = validator.extract_inline_scripts(html_path)

        self.assertEqual(len(scripts), 2)
        self.assertFalse(scripts[0].is_module)
        self.assertTrue(scripts[1].is_module)
        self.assertIn("answer", scripts[0].code)

    def test_html_check_commands_write_js_and_mjs_temp_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            html_path = root / "desktop_console.html"
            out_dir = root / "checks"
            out_dir.mkdir()
            html_path.write_text(
                """
                <script>window.desktopReady = true;</script>
                <script type="module">import * as mod from './mod.js';</script>
                """,
                encoding="utf-8",
            )

            commands = validator.html_check_commands([html_path], "node", out_dir)

            output_files = [Path(command[-1]) for command in commands]

        self.assertEqual(len(commands), 2)
        self.assertEqual(output_files[0].suffix, ".js")
        self.assertEqual(output_files[1].suffix, ".mjs")

    def test_selected_pytest_targets_prefers_explicit_override(self):
        targets = validator.selected_pytest_targets(quick=False, explicit_targets=["one.py", "two.py::case"])

        self.assertEqual(targets, ("one.py", "two.py::case"))

    def test_run_delivery_checks_resolves_executables_only_when_needed(self):
        args = delivery_args(skip_launcher_smoke=False)

        with (
            patch.object(validator, "resolve_python", side_effect=AssertionError("python should not be resolved")),
            patch.object(validator, "resolve_executable", side_effect=AssertionError("executables should not be resolved")),
        ):
            results = validator.run_delivery_checks(args)

        self.assertEqual(results, [])

    def test_full_delivery_checks_run_launcher_smoke_by_default(self):
        args = delivery_args(quick=False, skip_launcher_smoke=False, launcher_smoke_timeout=7)
        smoke_result = validator.StepResult("desktop launcher start smoke", True, 0.1, 0)

        with (
            patch.object(validator, "resolve_python", return_value="python") as resolve_python,
            patch.object(validator, "run_launcher_smoke", return_value=[smoke_result]) as run_smoke,
        ):
            results = validator.run_delivery_checks(args)

        resolve_python.assert_called_once()
        run_smoke.assert_called_once_with("python", timeout=7)
        self.assertEqual(results, [smoke_result])

    def test_quick_delivery_checks_can_opt_into_launcher_smoke(self):
        args = delivery_args(skip_launcher_smoke=False, with_launcher_smoke=True, launcher_smoke_timeout=5)
        smoke_result = validator.StepResult("desktop launcher start smoke", True, 0.1, 0)

        with (
            patch.object(validator, "resolve_python", return_value="python"),
            patch.object(validator, "run_launcher_smoke", return_value=[smoke_result]) as run_smoke,
        ):
            results = validator.run_delivery_checks(args)

        run_smoke.assert_called_once_with("python", timeout=5)
        self.assertEqual(results, [smoke_result])

    def test_wpf_startup_smoke_runs_after_successful_build(self):
        args = delivery_args(skip_dotnet=False, skip_wpf_smoke=False, dotnet_timeout=3, wpf_smoke_timeout=4)
        build_result = validator.StepResult("WPF desktop build", True, 0.1, 0)
        smoke_result = validator.StepResult("WPF desktop startup smoke", True, 0.1, 0)

        with (
            patch.object(validator, "resolve_executable", return_value="dotnet") as resolve_executable,
            patch.object(validator, "run_command", side_effect=[build_result, smoke_result]) as run_command,
        ):
            results = validator.run_delivery_checks(args)

        resolve_executable.assert_called_once_with("dotnet", "dotnet")
        self.assertEqual([call.args[0] for call in run_command.call_args_list], ["WPF desktop build", "WPF desktop startup smoke"])
        self.assertIn("-o", run_command.call_args_list[0].args[1])
        self.assertIn(str(validator.WPF_DELIVERY_BUILD_DIR), run_command.call_args_list[0].args[1])
        self.assertEqual(run_command.call_args_list[1].args[1][0], str(validator.WPF_DELIVERY_BUILD_EXE))
        self.assertEqual(run_command.call_args_list[1].kwargs["timeout"], 4)
        self.assertEqual(results, [build_result, smoke_result])

    def test_wpf_startup_smoke_is_skipped_when_build_fails(self):
        args = delivery_args(skip_dotnet=False, skip_wpf_smoke=False, dotnet_timeout=3, wpf_smoke_timeout=4)
        build_result = validator.StepResult("WPF desktop build", False, 0.1, 1)

        with (
            patch.object(validator, "resolve_executable", return_value="dotnet"),
            patch.object(validator, "run_command", return_value=build_result) as run_command,
        ):
            results = validator.run_delivery_checks(args)

        run_command.assert_called_once()
        self.assertEqual(results, [build_result])

    def test_wpf_structure_check_accepts_split_files_and_xaml_handlers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "SpiritKinDesktop"
            (source / "Features").mkdir(parents=True)
            (source / "ViewModels").mkdir()
            xaml = source / "MainWindow.xaml"
            code = source / "MainWindow.xaml.cs"
            xaml.write_text(
                '<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation">'
                '<Button Click="Save_Click" />'
                "</Window>",
                encoding="utf-8",
            )
            code.write_text("public partial class MainWindow { private void Save_Click(object s, object e) {} }\n", encoding="utf-8")
            (source / "Features" / "Panel.cs").write_text("public partial class MainWindow { private void Other() {} }\n", encoding="utf-8")
            (source / "ViewModels" / "Vm.cs").write_text("public sealed class Vm {}\n", encoding="utf-8")

            errors = validator.wpf_split_structure_errors(
                source_dir=source,
                xaml_path=xaml,
                main_window_code=code,
                main_window_max_lines=5,
                split_file_max_lines=10,
            )

        self.assertEqual(errors, [])

    def test_wpf_structure_check_reports_missing_xaml_handler(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "SpiritKinDesktop"
            (source / "Features").mkdir(parents=True)
            (source / "ViewModels").mkdir()
            xaml = source / "MainWindow.xaml"
            code = source / "MainWindow.xaml.cs"
            xaml.write_text(
                '<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation">'
                '<Button Click="Missing_Click" />'
                "</Window>",
                encoding="utf-8",
            )
            code.write_text("public partial class MainWindow {}\n", encoding="utf-8")
            (source / "Features" / "Panel.cs").write_text("public partial class MainWindow {}\n", encoding="utf-8")
            (source / "ViewModels" / "Vm.cs").write_text("public sealed class Vm {}\n", encoding="utf-8")

            errors = validator.wpf_split_structure_errors(source_dir=source, xaml_path=xaml, main_window_code=code)

        self.assertTrue(any("Missing_Click" in error for error in errors))

    def test_wpf_structure_check_reports_oversized_split_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "SpiritKinDesktop"
            (source / "Features").mkdir(parents=True)
            (source / "ViewModels").mkdir()
            xaml = source / "MainWindow.xaml"
            code = source / "MainWindow.xaml.cs"
            xaml.write_text('<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation" />', encoding="utf-8")
            code.write_text("public partial class MainWindow {}\n", encoding="utf-8")
            (source / "Features" / "Large.cs").write_text("\n".join(["// line"] * 3), encoding="utf-8")
            (source / "ViewModels" / "Vm.cs").write_text("public sealed class Vm {}\n", encoding="utf-8")

            errors = validator.wpf_split_structure_errors(
                source_dir=source,
                xaml_path=xaml,
                main_window_code=code,
                split_file_max_lines=3,
            )

        self.assertTrue(any("Large.cs" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
