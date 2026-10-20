#!/usr/bin/env python3
"""Split an MKV, then run TTS-to-Jianying workflow (Standalone).

This script can detect and split scenes from an MKV file, synthesize narration
via Azure Speech or Fish Audio, align word boundaries, and export a ready-to-use
Jianying Pro draft package directly without relying on NarratoAI.
"""

from __future__ import annotations

import argparse
import getpass
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from config import clean_string_value, find_config_file, load_config, pick_setting
from jianying_draft import VideoClipParams, write_audio_only_jianying_draft
from split_scenes import find_files_by_name, resolve_input_file
import tts_service

TARGET_RELATIVE_PATH = Path("scripts/azure_tts_to_jianying.py")

MODE_FULL = "split-and-tts"      # 1. 先分割 MKV 镜头，再生成 TTS → 剪映草稿
MODE_TTS_ONLY = "tts-only"       # 2. 直接生成 TTS → 剪映草稿（跳过镜头分割）
MODE_SPLIT_ONLY = "split-only"   # 3. 仅分割 MKV 镜头，不生成 TTS


def normalize_interactive_path(value: str) -> str:
    """Strip quotes and whitespace from user path input."""
    return clean_string_value(value)


def choose_interactive_mode() -> str:
    """交互模式下询问本次要执行的工作模式。"""
    options = [
        (MODE_FULL, "先分割 MKV 镜头，再生成 TTS → 剪映草稿"),
        (MODE_TTS_ONLY, "直接生成 TTS → 剪映草稿（跳过镜头分割）"),
        (MODE_SPLIT_ONLY, "仅分割 MKV 镜头，不生成 TTS"),
    ]
    print("\n请选择本次要执行的操作：")
    for index, (_value, label) in enumerate(options, start=1):
        print(f"  {index}. {label}")
    while True:
        selection = input(f"请输入数字 [1-{len(options)}]，直接回车默认 1：").strip()
        if not selection:
            return MODE_FULL
        try:
            selected_index = int(selection)
        except ValueError:
            selected_index = 0
        if 1 <= selected_index <= len(options):
            return options[selected_index - 1][0]
        print(f"输入无效，请输入 1-{len(options)}。")


def run_scene_split(
    mkv_path: str,
    scene_output: str | None = None,
    force: bool = False,
    follow_with_tts: bool = True,
    config_path: str | None = None,
) -> None:
    splitter = Path(__file__).resolve().with_name("split_scenes.py")
    command = [sys.executable, str(splitter), mkv_path]
    if scene_output:
        command.extend(["--output", scene_output])
    if force:
        command.append("--force")
    if config_path:
        command.extend(["--config", config_path])
    print("\n第一步：检测并重新编码切割 MKV，同时提取简体中文字幕。", flush=True)
    subprocess.run(command, check=True)
    if follow_with_tts:
        print("\n第二步：进入 TTS → 剪映草稿流程。\n", flush=True)
    else:
        print("\n分割完成，本次不进入 TTS 流程。\n", flush=True)


def extract_wrapper_options(
    arguments: list[str],
) -> tuple[str | None, str | None, str | None, bool, bool, bool, bool, list[str]]:
    """Backward-compatible option extractor for wrapper tests and external callers."""
    narrato_root: str | None = None
    mkv_path: str | None = None
    scene_output: str | None = None
    show_info = False
    force_scenes = False
    skip_scene_split = False
    split_only = False
    forwarded: list[str] = []
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument == "--wrapper-info":
            show_info = True
        elif argument == "--force-scenes":
            force_scenes = True
        elif argument == "--skip-scene-split":
            skip_scene_split = True
        elif argument == "--split-only":
            split_only = True
        elif argument in {"--narrato-root", "--mkv", "--scene-output"}:
            index += 1
            if index >= len(arguments):
                raise RuntimeError(f"{argument} 后缺少路径")
            if argument == "--narrato-root":
                narrato_root = arguments[index]
            elif argument == "--mkv":
                mkv_path = arguments[index]
            else:
                scene_output = arguments[index]
        elif argument.startswith("--narrato-root="):
            narrato_root = argument.split("=", 1)[1]
        elif argument.startswith("--mkv="):
            mkv_path = argument.split("=", 1)[1]
        elif argument.startswith("--scene-output="):
            scene_output = argument.split("=", 1)[1]
        else:
            forwarded.append(argument)
        index += 1
    return (
        narrato_root,
        mkv_path,
        scene_output,
        show_info,
        force_scenes,
        skip_scene_split,
        split_only,
        forwarded,
    )


