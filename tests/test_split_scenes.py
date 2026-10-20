import csv
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("split_scenes", ROOT / "split_scenes.py")
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def subtitle(track_id, name, language="chi", ietf=None, codec="SubRip/SRT"):
    properties = {"track_name": name, "language": language}
    if ietf:
        properties["language_ietf"] = ietf
    return {"id": track_id, "type": "subtitles", "codec": codec, "properties": properties}


class SubtitleSelectionTests(unittest.TestCase):
    def test_prefers_simplified_over_traditional(self):
        selected, suffix = MODULE.choose_subtitle_track(
            [subtitle(2, "简体"), subtitle(3, "繁体")]
        )
        self.assertEqual(selected["id"], 2)
        self.assertEqual(suffix, ".srt")

    def test_uses_ietf_language(self):
        selected, _ = MODULE.choose_subtitle_track(
            [subtitle(4, "", ietf="zh-Hans"), subtitle(5, "", ietf="zh-Hant")]
        )
        self.assertEqual(selected["id"], 4)

    def test_rejects_ambiguous_tracks(self):
        with self.assertRaises(RuntimeError):
            MODULE.choose_subtitle_track([subtitle(2, ""), subtitle(3, "")])

    def test_explicit_track_wins(self):
        selected, _ = MODULE.choose_subtitle_track(
            [subtitle(2, "简体"), subtitle(3, "繁体")], requested_id=3
        )
        self.assertEqual(selected["id"], 3)


class CsvTests(unittest.TestCase):
    def test_reads_cut_list(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scenes.csv"
            with path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(["Timecode List:", "00:00:01.000", "00:00:02.500"])
            self.assertEqual(
                MODULE.read_cut_times(path), ["00:00:01.000", "00:00:02.500"]
            )

    def test_parses_scene_timecode(self):
        self.assertAlmostEqual(MODULE.parse_timecode("01:02:03.500"), 3723.5)


class ReencodeTests(unittest.TestCase):
    def test_encodes_once_and_forces_all_scene_boundaries(self):
        with (
            mock.patch.object(MODULE, "run") as runner,
            mock.patch.object(MODULE, "shot_files", return_value=[]),
        ):
            MODULE.reencode_scenes(
                "ffmpeg",
                Path("/tmp/source.mkv"),
                Path("/tmp/shots"),
                ["00:00:01.000", "00:00:02.500"],
            )

        runner.assert_called_once()
        command = runner.call_args.args[0]
        self.assertIn("-force_key_frames", command)
        self.assertNotIn("-forced-idr", command)
        self.assertEqual(command[command.index("-segment_times") + 1], "1.000,2.500")
        self.assertNotIn("0:s?", command)
        self.assertNotIn("-c:s", command)
        self.assertEqual(command[-1], "/tmp/shots/source-Shot-%03d.mkv")

    def test_nvenc_requests_forced_idr(self):
        with (
            mock.patch.object(MODULE, "run") as runner,
            mock.patch.object(MODULE, "shot_files", return_value=[]),
        ):
            MODULE.reencode_scenes(
                "ffmpeg", Path("/tmp/source.mkv"), Path("/tmp/shots"),
                ["00:00:01.000"], "h264_nvenc",
            )
        command = runner.call_args.args[0]
        self.assertEqual(command[command.index("-forced-idr") + 1], "1")


class VideoEncoderChoiceTests(unittest.TestCase):
    def test_auto_uses_cpu_when_nvenc_is_listed_but_unusable(self):
        with (
            mock.patch.object(MODULE, "ffmpeg_encoders", return_value={"h264_nvenc", "libx264"}),
            mock.patch.object(MODULE, "nvenc_usable", return_value=False),
        ):
            self.assertEqual(MODULE.choose_video_encoder("ffmpeg", "auto"), "libx264")

    def test_auto_keeps_nvenc_when_a_device_is_really_available(self):
        with (
            mock.patch.object(MODULE, "ffmpeg_encoders", return_value={"h264_nvenc", "libx264"}),
            mock.patch.object(MODULE, "nvenc_usable", return_value=True),
        ):
            self.assertEqual(MODULE.choose_video_encoder("ffmpeg", "auto"), "h264_nvenc")

    def test_explicit_nvenc_request_is_not_downgraded(self):
        with (
            mock.patch.object(MODULE, "ffmpeg_encoders", return_value={"h264_nvenc", "libx264"}),
            mock.patch.object(MODULE, "nvenc_usable", return_value=False),
        ):
            self.assertEqual(MODULE.choose_video_encoder("ffmpeg", "h264_nvenc"), "h264_nvenc")

    def test_missing_encoder_still_raises(self):
        with mock.patch.object(MODULE, "ffmpeg_encoders", return_value={"libx264"}):
            with self.assertRaises(RuntimeError):
                MODULE.choose_video_encoder("ffmpeg", "h264_nvenc")


class FfmpegStdinTests(unittest.TestCase):
    def test_segmenting_never_waits_on_stdin(self):
        with (
            mock.patch.object(MODULE, "run") as runner,
            mock.patch.object(MODULE, "shot_files", return_value=[]),
        ):
            MODULE.reencode_scenes(
                "ffmpeg", Path("/tmp/source.mkv"), Path("/tmp/shots"), ["00:00:01.000"]
            )
        self.assertIn("-nostdin", runner.call_args.args[0])


class SubtitleConversionTests(unittest.TestCase):
    def test_removes_ass_and_html_markup(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "captions.srt"
            path.write_text(
                "1\n00:00:01,000 --> 00:00:02,000\n{}<b>这个世上不只有诅咒</b>\n",
                encoding="utf-8",
            )
            MODULE.clean_srt_markup(path)
            self.assertEqual(path.read_text(encoding="utf-8").splitlines()[-1], "这个世上不只有诅咒")


class ShotFileTests(unittest.TestCase):
    def test_filename_with_square_brackets_is_literal(self):
        with tempfile.TemporaryDirectory() as directory:
            shots = Path(directory)
            expected = shots / "[Group] Episode-01-Shot-001.mkv"
            expected.touch()
            (shots / "G Episode-01-Shot-002.mkv").touch()
            self.assertEqual(MODULE.shot_files(shots, "[Group] Episode-01"), [expected])


class InputResolutionTests(unittest.TestCase):
    def test_resolves_direct_path(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "video.mkv"
            source.touch()
            self.assertEqual(MODULE.resolve_input_file(str(source)), source.resolve())

    def test_finds_filename_recursively(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "videos" / "nested" / "episode.mkv"
            source.parent.mkdir(parents=True)
            source.touch()
            with mock.patch.object(
                MODULE, "find_files_by_name", return_value=[source.resolve()]
            ) as finder:
                resolved = MODULE.resolve_input_file("episode.mkv", interactive=False)
            self.assertEqual(resolved, source.resolve())
            finder.assert_called_once_with("episode.mkv")

    def test_noninteractive_duplicate_requires_full_path(self):
        matches = [Path("/a/video.mkv"), Path("/b/video.mkv")]
        with mock.patch.object(MODULE, "find_files_by_name", return_value=matches):
            with self.assertRaises(RuntimeError):
                MODULE.resolve_input_file("video.mkv", interactive=False)


if __name__ == "__main__":
    unittest.main()
