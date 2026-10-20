#!/usr/bin/env python3
"""Detect shots in an MKV, split without re-encoding, and extract zh-Hans subtitles."""

from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any, Sequence

_REPO_ROOT = Path(__file__).resolve().parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from config import clean_string_value, find_config_file, load_config, pick_setting


SEARCH_PRUNE_DIRECTORIES = {
    ".cache",
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "__pycache__",
    "node_modules",
}


def executable(name: str, override: str | None = None) -> str:
    """Find a required executable, including the user's common PySceneDetect venv."""
    candidates = [override, shutil.which(name)]
    if name == "scenedetect":
        candidates.append(str(Path.home() / ".venvs/pyscenedetect/bin/scenedetect"))
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return str(Path(candidate).resolve())
    raise RuntimeError(
        f"找不到 {name}。请安装并加入 PATH，或使用 --{name} 指定可执行文件。"
    )


def ffmpeg_encoders(ffmpeg: str) -> set[str]:
    result = subprocess.run(
        [ffmpeg, "-hide_banner", "-encoders"],
        check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    return {
        line.split()[1]
        for line in result.stdout.splitlines()
        if len(line.split()) >= 2 and line.lstrip().startswith("V")
    }


def nvenc_usable(ffmpeg: str) -> bool:
    """Check that NVENC can really open an encoder, not just that it is listed.

    `ffmpeg -encoders` advertises h264_nvenc whenever it is compiled in, even on
    machines without a usable NVIDIA device. Encoding a tiny synthetic frame is
    the only reliable way to tell the difference.
    """
    command = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
        "-f", "lavfi", "-i", "color=c=black:s=256x144:r=25:d=0.04",
        "-frames:v", "1", "-c:v", "h264_nvenc", "-f", "null", "-",
    ]
    try:
        result = subprocess.run(
            command, text=True,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
    except OSError:
        return False
    return result.returncode == 0


def choose_video_encoder(ffmpeg: str, requested: str) -> str:
    available = ffmpeg_encoders(ffmpeg)
    if requested == "auto":
        if "h264_nvenc" not in available:
            selected = "libx264"
        elif nvenc_usable(ffmpeg):
            selected = "h264_nvenc"
        else:
            print(
                "FFmpeg 带有 h264_nvenc，但本机没有可用的 NVENC/CUDA 设备，改用 CPU libx264。",
                flush=True,
            )
            selected = "libx264"
    else:
        selected = requested
    if selected not in available:
        raise RuntimeError(
            f"FFmpeg 不支持视频编码器 {selected}。可用编码器中未找到该名称；"
            "请安装带 NVENC 的 FFmpeg，或将 video_encoder 设置为 libx264。"
        )
    return selected


def parse_timecode(value: str) -> float:
    """Convert a PySceneDetect timecode (HH:MM:SS[.mmm]) to seconds."""
    text = value.strip()
    parts = text.split(":")
    if len(parts) != 3:
        raise RuntimeError(f"无法解析场景时间码：{value}")
    try:
        hours, minutes = int(parts[0]), int(parts[1])
        seconds = float(parts[2])
    except ValueError as error:
        raise RuntimeError(f"无法解析场景时间码：{value}") from error
    if hours < 0 or not 0 <= minutes < 60 or not 0 <= seconds < 60:
        raise RuntimeError(f"场景时间码超出范围：{value}")
    return hours * 3600 + minutes * 60 + seconds


def reencode_scenes(
    ffmpeg: str,
    source: Path,
    shots_dir: Path,
    cut_times: Sequence[str],
    video_encoder: str = "libx264",
) -> list[Path]:
    """Encode the source once and segment it at forced keyframes."""
    output_pattern = shots_dir / f"{source.stem}-Shot-%03d.mkv"
    command = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-nostdin",
        "-i", str(source),
        "-map", "0:v?", "-map", "0:a?",
        "-c:v", video_encoder,
        "-c:a", "copy", "-shortest",
    ]
    if video_encoder == "h264_nvenc":
        command[command.index("-c:a"):command.index("-c:a")] = [
            "-preset", "p5", "-rc", "vbr", "-cq", "19", "-b:v", "0",
            "-forced-idr", "1",
        ]
    else:
        command[command.index("-c:a"):command.index("-c:a")] = [
            "-preset", "medium", "-crf", "18",
        ]
    if cut_times:
        boundary_seconds = ",".join(f"{parse_timecode(value):.3f}" for value in cut_times)
        command.extend([
            "-force_key_frames", boundary_seconds,
            "-f", "segment", "-segment_times", boundary_seconds,
            "-segment_time_delta", "0.05", "-reset_timestamps", "1",
            "-segment_start_number", "1",
        ])
    else:
        command.extend(["-f", "segment", "-segment_time", "86400", "-segment_start_number", "1"])
    command.append(str(output_pattern))
    try:
        run(command)
    except subprocess.CalledProcessError:
        if video_encoder != "h264_nvenc":
            raise
        print("NVENC/CUDA 初始化失败，清理未完成的片段并回退到 CPU libx264。", flush=True)
        for partial in shot_files(shots_dir, source.stem):
            partial.unlink(missing_ok=True)
        return reencode_scenes(ffmpeg, source, shots_dir, cut_times, "libx264")
    return shot_files(shots_dir, source.stem)