def resolve_narrato_root(explicit: str | None = None) -> Path:
    """Backward compatibility stub for tests."""
    repo_root = Path(__file__).resolve().parent
    configured = explicit or os.environ.get("NARRATOAI_ROOT")
    candidates = (
        [configured]
        if configured
        else [
            str(repo_root.parent / "NarratoAI-local"),
            str(Path.cwd() / "NarratoAI-local"),
            str(Path.home() / "NarratoAI-local"),
        ]
    )
    for raw_candidate in candidates:
        if not raw_candidate:
            continue
        candidate = Path(raw_candidate).expanduser().resolve()
        if (
            (candidate / "scripts/azure_tts_to_jianying.py").is_file()
            and (candidate / "app/models/schema.py").is_file()
            and (candidate / "app/services/jianying_draft_builder.py").is_file()
        ):
            return candidate
    raise RuntimeError("找不到完整的 NarratoAI 项目。")


def choose_python(narrato_root: Path) -> Path:
    """Backward compatibility stub for tests."""
    candidates = [
        narrato_root / ".venv/bin/python",
        narrato_root / ".venv/Scripts/python.exe",
    ]
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate.absolute()
    raise RuntimeError(f"找不到 NarratoAI 虚拟环境：{narrato_root / '.venv'}。")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="MKV 镜头分割与 TTS 剪映草稿生成工具（独立版）"
    )
    parser.add_argument("mkv_pos", nargs="?", help="待分割的 MKV 文件路径")
    parser.add_argument("--mkv", help="待分割的 MKV 文件路径")
    parser.add_argument("-c", "--config", help="配置文件 config.toml 路径")
    parser.add_argument("--scene-output", help="镜头输出目录")
    parser.add_argument("--force-scenes", action="store_true", help="覆盖已有的镜头输出")
    parser.add_argument("--skip-scene-split", action="store_true", help="跳过镜头分割，直接生成 TTS")
    parser.add_argument("--split-only", action="store_true", help="仅分割镜头，不生成 TTS")
    parser.add_argument("--wrapper-info", action="store_true", help="查看运行环境与配置信息")
    parser.add_argument("--narrato-root", help="[兼容选项] NarratoAI 项目路径（独立版无需设置）")

    # TTS 参数
    parser.add_argument("-i", "--input", help="解说词文本文件路径或文件名")
    parser.add_argument("--narration-dir", help="解说词查找目录（默认包含视频目录与用户主目录）")
    parser.add_argument("--text", help="直接传入解说词文本")
    parser.add_argument("--draft-dir", help="剪映草稿根目录 (JianyingPro Drafts)")
    parser.add_argument("--name", help="剪映草稿名称")
    parser.add_argument("--tts-engine", choices=["azure_speech", "fish_tts"], help="TTS 后端")

    # Azure Speech 参数
    parser.add_argument("--speech-key", help="Azure Speech Key")
    parser.add_argument("--speech-region", help="Azure Speech Region")
    parser.add_argument("--voice", help="Azure 音色 ShortName")
    parser.add_argument("--rate", type=float, help="语速倍率（默认 1.0）")
    parser.add_argument("--pitch", type=int, help="音调变化（Hz，默认 0）")
    parser.add_argument("--azure-volume", type=int, help="Azure 合成音量（0-100，默认 80）")
    parser.add_argument("--style", help="说话风格")
    parser.add_argument("--role", help="说话角色")
    parser.add_argument("--style-degree", type=float, help="风格强度（0.01-2.0）")

    # Fish Audio 参数
    parser.add_argument("--fish-api-key", help="Fish Audio API Key")
    parser.add_argument("--fish-model", help="Fish Audio 模型名称")
    parser.add_argument("--fish-reference-id", help="Fish Audio reference_id")
    parser.add_argument("--fish-api-url", help="Fish Audio API 地址")
    parser.add_argument("--fish-temperature", type=float, help="Fish Audio temperature")
    parser.add_argument("--fish-top-p", type=float, help="Fish Audio top_p")
    parser.add_argument("--fish-chunk-length", type=int, help="Fish Audio chunk_length")
    parser.add_argument("--fish-latency", choices=["low", "normal", "balanced"], help="Fish Audio latency")
    parser.add_argument("--fish-timeout", type=int, help="Fish Audio 请求超时秒数")

    # 草稿与音频切分细节
    parser.add_argument("--track-volume", type=float, help="剪映音轨音量（默认 1.0）")
    parser.add_argument("--chunk-size", type=int, help="TTS 分段最大字符数")
    parser.add_argument("--subtitle-max-chars", type=int, help="单条字幕最大字符数")
    parser.add_argument("--silence-threshold-db", type=float, help="静音检测阈值 dB")
    parser.add_argument("--internal-silence-seconds", type=float, help="句内静音触发长度（秒）")
    parser.add_argument("--segment-gap-seconds", type=float, help="相邻素材间隔（秒）")
    parser.add_argument("--width", type=int, help="草稿画布宽度")
    parser.add_argument("--height", type=int, help="草稿画布高度")
    parser.add_argument("--non-interactive", action="store_true", help="禁止任何交互询问")

    return parser


