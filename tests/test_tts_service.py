from pathlib import Path
import tempfile
import unittest

from tts_service import (
    align_subtitle_lines,
    build_azure_ssml,
    format_srt_time,
    split_narration,
    split_subtitle_lines,
    write_srt,
)


class TtsServiceTests(unittest.TestCase):
    def test_split_narration(self):
        text = "第一句。第二句！第三句？\n\n第四段。"
        chunks = split_narration(text, max_chars=100)
        self.assertTrue(len(chunks) >= 1)
        self.assertIn("第一句", "".join(chunks))

    def test_split_subtitle_lines(self):
        text = "这是一个很长的测试句子，它应该在逗号或者句号处被切开，以保证字幕的行数不会超出限制。"
        lines = split_subtitle_lines(text, max_chars=15)
        self.assertTrue(len(lines) > 1)
        for line in lines:
            self.assertTrue(len(line) <= 20)

    def test_build_azure_ssml(self):
        ssml = build_azure_ssml(
            text="测试语音合成",
            voice_name="zh-CN-XiaoyiNeural",
            rate=1.0,
            pitch_percent=0,
            volume_percent=80,
            style="cheerful",
            role="YoungAdultFemale",
            style_degree=1.2,
        )
        self.assertIn("<mstts:express-as", ssml)
        self.assertIn('style="cheerful"', ssml)
        self.assertIn('role="YoungAdultFemale"', ssml)
        self.assertIn("zh-CN-XiaoyiNeural", ssml)
        self.assertIn("测试语音合成", ssml)

    def test_format_srt_time(self):
        self.assertEqual(format_srt_time(0.0), "00:00:00,000")
        self.assertEqual(format_srt_time(61.5), "00:01:01,500")
        self.assertEqual(format_srt_time(3661.123), "01:01:01,123")

    def test_write_srt(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            srt_path = Path(temp_dir) / "sub.srt"
            entries = [(0.0, 2.0, "第一行"), (2.1, 4.0, "第二行")]
            write_srt(srt_path, entries)
            content = srt_path.read_text(encoding="utf-8-sig")
            self.assertIn("00:00:00,000 --> 00:00:02,000", content)
            self.assertIn("第一行", content)
            self.assertIn("第二行", content)

    def test_align_subtitle_lines(self):
        lines = ["你好", "世界"]
        boundary_events = [
            (0.0, 0.4, "你"),
            (0.4, 0.8, "好"),
            (1.0, 1.4, "世"),
            (1.4, 1.8, "界"),
        ]
        entries = align_subtitle_lines(lines, boundary_events, audio_duration=2.0, timeline_offset=0.0)
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0][2], "你好")
        self.assertAlmostEqual(entries[0][0], 0.0)
        self.assertAlmostEqual(entries[0][1], 0.8)
        self.assertEqual(entries[1][2], "世界")
        self.assertAlmostEqual(entries[1][0], 1.0)
        self.assertAlmostEqual(entries[1][1], 1.8)


if __name__ == "__main__":
    unittest.main()
