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
        root, info, forwarded = MODULE.extract_wrapper_options(
            ["--input", "稿.txt", "--narrato-root=/opt/narrato", "--wrapper-info"]
        )
        self.assertEqual(root, "/opt/narrato")
        self.assertTrue(info)
        self.assertEqual(forwarded, ["--input", "稿.txt"])


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


if __name__ == "__main__":
    unittest.main()