def resolve_narration_file(
    raw_value: str | None = None,
    mkv_source: Path | None = None,
    config_input: str | None = None,
    narration_dir: str | None = None,
    interactive: bool = True,
) -> Path | None:
    """智能解析/查找解说稿文件。

    1. 若提供了 raw_value：
       - 支持完整路径；
       - 支持仅输入文件名（如 '恶女不才第八集.txt' 或 '恶女不才第八集'），自动在当前目录、用户主目录、narration_dir 递归查找。
    2. 若未提供 raw_value（自动探测）：
       - 优先读取 config_input；
       - 若已知 MKV 视频，自动在视频所在目录、用户主目录（~）、narration_dir 查找与视频同名或相关的 .txt；
       - 若未找到直接同名文件，使用递归搜索匹配；
       - 若视频所在目录下有且仅有一个 .txt 文件，自动匹配；
       - 自动检查当前目录下的通用解说稿（如 解说词.txt、narration.txt、稿.txt 等）。
    """
    search_roots: list[Path] = []
    if narration_dir:
        nd = Path(narration_dir).expanduser().resolve()
        if nd.is_dir():
            search_roots.append(nd)
    if mkv_source and mkv_source.parent.is_dir() and mkv_source.parent not in search_roots:
        search_roots.append(mkv_source.parent)
    home_dir = Path.home().resolve()
    if home_dir not in search_roots:
        search_roots.append(home_dir)
    cwd_dir = Path.cwd().resolve()
    if cwd_dir not in search_roots:
        search_roots.append(cwd_dir)

    # 显式提供了路径或文件名
    if raw_value:
        normalized = clean_string_value(raw_value)
        if not normalized:
            return None

        direct = Path(normalized).expanduser()
        if direct.is_file():
            return direct.resolve()

        if not direct.suffix and direct.with_suffix(".txt").is_file():
            return direct.with_suffix(".txt").resolve()

        # 在候选目录下直连检查
        search_names = [normalized]
        if not normalized.endswith(".txt"):
            search_names.append(f"{normalized}.txt")

        for root in search_roots:
            for name in search_names:
                candidate = root / name
                if candidate.is_file():
                    return candidate.resolve()

        # 递归全盘查找
        matches: list[Path] = []
        for name in search_names:
            matches.extend(find_files_by_name(name, roots=search_roots))

        unique_matches = sorted(set(matches), key=lambda p: str(p))
        if len(unique_matches) == 1:
            print(f"✓ 找到解说稿：{unique_matches[0]}", flush=True)
            return unique_matches[0]
        if len(unique_matches) > 1:
            if not interactive:
                candidates = "\n".join(f"  - {p}" for p in unique_matches)
                raise RuntimeError(f"找到多个同名解说稿，请传入完整路径：\n{candidates}")
            print("找到多个同名解说稿：")
            for idx, p in enumerate(unique_matches, start=1):
                print(f"  {idx}. {p}")
            while True:
                sel = input(f"请选择解说稿 [1-{len(unique_matches)}]，直接回车默认 1：").strip()
                if not sel:
                    return unique_matches[0]
                try:
                    s_idx = int(sel)
                except ValueError:
                    s_idx = 0
                if 1 <= s_idx <= len(unique_matches):
                    return unique_matches[s_idx - 1]
                print("输入无效，请输入候选项编号。")

        raise RuntimeError(f"未找到解说稿文件：{normalized}")

    # 未提供 raw_value：执行自动探测
    if config_input:
        cand = Path(config_input).expanduser().resolve()
        if cand.is_file():
            print(f"✓ 使用配置文件中的解说稿：{cand}")
            return cand

    if mkv_source:
        stem = mkv_source.stem
        # 常见命名模式
        candidate_names = [
            f"{stem}.txt",
            f"{stem}_解说.txt",
            f"{stem}_解说词.txt",
            f"{stem}_文案.txt",
            f"{stem}_稿.txt",
        ]
        for root in search_roots:
            for name in candidate_names:
                cand = root / name
                if cand.is_file():
                    print(f"✓ 自动匹配到解说稿：{cand.resolve()}", flush=True)
                    return cand.resolve()

        # 递归查找 stem.txt
        recursive_matches = find_files_by_name(f"{stem}.txt", roots=search_roots)
        if len(recursive_matches) == 1:
            print(f"✓ 自动找到同名解说稿：{recursive_matches[0]}", flush=True)
            return recursive_matches[0]
        elif len(recursive_matches) > 1:
            print(f"✓ 找到多个与视频同名的解说稿，已选用：{recursive_matches[0]}")
            return recursive_matches[0]

        # 如果视频所在目录下有且仅有一个 .txt
        if mkv_source.parent.is_dir():
            dir_txts = [
                p.resolve()
                for p in mkv_source.parent.iterdir()
                if p.is_file() and p.suffix.lower() == ".txt"
            ]
            if len(dir_txts) == 1:
                print(f"✓ 自动选用视频目录中的唯一解说稿：{dir_txts[0]}", flush=True)
                return dir_txts[0]

    # 检查当前目录通用命名
    for default_name in ["解说词.txt", "narration.txt", "稿.txt", "文案.txt"]:
        cand = (Path.cwd() / default_name).resolve()
        if cand.is_file():
            print(f"✓ 自动选用当前目录下的解说稿：{cand}", flush=True)
            return cand

    return None


