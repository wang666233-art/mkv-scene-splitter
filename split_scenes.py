#!/usr/bin/env python3
"""Detect shots in an MKV, split without re-encoding, and extract zh-Hans subtitles."""

from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any, Sequence


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
    for path in (
        output_dir / "scenes.csv",
        output_dir / "subtitles" / f"{stem}.zh-Hans{subtitle_suffix}",
    ):
        if path.exists():
            path.unlink()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="自动检测 MKV 镜头，无重编码拆分，并导出简体中文字幕。"
    )
    parser.add_argument("input", nargs="?", help="输入 MKV 文件路径；省略时交互输入")
    parser.add_argument("-o", "--output", help="输出目录（默认：源文件旁的 <文件名>_scenes）")
    parser.add_argument("--threshold", type=float, default=3.0, help="自适应检测阈值（默认：3.0）")
    parser.add_argument(
        "--min-content-val", type=float, default=15.0, help="最小内容变化值（默认：15.0）"
    )
    parser.add_argument(
        "--min-scene-len", default="0.6s", help="最短镜头长度（默认：0.6s）"
    )
    parser.add_argument("--subtitle-track", type=int, help="手动指定 mkvextract 字幕轨道 ID")
    parser.add_argument("--scenedetect", help="scenedetect 可执行文件路径")
    parser.add_argument("--mkvmerge", help="mkvmerge 可执行文件路径")
    parser.add_argument("--mkvextract", help="mkvextract 可执行文件路径")
    parser.add_argument("--force", action="store_true", help="覆盖该输出目录中本脚本生成的文件")
    parser.add_argument("--dry-run", action="store_true", help="只检查依赖、轨道与输出路径")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    raw_input = args.input
    if not raw_input:
        raw_input = input("请输入 MKV 文件路径：").strip()
    source = resolve_input_file(raw_input)
    if source.suffix.lower() != ".mkv":
        raise RuntimeError(f"当前脚本只接受 MKV 文件：{source}")

    scenedetect = executable("scenedetect", args.scenedetect)
    mkvmerge = executable("mkvmerge", args.mkvmerge)
    mkvextract = executable("mkvextract", args.mkvextract)
    metadata = inspect_tracks(mkvmerge, source)
    subtitle, subtitle_suffix = choose_subtitle_track(
        metadata.get("tracks", []), args.subtitle_track
    )

    output_dir = (
        Path(args.output).expanduser().resolve()
        if args.output
        else source.with_name(f"{source.stem}_scenes")
    )
    shots_dir = output_dir / "shots"
    subtitles_dir = output_dir / "subtitles"
    csv_path = output_dir / "scenes.csv"
    subtitle_path = subtitles_dir / f"{source.stem}.zh-Hans{subtitle_suffix}"
    shot_base = shots_dir / f"{source.stem}-Shot.mkv"

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

    run(
        [
            scenedetect,
            "-i",
            str(source),
            "-o",
            str(output_dir),
            "-b",
            "opencv",
            "-m",
            args.min_scene_len,
            "detect-adaptive",
            "--threshold",
            str(args.threshold),
            "--min-content-val",
            str(args.min_content_val),
            "list-scenes",
            "--filename",
            csv_path.name,
            "--quiet",
        ]
    )
    cut_times = read_cut_times(csv_path)
    print(f"PySceneDetect 识别到 {len(cut_times) + 1} 个候选镜头。")

    split_command = [mkvmerge, "-o", str(shot_base)]
    if cut_times:
        split_command.extend(["--split", "timestamps:" + ",".join(cut_times)])
    split_command.append(str(source))
    run(split_command)
    run([mkvextract, "tracks", str(source), f"{subtitle['id']}:{subtitle_path}"])

    clips = shot_files(shots_dir, source.stem)
    print(f"完成：生成 {len(clips)} 个无重编码镜头。")
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
