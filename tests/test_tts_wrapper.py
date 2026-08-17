import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "azure_tts_to_jianying", ROOT / "azure_tts_to_jianying.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class WrapperOptionTests(unittest.TestCase):
    def test_extracts_wrapper_options_and_forwards_the_rest(self):
        root, mkv, output, info, force, skip, forwarded = MODULE.extract_wrapper_options(
            [
                "--input",
                "稿.txt",
                "--narrato-root=/opt/narrato",
                "--mkv",
                "video.mkv",
                "--scene-output=/tmp/shots",
                "--force-scenes",
                "--wrapper-info",
            ]
        )
        self.assertEqual(root, "/opt/narrato")
        self.assertEqual(mkv, "video.mkv")
        self.assertEqual(output, "/tmp/shots")
        self.assertTrue(info)
        self.assertTrue(force)
        self.assertFalse(skip)
        self.assertEqual(forwarded, ["--input", "稿.txt"])

    def test_skip_scene_split_is_consumed(self):
        _root, _mkv, _output, _info, _force, skip, forwarded = (
            MODULE.extract_wrapper_options(["--skip-scene-split", "--text", "你好"])
        )
        self.assertTrue(skip)
        self.assertEqual(forwarded, ["--text", "你好"])

    def test_normalizes_quoted_interactive_path(self):
        self.assertEqual(MODULE.normalize_interactive_path(" '/tmp/a.mkv' "), "/tmp/a.mkv")


class RootResolutionTests(unittest.TestCase):
    def test_resolves_explicit_valid_project(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative in (
                MODULE.TARGET_RELATIVE_PATH,
                Path("app/models/schema.py"),
                Path("app/services/jianying_draft_builder.py"),
            ):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
            with mock.patch.dict(os.environ, {}, clear=True):
                self.assertEqual(MODULE.resolve_narrato_root(str(root)), root.resolve())

    def test_rejects_incomplete_explicit_project(self):
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.dict(os.environ, {"NARRATOAI_ROOT": directory}, clear=True):
                with mock.patch.object(MODULE.Path, "home", return_value=Path(directory)):
                    with self.assertRaises(RuntimeError):
                        MODULE.resolve_narrato_root(directory)


class SceneWorkflowTests(unittest.TestCase):
    def test_builds_splitter_command(self):
        with mock.patch.object(MODULE.subprocess, "run") as run:
            MODULE.run_scene_split("video.mkv", "/tmp/result", force=True)
        command = run.call_args.args[0]
        self.assertEqual(command[0], MODULE.sys.executable)
        self.assertTrue(command[1].endswith("split_scenes.py"))
        self.assertEqual(command[2:], ["video.mkv", "--output", "/tmp/result", "--force"])
        self.assertTrue(run.call_args.kwargs["check"])

    def test_default_interactive_flow_prompts_for_mkv_before_tts(self):
        fake_root = Path("/tmp/NarratoAI-local")
        fake_python = fake_root / ".venv/bin/python"
        with (
            mock.patch.object(MODULE, "resolve_narrato_root", return_value=fake_root),
            mock.patch.object(MODULE, "choose_python", return_value=fake_python),
            mock.patch.object(MODULE.sys.stdin, "isatty", return_value=True),
            mock.patch("builtins.input", return_value="'/tmp/video.mkv'"),
            mock.patch.object(MODULE, "run_scene_split") as split,
            mock.patch.object(MODULE.os, "execv") as execv,
        ):
            result = MODULE.main(["--input", "稿.txt"])
        self.assertEqual(result, 0)
        split.assert_called_once_with("/tmp/video.mkv", None, False)
        execv.assert_called_once()


if __name__ == "__main__":
    unittest.main()
