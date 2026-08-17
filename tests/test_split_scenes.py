import csv
import importlib.util
from pathlib import Path
import tempfile
import unittest


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


class ShotFileTests(unittest.TestCase):
    def test_filename_with_square_brackets_is_literal(self):
        with tempfile.TemporaryDirectory() as directory:
            shots = Path(directory)
            expected = shots / "[Group] Episode-01-Shot-001.mkv"
            expected.touch()
            (shots / "G Episode-01-Shot-002.mkv").touch()
            self.assertEqual(MODULE.shot_files(shots, "[Group] Episode-01"), [expected])


if __name__ == "__main__":
    unittest.main()