def find_files_by_name(filename: str, roots: Sequence[Path] | None = None) -> list[Path]:
    """Find an exact filename below local user directories without following symlinks."""
    search_roots = list(roots or (Path.cwd(), Path.home()))
    matches: set[Path] = set()
    visited_roots: list[Path] = []
    for raw_root in search_roots:
        root = raw_root.expanduser().resolve()
        if not root.is_dir() or any(root == seen or seen in root.parents for seen in visited_roots):
            continue
        # If a broader root is encountered after a nested one, searching the
        # broader root is still necessary, but avoid reporting duplicates.
        visited_roots.append(root)
        for current, directories, files in os.walk(root, followlinks=False):
            directories[:] = [
                name for name in directories if name not in SEARCH_PRUNE_DIRECTORIES
            ]
            if filename in files:
                matches.add((Path(current) / filename).resolve())
    return sorted(matches, key=lambda path: str(path))


def resolve_input_file(raw_value: str, *, interactive: bool | None = None) -> Path:
    """Resolve a path, searching the local home tree when only a filename is given."""
    normalized = raw_value.strip().strip('"').strip("'")
    if not normalized:
        raise RuntimeError("MKV 文件路径不能为空")

    direct = Path(normalized).expanduser()
    if direct.is_file():
        return direct.resolve()
    if direct.name != normalized:
        raise RuntimeError(f"输入文件不存在：{direct.resolve()}")

    print(f"未在当前目录找到 {normalized}，正在本机用户目录中查找…", flush=True)
    matches = find_files_by_name(normalized)
    if not matches:
        raise RuntimeError(f"未找到文件：{normalized}")
    if len(matches) == 1:
        print(f"已找到：{matches[0]}", flush=True)
        return matches[0]

    use_interactive = sys.stdin.isatty() if interactive is None else interactive
    if not use_interactive:
        candidates = "\n".join(f"  - {path}" for path in matches)
        raise RuntimeError(f"找到多个同名文件，请传入完整路径：\n{candidates}")
    print("找到多个同名文件：")
    for index, path in enumerate(matches, start=1):
        print(f"  {index}. {path}")
    while True:
        selection = input(f"请选择文件 [1-{len(matches)}]：").strip()
        try:
            selected_index = int(selection)
        except ValueError:
            selected_index = 0
        if 1 <= selected_index <= len(matches):
            return matches[selected_index - 1]
        print("输入无效，请输入候选项编号。")


def run(command: Sequence[str], *, capture: bool = False) -> subprocess.CompletedProcess[str]:
    print("+", " ".join(shlex_quote(part) for part in command), flush=True)
    return subprocess.run(
        command,
        check=True,
        text=True,
        stdout=subprocess.PIPE if capture else None,
    )


def shlex_quote(value: str) -> str:
    """Small local shell-style formatter used only for readable logging."""
    import shlex

    return shlex.quote(value)


def inspect_tracks(mkvmerge: str, source: Path) -> dict[str, Any]:
    result = run([mkvmerge, "-J", str(source)], capture=True)
    return json.loads(result.stdout)


def subtitle_extension(codec: str) -> str | None:
    normalized = codec.lower()
    if "subrip" in normalized or normalized == "s_text/utf8":
        return ".srt"
    if "substation" in normalized or normalized in {"s_text/ass", "s_text/ssa"}:
        return ".ass"
    if "webvtt" in normalized:
        return ".vtt"
    return None