def execute_tts_workflow(
    narration: str,
    draft_dir: str,
    draft_name: str,
    tts_engine: str,
    config: dict[str, Any],
    args: argparse.Namespace,
) -> tuple[str, str]:
    """执行 TTS 生成、字词对齐、静音切分与剪映草稿构建。"""
    azure_cfg = config.get("azure", {})
    fish_cfg = config.get("fish_tts", {})
    jianying_cfg = config.get("jianying", {})
    proxy_cfg = config.get("proxy", {})
    tts_cfg = config.get("tts", {})

    rate = pick_setting(args.rate, "TTS_RATE", tts_cfg.get("rate", 1.0), 1.0, float)
    track_volume = pick_setting(args.track_volume, "JIANYING_TRACK_VOLUME", jianying_cfg.get("track_volume", 1.0), 1.0, float)
    chunk_size = pick_setting(args.chunk_size, "TTS_CHUNK_SIZE", tts_cfg.get("chunk_size", tts_service.DEFAULT_CHUNK_SIZE), tts_service.DEFAULT_CHUNK_SIZE, int)
    subtitle_max_chars = pick_setting(args.subtitle_max_chars, "TTS_SUBTITLE_MAX_CHARS", tts_cfg.get("subtitle_max_chars", 30), 30, int)
    silence_threshold_db = pick_setting(args.silence_threshold_db, "TTS_SILENCE_THRESHOLD_DB", tts_cfg.get("silence_threshold_db", tts_service.DEFAULT_SILENCE_THRESHOLD_DB), tts_service.DEFAULT_SILENCE_THRESHOLD_DB, float)
    internal_silence_seconds = pick_setting(args.internal_silence_seconds, "TTS_INTERNAL_SILENCE_SECONDS", tts_cfg.get("internal_silence_seconds", tts_service.DEFAULT_INTERNAL_SILENCE_SECONDS), tts_service.DEFAULT_INTERNAL_SILENCE_SECONDS, float)
    segment_gap_seconds = pick_setting(args.segment_gap_seconds, "TTS_SEGMENT_GAP_SECONDS", tts_cfg.get("segment_gap_seconds", tts_service.DEFAULT_SEGMENT_GAP_SECONDS), tts_service.DEFAULT_SEGMENT_GAP_SECONDS, float)
    width = pick_setting(args.width, "DRAFT_WIDTH", jianying_cfg.get("width", 1920), 1920, int)
    height = pick_setting(args.height, "DRAFT_HEIGHT", jianying_cfg.get("height", 1080), 1080, int)

    speechsdk = None
    speech_key = ""
    speech_region = ""
    voice_name = ""
    pitch = 0
    azure_volume = 80
    style = ""
    role = ""
    style_degree = 1.0

    fish_api_key = ""
    fish_model = "s2.1-pro-free"
    fish_reference_id = ""
    fish_api_url = tts_service.DEFAULT_FISH_API_URL
    fish_temperature = 0.7
    fish_top_p = 0.7
    fish_chunk_length = 300
    fish_latency = "normal"
    fish_timeout = 180

    if tts_engine == "azure_speech":
        speech_key = pick_setting(args.speech_key, "AZURE_SPEECH_KEY", azure_cfg.get("speech_key", ""), "")
        speech_region = pick_setting(args.speech_region, "AZURE_SPEECH_REGION", azure_cfg.get("speech_region", ""), "")
        voice_name = pick_setting(args.voice, "AZURE_SPEECH_VOICE", azure_cfg.get("voice", "zh-CN-XiaoyiNeural"), "zh-CN-XiaoyiNeural")
        pitch = pick_setting(args.pitch, "AZURE_SPEECH_PITCH", azure_cfg.get("pitch", 0), 0, int)
        azure_volume = pick_setting(args.azure_volume, "AZURE_SPEECH_VOLUME", azure_cfg.get("volume", 80), 80, int)
        style = pick_setting(args.style, "AZURE_SPEECH_STYLE", azure_cfg.get("style", ""), "")
        role = pick_setting(args.role, "AZURE_SPEECH_ROLE", azure_cfg.get("role", ""), "")
        style_degree = pick_setting(args.style_degree, "AZURE_SPEECH_STYLE_DEGREE", azure_cfg.get("style_degree", 1.0), 1.0, float)

        if not speech_key or not speech_region:
            if sys.stdin.isatty() and not args.non_interactive:
                print("\n未检测到完整的 Azure Speech 配置：")
                if not speech_key:
                    speech_key = getpass.getpass("请输入 Azure Speech Key：").strip()
                if not speech_region:
                    speech_region = input("请输入 Azure Speech Region (例如 eastasia)：").strip()
            if not speech_key or not speech_region:
                raise ValueError("未配置 Azure Speech Key/Region，请在 config.toml 或参数中设置")

        try:
            import azure.cognitiveservices.speech as speechsdk
        except ImportError as error:
            raise RuntimeError(
                "未安装 Azure Speech SDK，请在当前环境运行: uv add azure-cognitiveservices-speech"
            ) from error

    elif tts_engine == "fish_tts":
        fish_api_key = pick_setting(args.fish_api_key, "FISH_API_KEY", fish_cfg.get("api_key", ""), "")
        fish_model = pick_setting(args.fish_model, "FISH_MODEL", fish_cfg.get("model", "s2.1-pro-free"), "s2.1-pro-free")
        fish_reference_id = pick_setting(args.fish_reference_id, "FISH_REFERENCE_ID", fish_cfg.get("reference_id", ""), "")
        fish_api_url = pick_setting(args.fish_api_url, "FISH_API_URL", fish_cfg.get("api_url", tts_service.DEFAULT_FISH_API_URL), tts_service.DEFAULT_FISH_API_URL)
        fish_temperature = pick_setting(args.fish_temperature, "FISH_TEMPERATURE", fish_cfg.get("temperature", 0.7), 0.7, float)
        fish_top_p = pick_setting(args.fish_top_p, "FISH_TOP_P", fish_cfg.get("top_p", 0.7), 0.7, float)
        fish_chunk_length = pick_setting(args.fish_chunk_length, "FISH_CHUNK_LENGTH", fish_cfg.get("chunk_length", 300), 300, int)
        fish_latency = pick_setting(args.fish_latency, "FISH_LATENCY", fish_cfg.get("latency", "normal"), "normal")
        fish_timeout = pick_setting(args.fish_timeout, "FISH_TIMEOUT", fish_cfg.get("timeout", 180), 180, int)

        if not fish_api_key:
            if sys.stdin.isatty() and not args.non_interactive:
                fish_api_key = getpass.getpass("请输入 Fish Audio API Key：").strip()
            if not fish_api_key:
                raise ValueError("未配置 Fish Audio API Key，请在 config.toml 或参数中设置")

    proxies = None
    if proxy_cfg.get("enabled") and proxy_cfg.get("http"):
        proxies = {
            "http": proxy_cfg.get("http"),
            "https": proxy_cfg.get("https") or proxy_cfg.get("http"),
        }

    marker_pattern = re.compile(r"【\s*播放原片\s*([0-9]+(?:\.[0-9]+)?)\s*秒\s*】")
    timeline_units: list[tuple[str, str | float]] = []
    cursor = 0
    for match in marker_pattern.finditer(narration):
        text = narration[cursor:match.start()].strip()
        if text:
            timeline_units.append(("tts", text))
        duration = float(match.group(1))
        if duration <= 0:
            raise ValueError("原片播放标记的时长必须大于 0 秒")
        timeline_units.append(("original", duration))
        cursor = match.end()
    tail = narration[cursor:].strip()
    if tail:
        timeline_units.append(("tts", tail))
    if not timeline_units:
        raise ValueError("解说稿中没有可处理的正文或原片标记")
    chunks = [(kind, chunk) for kind, value in timeline_units for chunk in (
        tts_service.split_narration(str(value), chunk_size) if kind == "tts" else [value]
    )]
    print(f"解说稿共 {len(narration)} 个字符，将处理 {len(chunks)} 个时间片段（含原片占位）。")
    engine_label = f"Azure Speech ({voice_name})" if tts_engine == "azure_speech" else f"Fish Audio ({fish_model})"
    print(f"TTS 后端：{engine_label}")

    with tempfile.TemporaryDirectory(prefix=f"mkv_tts_{tts_engine}_") as temp_dir:
        audio_ranges: list[tuple[float, float, Path]] = []
        subtitle_entries: list[tuple[float, float, str]] = []
        timeline_offset = 0.0

        tts_index = 0
        for kind, chunk in chunks:
            if kind == "original":
                timeline_offset += float(chunk)
                print(f"插入原片占位 {float(chunk):g} 秒（跳过 TTS）")
                continue
            tts_index += 1
            output_file = Path(temp_dir) / f"{tts_engine}_{tts_index:03d}.wav"
            print(f"[{tts_index}] 正在合成 {len(chunk)} 个字符…")
            subtitle_lines = tts_service.split_subtitle_lines(chunk, subtitle_max_chars) or [chunk]
            tts_text = "\n".join(subtitle_lines)

            if tts_engine == "fish_tts":
                boundary_events = tts_service.synthesize_fish_chunk(
                    tts_text,
                    output_file,
                    fish_api_key,
                    fish_model,
                    fish_reference_id,
                    fish_api_url,
                    rate,
                    fish_temperature,
                    fish_top_p,
                    fish_chunk_length,
                    fish_latency,
                    fish_timeout,
                    proxies=proxies,
                )
            else:
                boundary_events = tts_service.synthesize_azure_chunk(
                    speechsdk,
                    tts_text,
                    output_file,
                    speech_key,
                    speech_region,
                    voice_name,
                    rate,
                    pitch,
                    azure_volume,
                    style,
                    role,
                    style_degree,
                )

            audio_duration = tts_service.get_audio_duration(output_file)
            audio_ranges.append((timeline_offset, timeline_offset + audio_duration, output_file))

            if tts_engine == "fish_tts":
                subtitle_entries.extend(
                    tts_service.align_subtitle_lines_strict(
                        subtitle_lines,
                        boundary_events,
                        timeline_offset,
                    )
                )
            else:
                subtitle_entries.extend(
                    tts_service.align_subtitle_lines(
                        subtitle_lines,
                        boundary_events,
                        audio_duration,
                        timeline_offset,
                    )
                )
            timeline_offset += audio_duration

        print(f"正在按字幕切分音频，共 {len(subtitle_entries)} 个片段…")
        audio_segments, subtitle_entries = tts_service.cut_audio_segments(
            audio_ranges,
            subtitle_entries,
            Path(temp_dir) / "audio_segments",
            silence_threshold_db=silence_threshold_db,
            internal_silence_seconds=internal_silence_seconds,
            segment_gap_seconds=segment_gap_seconds,
        )

        subtitle_file = Path(temp_dir) / f"{tts_engine}_subtitles.srt"
        tts_service.write_srt(subtitle_file, subtitle_entries)

        subtitle_params = VideoClipParams(
            subtitle_enabled=True,
            font_name=jianying_cfg.get("font_name", "SourceHanSansCN-Regular.otf"),
            font_size=int(jianying_cfg.get("font_size", 60)),
            text_fore_color=jianying_cfg.get("text_fore_color", "#FFFFFF"),
            stroke_color=jianying_cfg.get("stroke_color", "#000000"),
            stroke_width=float(jianying_cfg.get("stroke_width", 1.5)),
            subtitle_position=jianying_cfg.get("subtitle_position", "bottom"),
            custom_position=float(jianying_cfg.get("custom_position", 70.0)),
        )

        draft_path, actual_draft_name = write_audio_only_jianying_draft(
            jianying_draft_path=draft_dir,
            draft_name=draft_name,
            audio_files=[],
            volume=track_volume,
            width=width,
            height=height,
            subtitle_path=str(subtitle_file),
            subtitle_params=subtitle_params,
            audio_segments=audio_segments,
        )

    return draft_path, actual_draft_name


