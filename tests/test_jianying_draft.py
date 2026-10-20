import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from jianying_draft import (
    VideoClipParams,
    parse_srt_like_time,
    _format_draft_uuid,
    _safe_file_name,
    _normalize_hex_color,
    write_audio_only_jianying_draft,
)


class JianyingDraftTests(unittest.TestCase):
    def test_parse_srt_like_time(self):
        self.assertAlmostEqual(parse_srt_like_time("00:01:23,456"), 83.456)
        self.assertAlmostEqual(parse_srt_like_time("01:00:00.000"), 3600.0)
        with self.assertRaises(ValueError):
            parse_srt_like_time("invalid_time")

    def test_format_draft_uuid(self):
        compact = "0123456789abcdef0123456789abcdef"
        formatted = _format_draft_uuid(compact)
        self.assertEqual(formatted, "01234567-89AB-CDEF-0123-456789ABCDEF")

    def test_safe_file_name(self):
        self.assertEqual(_safe_file_name("path/to/bad:name?.mp3", "fallback"), "bad_name_.mp3")

    def test_normalize_hex_color(self):
        self.assertEqual(_normalize_hex_color("fff"), "#FFFFFF")
        self.assertEqual(_normalize_hex_color("#123456"), "#123456")
        self.assertEqual(_normalize_hex_color("invalid", "#000000"), "#000000")

    def test_write_audio_only_draft_basic(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            audio_file = root / "test_audio.wav"
            audio_file.write_bytes(b"RIFF" + b"\x00" * 100)

            srt_file = root / "test.srt"
            srt_file.write_text(
                "1\n00:00:00,000 --> 00:00:02,000\n第一句解说\n\n2\n00:00:02,100 --> 00:00:04,500\n第二句解说\n\n",
                encoding="utf-8-sig",
            )

            with mock.patch("jianying_draft._get_media_duration_ffprobe", return_value=5.0):
                # 传入带单引号的草稿目录，验证自动去除外层引号
                quoted_draft_dir = f"'{root / 'JianyingPro Drafts'}'"
                draft_path, draft_name = write_audio_only_jianying_draft(
                    jianying_draft_path=quoted_draft_dir,
                    draft_name="TestDraft",
                    audio_files=[str(audio_file)],
                    subtitle_path=str(srt_file),
                )

            self.assertTrue(os.path.isdir(draft_path))
            self.assertEqual(draft_name, "TestDraft")

            draft_info_file = Path(draft_path) / "draft_info.json"
            self.assertTrue(draft_info_file.is_file())

            info = json.loads(draft_info_file.read_text(encoding="utf-8"))
            self.assertEqual(info["name"], "TestDraft")
            # 应该有音频轨和字幕轨
            track_types = [t["type"] for t in info.get("tracks", [])]
            self.assertIn("audio", track_types)
            self.assertIn("text", track_types)

            # 验证 root_meta_info.json
            root_meta = Path(temp_dir) / "JianyingPro Drafts" / "root_meta_info.json"
            self.assertTrue(root_meta.is_file())
            root_meta_data = json.loads(root_meta.read_text(encoding="utf-8"))
            self.assertTrue(len(root_meta_data.get("all_draft_store", [])) > 0)


if __name__ == "__main__":
    unittest.main()