def simplified_score(track: dict[str, Any]) -> int:
    props = track.get("properties", {})
    name = str(props.get("track_name") or "").lower().replace("_", "-")
    language = str(props.get("language") or "").lower()
    ietf = str(props.get("language_ietf") or "").lower().replace("_", "-")
    text = f"{name} {language} {ietf}"

    if any(token in text for token in ("繁体", "繁中", "traditional", "zh-hant", "zh-tw", "cht")):
        return -100

    score = 0
    if any(token in text for token in ("简体", "簡體", "简中", "簡中", "simplified", "zh-hans", "zh-cn", "chs")):
        score += 100
    if language in {"chi", "zho", "zh", "chs"} or ietf.startswith("zh"):
        score += 20
    return score


def choose_subtitle_track(
    tracks: Sequence[dict[str, Any]], requested_id: int | None = None
) -> tuple[dict[str, Any], str]:
    text_tracks = [
        track
        for track in tracks
        if track.get("type") == "subtitles" and subtitle_extension(str(track.get("codec", "")))
    ]
    if requested_id is not None:
        matches = [track for track in text_tracks if track.get("id") == requested_id]
        if not matches:
            raise RuntimeError(f"轨道 {requested_id} 不是受支持的文本字幕轨。")
        selected = matches[0]
        return selected, subtitle_extension(str(selected["codec"])) or ".srt"

    if not text_tracks:
        raise RuntimeError("源文件中没有可导出的 SRT/ASS/WebVTT 文本字幕轨。")

    scored = sorted(
        ((simplified_score(track), track) for track in text_tracks),
        key=lambda item: item[0],
        reverse=True,
    )
    best_score = scored[0][0]
    winners = [track for score, track in scored if score == best_score]
    if best_score <= 0 or len(winners) != 1:
        details = ", ".join(
            f"ID {track['id']} ({track.get('properties', {}).get('track_name') or '无名称'}, "
            f"{track.get('properties', {}).get('language_ietf') or track.get('properties', {}).get('language') or 'und'})"
            for track in text_tracks
        )
        raise RuntimeError(
            "无法唯一判断简体中文字幕轨，请使用 --subtitle-track 指定轨道号。候选：" + details
        )
    selected = winners[0]
    return selected, subtitle_extension(str(selected["codec"])) or ".srt"


def read_cut_times(csv_path: Path) -> list[str]:
    with csv_path.open(encoding="utf-8-sig", newline="") as handle:
        first_row = next(csv.reader(handle), [])
    if not first_row or first_row[0].strip() != "Timecode List:":
        raise RuntimeError(f"无法从 {csv_path} 读取 PySceneDetect 时间码列表。")
    return [value.strip() for value in first_row[1:] if value.strip()]


def shot_files(shots_dir: Path, stem: str) -> list[Path]:
    """Return generated shot files without treating characters in stem as globs."""
    if not shots_dir.is_dir():
        return []
    base_name = f"{stem}-Shot"
    return sorted(
        path
        for path in shots_dir.iterdir()
        if path.is_file()
        and path.suffix.lower() == ".mkv"
        and (path.stem == base_name or path.stem.startswith(base_name + "-"))
    )


def clean_generated(output_dir: Path, stem: str, subtitle_suffix: str) -> None:
    for path in shot_files(output_dir / "shots", stem):
        path.unlink()
    subtitle_paths = [
        output_dir / "subtitles" / f"{stem}.zh-Hans{suffix}"
        for suffix in (".srt", ".ass", ".ssa", ".vtt")
    ]
    for path in [output_dir / "scenes.csv", *subtitle_paths]:
        if path.exists():
            path.unlink()


def extract_subtitle(
    mkvextract: str,
    ffmpeg: str,
    source: Path,
    track_id: int,
    output: Path,
    source_suffix: str,
) -> None:
    """Extract subtitles and normalize formats unsupported by Jianying to UTF-8 SRT."""
    if source_suffix == ".srt":
        run([mkvextract, "tracks", str(source), f"{track_id}:{output}"])
        clean_srt_markup(output)
        return
    temporary = output.with_name(f".{output.stem}.source{source_suffix}")
    try:
        run([mkvextract, "tracks", str(source), f"{track_id}:{temporary}"])
        run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-nostdin", "-i", str(temporary), "-c:s", "srt", str(output)])
        clean_srt_markup(output)
    finally:
        temporary.unlink(missing_ok=True)