def main(arguments: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(sys.argv[1:] if arguments is None else arguments)

    # 加载配置
    config = load_config(args.config)
    jianying_cfg = config.get("jianying", {})
    tts_cfg = config.get("tts", {})
    config_file_found = find_config_file(args.config)

    if args.wrapper_info:
        print("运行模式：独立模式 (Standalone)")
        print(f"Python：{sys.executable}")
        print(f"配置文件：{config_file_found or '未找到（可在项目根目录下创建 config.toml）'}")
        print(f"剪映草稿目录：{jianying_cfg.get('draft_dir', '未配置')}")
        print(f"默认 TTS 引擎：{tts_cfg.get('engine', 'azure_speech')}")
        return 0

    interactive = sys.stdin.isatty() and not args.non_interactive
    raw_mkv = args.mkv or args.mkv_pos

    # 确定工作模式
    if args.skip_scene_split:
        mode = MODE_TTS_ONLY
    elif args.split_only:
        mode = MODE_SPLIT_ONLY
    elif interactive and not raw_mkv and not args.input and not args.text:
        mode = choose_interactive_mode()
    else:
        mode = MODE_FULL

    resolved_mkv_source: Path | None = None

    # 第一阶段：镜头分割
    if mode != MODE_TTS_ONLY:
        if not raw_mkv:
            if not interactive:
                raise RuntimeError(
                    "非交互模式下请用 --mkv 指定待分割文件，或用 --skip-scene-split 跳过镜头切割。"
                )
            raw_mkv = input("请输入要分割的 MKV 文件路径：").strip()
        raw_mkv = normalize_interactive_path(raw_mkv)
        if not raw_mkv:
            raise RuntimeError("MKV 文件路径不能为空")
        run_scene_split(
            raw_mkv,
            args.scene_output,
            args.force_scenes,
            follow_with_tts=(mode == MODE_FULL),
        )
        if mode == MODE_SPLIT_ONLY:
            return 0
        try:
            resolved_mkv_source = resolve_input_file(raw_mkv, interactive=False)
        except Exception:
            try:
                resolved_mkv_source = Path(raw_mkv).resolve()
            except Exception:
                resolved_mkv_source = None

    # 第二阶段：TTS → 剪映草稿
    if not resolved_mkv_source and raw_mkv:
        try:
            resolved_mkv_source = resolve_input_file(raw_mkv, interactive=False)
        except Exception:
            try:
                resolved_mkv_source = Path(raw_mkv).resolve()
            except Exception:
                resolved_mkv_source = None

    narration_dir = pick_setting(args.narration_dir, "NARRATION_DIR", tts_cfg.get("narration_dir"), None)
    resolved_narration_file: Path | None = None

    if not args.text:
        resolved_narration_file = resolve_narration_file(
            raw_value=args.input,
            mkv_source=resolved_mkv_source,
            config_input=tts_cfg.get("input_file"),
            narration_dir=narration_dir,
            interactive=interactive,
        )

        if not resolved_narration_file and interactive:
            user_input = input("未自动匹配到同名解说稿，请输入解说稿文件路径或文件名（直接回车从标准输入读取）：").strip()
            if user_input:
                resolved_narration_file = resolve_narration_file(
                    raw_value=user_input,
                    mkv_source=resolved_mkv_source,
                    narration_dir=narration_dir,
                    interactive=True,
                )

    narration_path_str = str(resolved_narration_file) if resolved_narration_file else None
    narration = tts_service.read_narration(narration_path_str, args.text)

    # 确定剪映草稿目录
    draft_dir = pick_setting(
        args.draft_dir,
        "JIANYING_DRAFT_DIR",
        jianying_cfg.get("draft_dir", ""),
        default="",
    )
    if not draft_dir and interactive:
        draft_dir = input("请输入剪映草稿根目录 (例如 JianyingPro Drafts 路径)：").strip()
    draft_dir = clean_string_value(draft_dir)
    if not draft_dir:
        raise ValueError("未配置剪映草稿根目录，请在 config.toml 中设置 [jianying].draft_dir 或传入 --draft-dir")

    # 确定草稿名称
    default_name = (
        f"{resolved_narration_file.stem}_TTS"
        if resolved_narration_file
        else (
            f"{resolved_mkv_source.stem}_TTS"
            if resolved_mkv_source
            else f"TTS_{int(time.time())}"
        )
    )
    draft_name = pick_setting(args.name, "DRAFT_NAME", None, default=default_name)

    # 确定 TTS 引擎
    configured_engine = str(tts_cfg.get("engine", "azure_speech")).strip().lower()
    default_engine = configured_engine if configured_engine in {"azure_speech", "fish_tts"} else "azure_speech"
    tts_engine = pick_setting(args.tts_engine, "TTS_ENGINE", default_engine, default_engine)

    draft_path, actual_draft_name = execute_tts_workflow(
        narration=narration,
        draft_dir=draft_dir,
        draft_name=draft_name,
        tts_engine=tts_engine,
        config=config,
        args=args,
    )

    print(f"\n🎉 剪映草稿生成完毕：{actual_draft_name}")
    print(f"📁 草稿路径：{draft_path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"错误：{error}", file=sys.stderr)
        raise SystemExit(1)
