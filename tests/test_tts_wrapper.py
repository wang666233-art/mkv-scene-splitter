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
        root, mkv, output, info, force, skip, split_only, forwarded = (
            MODULE.extract_wrapper_options(
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
        )
        self.assertEqual(root, "/opt/narrato")
        self.assertEqual(mkv, "video.mkv")
        self.assertEqual(output, "/tmp/shots")
        self.assertTrue(info)
        self.assertTrue(force)
        self.assertFalse(skip)
        self.assertFalse(split_only)
        self.assertEqual(forwarded, ["--input", "稿.txt"])

    def test_skip_scene_split_is_consumed(self):
        _root, _mkv, _output, _info, _force, skip, split_only, forwarded = (
            MODULE.extract_wrapper_options(["--skip-scene-split", "--text", "你好"])
        )
        self.assertTrue(skip)
        self.assertFalse(split_only)
        self.assertEqual(forwarded, ["--text", "你好"])

    def test_split_only_is_consumed(self):
        _root, _mkv, _output, _info, _force, skip, split_only, forwarded = (
            MODULE.extract_wrapper_options(["--split-only", "--text", "你好"])
        )
        self.assertTrue(split_only)
        self.assertFalse(skip)
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

    def test_interactive_mode_prompts_for_mkv_before_tts(self):
        with (
            mock.patch.object(MODULE.sys.stdin, "isatty", return_value=True),
            mock.patch.object(MODULE, "choose_interactive_mode", return_value=MODULE.MODE_FULL),
            mock.patch("builtins.input", return_value="'/tmp/video.mkv'"),
            mock.patch.object(MODULE, "run_scene_split") as split,
            mock.patch.object(MODULE, "resolve_narration_file", return_value=Path("/tmp/稿.txt")),
            mock.patch.object(MODULE.tts_service, "read_narration", return_value="解说词内容"),
            mock.patch.object(
                MODULE, "execute_tts_workflow", return_value=("/tmp/draft", "draft_01")
            ) as tts_flow,
        ):
            result = MODULE.main(["--input", "稿.txt", "--draft-dir", "/tmp/drafts"])
        self.assertEqual(result, 0)
        split.assert_called_once_with(
            "/tmp/video.mkv", None, False, follow_with_tts=True
        )
        tts_flow.assert_called_once()

    def test_interactive_mode_split_only_returns_before_tts(self):
        fake_root = Path("/tmp/NarratoAI-local")
        fake_python = fake_root / ".venv/bin/python"
        with (
            mock.patch.object(MODULE, "resolve_narrato_root", return_value=fake_root),
            mock.patch.object(MODULE, "choose_python", return_value=fake_python),
            mock.patch.object(MODULE.sys.stdin, "isatty", return_value=True),
            mock.patch.object(
                MODULE, "choose_interactive_mode", return_value=MODULE.MODE_SPLIT_ONLY
            ),
            mock.patch("builtins.input", return_value="/tmp/video.mkv"),
            mock.patch.object(MODULE, "run_scene_split") as split,
            mock.patch.object(MODULE.os, "execv") as execv,
        ):
            result = MODULE.main([])
        self.assertEqual(result, 0)
        split.assert_called_once_with(
            "/tmp/video.mkv", None, False, follow_with_tts=False
        )
        execv.assert_not_called()

    def test_noninteractive_split_only_with_mkv(self):
        fake_root = Path("/tmp/NarratoAI-local")
        fake_python = fake_root / ".venv/bin/python"
        with (
            mock.patch.object(MODULE, "resolve_narrato_root", return_value=fake_root),
            mock.patch.object(MODULE, "choose_python", return_value=fake_python),
            mock.patch.object(MODULE.sys.stdin, "isatty", return_value=False),
            mock.patch.object(MODULE, "choose_interactive_mode") as choose,
            mock.patch.object(MODULE, "run_scene_split") as split,
            mock.patch.object(MODULE.os, "execv") as execv,
        ):
            result = MODULE.main(["--split-only", "--mkv", "/tmp/video.mkv"])
        self.assertEqual(result, 0)
        choose.assert_not_called()
        split.assert_called_once_with(
            "/tmp/video.mkv", None, False, follow_with_tts=False
        )
        execv.assert_not_called()

    def test_choose_interactive_mode_defaults_to_full(self):
        with mock.patch("builtins.input", return_value=""):
            self.assertEqual(MODULE.choose_interactive_mode(), MODULE.MODE_FULL)

    def test_choose_interactive_mode_retries_on_invalid(self):
        with (
            mock.patch("builtins.input", side_effect=["x", "2"]),
            mock.patch("builtins.print"),
        ):
            self.assertEqual(MODULE.choose_interactive_mode(), MODULE.MODE_TTS_ONLY)


class NarrationResolutionTests(unittest.TestCase):
    def test_auto_detects_same_stem_txt(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            dir_path = Path(temp_dir)
            mkv_file = dir_path / "恶女不才第八集.mkv"
            mkv_file.touch()
            txt_file = dir_path / "恶女不才第八集.txt"
            txt_file.write_text("解说词内容", encoding="utf-8")

            resolved = MODULE.resolve_narration_file(
                raw_value=None,
                mkv_source=mkv_file,
                interactive=False,
            )
            self.assertEqual(resolved, txt_file.resolve())

    def test_resolves_filename_without_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            dir_path = Path(temp_dir)
            txt_file = dir_path / "向日葵马戏团第九集.txt"
            txt_file.write_text("解说词内容", encoding="utf-8")

            resolved = MODULE.resolve_narration_file(
                raw_value="向日葵马戏团第九集",
                narration_dir=str(dir_path),
                interactive=False,
            )
            self.assertEqual(resolved, txt_file.resolve())

    def test_prefers_config_input(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            dir_path = Path(temp_dir)
            cfg_file = dir_path / "fixed.txt"
            cfg_file.write_text("固定解说词", encoding="utf-8")

            resolved = MODULE.resolve_narration_file(
                raw_value=None,
                config_input=str(cfg_file),
                interactive=False,
            )
            self.assertEqual(resolved, cfg_file.resolve())


if __name__ == "__main__":
    unittest.main()
