import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from config import clean_string_value, find_config_file, load_config, pick_setting


class ConfigTests(unittest.TestCase):
    def test_clean_string_value(self):
        self.assertEqual(clean_string_value("  hello  "), "hello")
        self.assertEqual(clean_string_value("'path/with/single'"), "path/with/single")
        self.assertEqual(clean_string_value('"path/with/double"'), "path/with/double")
        self.assertEqual(clean_string_value(" ' nested ' "), "nested")
        self.assertEqual(clean_string_value(None), "")

    def test_find_and_load_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.toml"
            config_path.write_text("""
[scene_split]
threshold = 2.5
min_scene_len = "1.0s"

[jianying]
draft_dir = "/tmp/drafts"
width = 1080
height = 1920

[azure]
voice = "zh-CN-YunxiNeural"
""", encoding="utf-8")

            found = find_config_file(config_path)
            self.assertEqual(found, config_path.resolve())

            loaded = load_config(config_path)
            self.assertEqual(loaded["scene_split"]["threshold"], 2.5)
            self.assertEqual(loaded["jianying"]["draft_dir"], "/tmp/drafts")
            self.assertEqual(loaded["azure"]["voice"], "zh-CN-YunxiNeural")

    def test_pick_setting_precedence(self):
        # CLI wins over env, config, default
        with mock.patch.dict(os.environ, {"TEST_KEY": "from_env"}):
            self.assertEqual(
                pick_setting("from_cli", "TEST_KEY", "from_cfg", "from_default"),
                "from_cli",
            )
            # Env wins over config and default when CLI is None
            self.assertEqual(
                pick_setting(None, "TEST_KEY", "from_cfg", "from_default"),
                "from_env",
            )

        # Config wins over default when CLI and env are absent
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(
                pick_setting(None, "TEST_KEY", "from_cfg", "from_default"),
                "from_cfg",
            )
            # Default is used when all are absent
            self.assertEqual(
                pick_setting(None, "TEST_KEY", None, "from_default"),
                "from_default",
            )

    def test_pick_setting_type_casting(self):
        self.assertEqual(pick_setting("123", cast_type=int), 123)
        self.assertEqual(pick_setting("3.14", cast_type=float), 3.14)
        self.assertTrue(pick_setting("true", cast_type=bool))
        self.assertTrue(pick_setting("1", cast_type=bool))
        self.assertFalse(pick_setting("false", cast_type=bool))
        self.assertEqual(pick_setting("not_int", default=42, cast_type=int), 42)


if __name__ == "__main__":
    unittest.main()