def clean_srt_markup(path: Path) -> None:
    """Remove ASS override blocks and HTML-style tags left by subtitle conversion."""
    text = path.read_text(encoding="utf-8-sig")
    text = re.sub(r"\{[^}]*\}", "", text)
    text = re.sub(r"</?(?:b|i|u|s|font)(?:\s[^>]*)?>", "", text, flags=re.IGNORECASE)
    path.write_text(text, encoding="utf-8", newline="")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="自动检测 MKV 镜头，重新编码切割，并导出简体中文字幕。"
    )
    parser.add_argument("input", nargs="?", help="输入 MKV 文件路径；省略时交互输入")
    parser.add_argument("-c", "--config", help="配置文件路径 (config.toml)")
    parser.add_argument("-o", "--output", help="输出目录（默认：源文件旁的 <文件名>_scenes）")
    parser.add_argument("--threshold", type=float, default=None, help="镜头检测阈值（content 默认：12.0，adaptive 默认：2.0）")
    parser.add_argument(
        "--detector", choices=("content", "adaptive"), default=None,
        help="镜头检测器（默认 content，直接比较相邻帧）",
    )
    parser.add_argument(
        "--min-content-val", type=float, default=None, help="最小内容变化值（默认：8.0）"
    )
    parser.add_argument(
        "--min-scene-len", default=None, help="最短镜头长度（默认：0.3s）"
    )
    parser.add_argument("--subtitle-track", type=int, default=None, help="手动指定 mkvextract 字幕轨道 ID")
    parser.add_argument("--scenedetect", default=None, help="scenedetect 可执行文件路径")
    parser.add_argument("--mkvmerge", default=None, help="mkvmerge 可执行文件路径")
    parser.add_argument("--mkvextract", default=None, help="mkvextract 可执行文件路径")
    parser.add_argument("--ffmpeg", default=None, help="ffmpeg 可执行文件路径")
    parser.add_argument(
        "--video-encoder", choices=("auto", "h264_nvenc", "libx264"), default=None,
        help="视频编码器（默认 auto：优先 NVENC，否则 CPU libx264）",
    )
    parser.add_argument("--force", action="store_true", help="覆盖该输出目录中本脚本生成的文件")
    parser.add_argument("--dry-run", action="store_true", help="只检查依赖、轨道与输出路径")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = load_config(args.config)
    config_path = find_config_file(args.config)
    print(f"配置文件：{config_path or '未找到（使用默认值）'}", flush=True)
    scene_cfg = config.get("scene_split", {})

    detector = pick_setting(args.detector, "SCENE_DETECTOR", scene_cfg.get("detector", "content"), "content", str)
    threshold_default = 12.0 if detector == "content" else 2.0
    threshold = pick_setting(args.threshold, "SCENE_THRESHOLD", scene_cfg.get("threshold", threshold_default), threshold_default, float)
    min_content_val = pick_setting(args.min_content_val, "SCENE_MIN_CONTENT_VAL", scene_cfg.get("min_content_val", 8.0), 8.0, float)
    min_scene_len = pick_setting(args.min_scene_len, "SCENE_MIN_SCENE_LEN", scene_cfg.get("min_scene_len", "0.3s"), "0.3s", str)
    output_arg = pick_setting(args.output, "SCENE_OUTPUT_DIR", scene_cfg.get("output_dir"), None)
    subtitle_track = pick_setting(args.subtitle_track, "SCENE_SUBTITLE_TRACK", scene_cfg.get("subtitle_track"), None, int)
    scenedetect_bin = pick_setting(args.scenedetect, "SCENEDETECT_BIN", scene_cfg.get("scenedetect"), None)
    mkvmerge_bin = pick_setting(args.mkvmerge, "MKVMERGE_BIN", scene_cfg.get("mkvmerge"), None)
    mkvextract_bin = pick_setting(args.mkvextract, "MKVEXTRACT_BIN", scene_cfg.get("mkvextract"), None)
    ffmpeg_bin = pick_setting(getattr(args, "ffmpeg", None), "FFMPEG_BIN", scene_cfg.get("ffmpeg"), None)
    video_encoder_setting = pick_setting(
        args.video_encoder, "SCENE_VIDEO_ENCODER", scene_cfg.get("video_encoder", "auto"), "auto", str
    )

    raw_input = args.input
    if not raw_input:
        raw_input = input("请输入 MKV 文件路径：").strip()
    source = resolve_input_file(raw_input)
    if source.suffix.lower() != ".mkv":
        raise RuntimeError(f"当前脚本只接受 MKV 文件：{source}")

    scenedetect = executable("scenedetect", scenedetect_bin)
    mkvmerge = executable("mkvmerge", mkvmerge_bin)
    mkvextract = executable("mkvextract", mkvextract_bin)
    ffmpeg = executable("ffmpeg", ffmpeg_bin)
    if ffmpeg_bin is None and not nvenc_usable(ffmpeg):
        for system_ffmpeg in (Path("/usr/bin/ffmpeg"), Path("/usr/local/bin/ffmpeg")):
            if system_ffmpeg.is_file() and str(system_ffmpeg) != ffmpeg:
                if nvenc_usable(str(system_ffmpeg)):
                    ffmpeg = str(system_ffmpeg)
                    print(f"检测到可用的 NVENC FFmpeg：{ffmpeg}")
                    break
    video_encoder = choose_video_encoder(ffmpeg, video_encoder_setting)
    print(f"视频编码器：{video_encoder}")
    print(
        f"镜头检测：{detector}，threshold={threshold}，min_scene_len={min_scene_len}"
        + (f"，min_content_val={min_content_val}" if detector == "adaptive" else ""),
        flush=True,
    )
    metadata = inspect_tracks(mkvmerge, source)
    subtitle, source_subtitle_suffix = choose_subtitle_track(
        metadata.get("tracks", []), subtitle_track
    )
    subtitle_suffix = ".srt"

    output_dir = (
        Path(output_arg).expanduser().resolve()
        if output_arg
        else source.with_name(f"{source.stem}_scenes")
    )
    shots_dir = output_dir / "shots"
    subtitles_dir = output_dir / "subtitles"
    csv_path = output_dir / "scenes.csv"
    subtitle_path = subtitles_dir / f"{source.stem}.zh-Hans{subtitle_suffix}"

    props = subtitle.get("properties", {})
    print(f"输入：{source}")
    print(f"输出：{output_dir}")
    print(
        "简体字幕轨："
        f"ID {subtitle['id']}，{subtitle.get('codec')}，"
        f"名称={props.get('track_name') or '无'}，"
        f"语言={props.get('language_ietf') or props.get('language') or 'und'}"
    )
    if args.dry_run:
        print("检查完成（dry-run，未生成文件）。")
        return 0

    generated_exist = csv_path.exists() or subtitle_path.exists() or bool(
        shot_files(shots_dir, source.stem)
    )
    if generated_exist and not args.force:
        raise RuntimeError(f"输出文件已存在：{output_dir}；如需覆盖请添加 --force。")

    shots_dir.mkdir(parents=True, exist_ok=True)
    subtitles_dir.mkdir(parents=True, exist_ok=True)
    if args.force:
        clean_generated(output_dir, source.stem, subtitle_suffix)

    detect_command = [
            scenedetect,
            "-i",
            str(source),
            "-o",
            str(output_dir),
            "-b",
            "opencv",
            "-m",
            min_scene_len,
            f"detect-{detector}",
            "--threshold",
            str(threshold),
        ]
    if detector == "adaptive":
        detect_command.extend(["--min-content-val", str(min_content_val), "--frame-window", "1"])
    else:
        detect_command.extend(["--filter-mode", "suppress"])
    detect_command.extend([
            "list-scenes",
            "--filename",
            csv_path.name,
            "--quiet",
        ])
    run(detect_command)
    cut_times = read_cut_times(csv_path)
    print(f"PySceneDetect 识别到 {len(cut_times) + 1} 个候选镜头。")

    clips = reencode_scenes(ffmpeg, source, shots_dir, cut_times, video_encoder)
    extract_subtitle(
        mkvextract, ffmpeg, source, subtitle["id"], subtitle_path, source_subtitle_suffix
    )
    print(f"完成：生成 {len(clips)} 个重新编码镜头。")
    print(f"镜头目录：{shots_dir}")
    print(f"简体字幕：{subtitle_path}")
    print(f"时间表：{csv_path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, subprocess.CalledProcessError, json.JSONDecodeError) as error:
        print(f"错误：{error}", file=sys.stderr)
        raise SystemExit(1)
