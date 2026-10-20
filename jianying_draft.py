"""Standalone Jianying (CapCut) draft builder for audio and subtitle tracks."""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
from typing import Any
import uuid

logger = logging.getLogger(__name__)

MICROSECONDS = 1_000_000
DRAFT_PATH_PLACEHOLDER = "##_draftpath_placeholder_0E685133-18CE-45ED-8CB8-2904A212EC80_##"
DRAFT_PATH_PLACEHOLDER_PATTERN = re.compile(r"^##_draftpath_placeholder_[^#]+_##/")

MATERIAL_COLLECTION_KEYS = [
    "ai_translates",
    "audio_balances",
    "audio_effects",
    "audio_fades",
    "audio_pannings",
    "audio_pitch_shifts",
    "audio_track_indexes",
    "audios",
    "beats",
    "canvases",
    "chromas",
    "color_curves",
    "common_mask",
    "digital_human_model_dressing",
    "digital_humans",
    "drafts",
    "effects",
    "flowers",
    "green_screens",
    "handwrites",
    "hsl",
    "hsl_curves",
    "images",
    "log_color_wheels",
    "loudnesses",
    "manual_beautys",
    "manual_deformations",
    "material_animations",
    "material_colors",
    "multi_language_refs",
    "placeholder_infos",
    "placeholders",
    "plugin_effects",
    "primary_color_wheels",
    "realtime_denoises",
    "shapes",
    "smart_crops",
    "smart_relights",
    "sound_channel_mappings",
    "speeds",
    "stickers",
    "tail_leaders",
    "text_templates",
    "texts",
    "time_marks",
    "transitions",
    "video_effects",
    "video_radius",
    "video_shadows",
    "video_strokes",
    "video_trackings",
    "videos",
    "vocal_beautifys",
    "vocal_separations",
]

DRAFT_PACKAGE_DIRECTORIES = [
    "qr_upload",
    "matting",
    "common_attachment",
    "Resources/audioAlg",
    "Resources/digitalHuman",
    "Resources/restore_lut",
    "Resources/videoAlg",
    "subdraft",
    "adjust_mask",
    "assets/audio",
    "assets/video",
    "smart_crop",
]

DEFAULT_DRAFT_COVER_BYTES = bytes([
    0xFF, 0xD8, 0xFF, 0xDB, 0x00, 0x43, 0x00, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF,
    0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF,
    0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF,
    0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF,
    0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF,
    0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xC0, 0x00, 0x0B, 0x08, 0x00, 0x01, 0x00,
    0x01, 0x01, 0x01, 0x11, 0x00, 0xFF, 0xC4, 0x00, 0x14, 0x00, 0x01, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x01, 0xFF, 0xC4, 0x00, 0x14, 0x10, 0x01, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x01, 0xFF, 0xDA, 0x00, 0x08, 0x01, 0x01, 0x00, 0x00, 0x3F, 0x00, 0x7F,
    0xFF, 0xD9,
])


@dataclass
class VideoClipParams:
    subtitle_enabled: bool = True
    font_name: str = "SourceHanSansCN-Regular.otf"
    font_size: int = 60
    text_fore_color: str = "#FFFFFF"
    stroke_color: str = "#000000"
    stroke_width: float = 1.5
    subtitle_position: str = "bottom"
    custom_position: float = 70.0


def parse_srt_like_time(time_text: str) -> float:
    time_text = str(time_text or "").strip().replace(",", ".")
    parts = time_text.split(":")
    if len(parts) != 3:
        raise ValueError(f"不支持的时间格式: {time_text}")

    hours = int(parts[0])
    minutes = int(parts[1])
    seconds = float(parts[2])
    return hours * 3600 + minutes * 60 + seconds


def _format_draft_uuid(draft_id: str) -> str:
    compact = draft_id.replace("-", "")
    if not re.fullmatch(r"[a-fA-F0-9]{32}", compact):
        return draft_id
    return "-".join([
        compact[0:8],
        compact[8:12],
        compact[12:16],
        compact[16:20],
        compact[20:32],
    ]).upper()


def _detect_platform(draft_root_path: str) -> str:
    return "windows" if re.match(r"^(?:[a-zA-Z]:[\\/]|\\\\)", draft_root_path) else "mac"


def _create_platform_info(draft_root_path: str) -> dict[str, Any]:
    return {
        "app_id": 3704,
        "app_source": "lv",
        "app_version": "10.6.0",
        "device_id": "",
        "hard_disk_id": "",
        "mac_address": "",
        "os": _detect_platform(draft_root_path),
        "os_version": "",
    }


def _default_function_assistant_info() -> dict[str, Any]:
    return {
        "audio_noise_segid_list": [],
        "auto_adjust": False,
        "auto_adjust_fixed": False,
        "auto_adjust_fixed_value": 50.0,
        "auto_adjust_segid_list": [],
        "auto_caption": False,
        "auto_caption_segid_list": [],
        "auto_caption_template_id": "",
        "caption_opt": False,
        "caption_opt_segid_list": [],
        "color_correction": False,
        "color_correction_fixed": False,
        "color_correction_fixed_value": 50.0,
        "color_correction_segid_list": [],
        "deflicker_segid_list": [],
        "enhance_quality": False,
        "enhance_quality_fixed": False,
        "enhance_quality_segid_list": [],
        "enhance_voice_segid_list": [],
        "enhande_voice": False,
        "enhande_voice_fixed": False,
        "eye_correction": False,
        "eye_correction_segid_list": [],
        "fixed_rec_applied": False,
        "fps": {"den": 1, "num": 0},
        "normalize_loudness": False,
        "normalize_loudness_audio_denoise_segid_list": [],
        "normalize_loudness_fixed": False,
        "normalize_loudness_segid_list": [],
        "retouch": False,
        "retouch_fixed": False,
        "retouch_segid_list": [],
        "smart_rec_applied": False,
        "smart_segid_list": [],
        "smooth_slow_motion": False,
        "smooth_slow_motion_fixed": False,
        "video_noise_segid_list": [],
    }


def _safe_file_name(file_path: str, fallback: str) -> str:
    name = os.path.basename(file_path) or fallback
    name = re.sub(r'[<>:"|?*\x00-\x1f/\\]+', "_", name).strip(" ._")
    return name or fallback


def _normalize_asset_path(file_path: str | None, fallback: str) -> str:
    normalized = (file_path or "").replace("\\", "/").lstrip("./")
    without_draft_placeholder = DRAFT_PATH_PLACEHOLDER_PATTERN.sub("", normalized)
    if without_draft_placeholder.startswith("assets/"):
        return without_draft_placeholder
    assets_index = without_draft_placeholder.rfind("/assets/")
    if assets_index >= 0:
        return without_draft_placeholder[assets_index + 1:]
    return fallback


def _to_draft_material_path(relative_path: str) -> str:
    return f"{DRAFT_PATH_PLACEHOLDER}/{relative_path}"


def _unique_relative_asset_path(
    directory: str,
    file_name: str,
    used_paths: set[str],
) -> str:
    base_name, ext = os.path.splitext(file_name)
    candidate_name = file_name
    counter = 2
    while True:
        relative_path = f"{directory}/{candidate_name}"
        if relative_path not in used_paths:
            used_paths.add(relative_path)
            return relative_path
        candidate_name = f"{base_name}_{counter}{ext}"
        counter += 1


def _copy_asset_into_draft(source_file: str, draft_path: str, relative_path: str) -> None:
    destination = os.path.join(draft_path, *relative_path.split("/"))
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    if os.path.abspath(source_file) != os.path.abspath(destination):
        shutil.copy2(source_file, destination)


def _register_asset(
    source_file: str,
    draft_path: str,
    asset_dir: str,
    fallback_name: str,
    used_paths: set[str],
    asset_path_cache: dict[str, str],
) -> str:
    source_key = os.path.abspath(source_file)
    if source_key in asset_path_cache:
        return asset_path_cache[source_key]

    file_name = _safe_file_name(source_file, fallback_name)
    relative_path = _unique_relative_asset_path(asset_dir, file_name, used_paths)
    _copy_asset_into_draft(source_file, draft_path, relative_path)
    asset_path_cache[source_key] = relative_path
    return relative_path


def _create_unique_draft_path(drafts_root: str, draft_name: str) -> tuple[str, str]:
    folder_base = _safe_file_name(draft_name, f"NarratoAI_{int(time.time())}")
    folder_name = folder_base
    counter = 2
    while os.path.exists(os.path.join(drafts_root, folder_name)):
        folder_name = f"{folder_base}_{counter}"
        counter += 1
    return folder_name, os.path.join(drafts_root, folder_name)


def _create_material_collections() -> dict[str, list[Any]]:
    return {key: [] for key in MATERIAL_COLLECTION_KEYS}


def _create_draft_template(
    draft_id: str,
    draft_name: str,
    draft_root_path: str,
    width: int = 1920,
    height: int = 1080,
) -> dict[str, Any]:
    now_us = int(time.time() * MICROSECONDS)
    platform_info = _create_platform_info(draft_root_path)
    return {
        "canvas_config": {"height": height, "ratio": "original", "width": width},
        "color_space": 0,
        "config": {
            "adjust_max_index": 1,
            "attachment_info": [],
            "combination_max_index": 1,
            "export_range": None,
            "extract_audio_last_index": 1,
            "lyrics_recognition_id": "",
            "lyrics_sync": True,
            "lyrics_taskinfo": [],
            "maintrack_adsorb": True,
            "material_save_mode": 0,
            "multi_language_current": "none",
            "multi_language_list": [],
            "multi_language_main": "none",
            "multi_language_mode": "none",
            "original_sound_last_index": 1,
            "record_audio_last_index": 1,
            "sticker_max_index": 1,
            "subtitle_keywords_config": None,
            "subtitle_recognition_id": "",
            "subtitle_sync": True,
            "subtitle_taskinfo": [],
            "system_font_list": [],
            "video_mute": False,
            "zoom_info_params": None,
        },
        "cover": None,
        "create_time": now_us,
        "duration": 0,
        "extra_info": None,
        "fps": 30.0,
        "free_render_index_mode_on": False,
        "group_container": None,
        "id": draft_id,
        "keyframe_graph_list": [],
        "keyframes": {
            "adjusts": [],
            "audios": [],
            "effects": [],
            "filters": [],
            "handwrites": [],
            "stickers": [],
            "texts": [],
            "videos": [],
        },
        "last_modified_platform": platform_info,
        "materials": _create_material_collections(),
        "mutable_config": None,
        "name": draft_name,
        "new_version": "169.0.0",
        "relationships": [],
        "render_index_track_mode_on": True,
        "retouch_cover": None,
        "source": "default",
        "static_cover_image_path": "",
        "time_marks": None,
        "tracks": [],
        "update_time": now_us,
        "version": 360000,
    }


def _create_track(track_type: str, name: str) -> dict[str, Any]:
    return {
        "attribute": 0,
        "flag": 0,
        "id": uuid.uuid4().hex,
        "is_default_name": True,
        "name": name,
        "segments": [],
        "type": track_type,
    }


def _create_audio_material(relative_path: str, duration_us: int) -> dict[str, Any]:
    material_id = uuid.uuid4().hex
    return {
        "app_id": 0,
        "category_id": "",
        "category_name": "local",
        "check_flag": 1,
        "copyright_limit_type": "none",
        "duration": duration_us,
        "effect_id": "",
        "formula_id": "",
        "id": material_id,
        "intensifies_path": "",
        "is_ai_clone_tone": False,
        "is_text_edit_overdub": False,
        "is_ugc": False,
        "local_material_id": material_id,
        "music_id": material_id,
        "name": os.path.basename(relative_path),
        "path": relative_path,
        "remote_url": "",
        "query": "",
        "request_id": "",
        "resource_id": "",
        "search_id": "",
        "source_from": "",
        "source_platform": 0,
        "team_id": "",
        "text_id": "",
        "tone_category_id": "",
        "tone_category_name": "",
        "tone_effect_id": "",
        "tone_effect_name": "",
        "tone_platform": "",
        "tone_second_category_id": "",
        "tone_second_category_name": "",
        "tone_speaker": "",
        "tone_type": "",
        "type": "extract_music",
        "video_id": "",
        "wave_points": [],
    }


def _create_audio_segment(
    material_id: str,
    duration_us: int,
    target_start_us: int,
    volume: float,
) -> dict[str, Any]:
    return {
        "id": uuid.uuid4().hex,
        "material_id": material_id,
        "target_timerange": {"start": target_start_us, "duration": duration_us},
        "source_timerange": {"start": 0, "duration": duration_us},
        "speed": 1.0,
        "volume": volume,
        "extra_material_refs": [],
        "clip": None,
        "hdr_settings": None,
        "uniform_scale": None,
        "common_keyframes": [],
    }


def _normalize_hex_color(color: str | None, default: str = "#FFFFFF") -> str:
    col = str(color or default).strip()
    if not col.startswith("#"):
        col = f"#{col}"
    if re.fullmatch(r"#[0-9a-fA-F]{3}", col):
        col = "#" + "".join(char * 2 for char in col[1:])
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", col):
        col = default
    return col.upper()


def _hex_color_to_rgb_float(color: str | None, default: str = "#FFFFFF") -> tuple[float, float, float]:
    normalized = _normalize_hex_color(color, default)
    return (
        int(normalized[1:3], 16) / 255,
        int(normalized[3:5], 16) / 255,
        int(normalized[5:7], 16) / 255,
    )


def _resolve_subtitle_text_size(params: VideoClipParams) -> float:
    raw_size = getattr(params, "font_size", 60) or 60
    try:
        font_size = float(raw_size)
    except (TypeError, ValueError):
        font_size = 60.0
    return max(4.0, min(10.0, font_size / 12.0))


def _resolve_subtitle_transform_y(params: VideoClipParams) -> float:
    subtitle_position = str(getattr(params, "subtitle_position", "bottom") or "bottom").lower()
    if subtitle_position == "top":
        return 0.82
    if subtitle_position == "center":
        return 0.0
    if subtitle_position == "custom":
        try:
            y_percent = float(getattr(params, "custom_position", 85.0))
        except (TypeError, ValueError):
            y_percent = 85.0
        y_percent = max(0.0, min(100.0, y_percent))
        return max(-0.92, min(0.92, 1.0 - 2.0 * (y_percent / 100.0)))
    return -0.8


def _create_text_material(text: str, params: VideoClipParams) -> dict[str, Any]:
    material_id = uuid.uuid4().hex
    text_content = str(text or "")
    text_color = _hex_color_to_rgb_float(getattr(params, "text_fore_color", "#FFFFFF"), "#FFFFFF")
    stroke_color = _hex_color_to_rgb_float(getattr(params, "stroke_color", "#000000"), "#000000")
    try:
        stroke_width = float(getattr(params, "stroke_width", 1.5) or 0)
    except (TypeError, ValueError):
        stroke_width = 1.5

    text_style: dict[str, Any] = {
        "fill": {
            "alpha": 1.0,
            "content": {
                "render_type": "solid",
                "solid": {
                    "alpha": 1.0,
                    "color": list(text_color),
                },
            },
        },
        "range": [0, len(text_content)],
        "size": _resolve_subtitle_text_size(params),
        "bold": False,
        "italic": False,
        "underline": False,
        "strokes": [],
    }
    check_flag = 7
    if stroke_width > 0:
        text_style["strokes"] = [
            {
                "content": {
                    "solid": {
                        "alpha": 1.0,
                        "color": list(stroke_color),
                    }
                },
                "width": max(0.0, min(0.2, stroke_width / 100.0 * 0.2)),
            }
        ]
        check_flag |= 8

    return {
        "id": material_id,
        "content": json.dumps(
            {
                "styles": [text_style],
                "text": text_content,
            },
            ensure_ascii=False,
        ),
        "typesetting": 0,
        "alignment": 1,
        "letter_spacing": 0.0,
        "line_spacing": 0.02,
        "line_feed": 1,
        "line_max_width": 0.82,
        "force_apply_line_max_width": False,
        "check_flag": check_flag,
        "type": "subtitle",
        "global_alpha": 1.0,
    }


def _create_text_segment(
    material_id: str,
    start_us: int,
    duration_us: int,
    params: VideoClipParams,
) -> dict[str, Any]:
    return {
        "id": uuid.uuid4().hex,
        "material_id": material_id,
        "target_timerange": {"start": start_us, "duration": duration_us},
        "source_timerange": None,
        "speed": 1.0,
        "volume": 1.0,
        "extra_material_refs": [],
        "is_tone_modify": False,
        "clip": {
            "alpha": 1.0,
            "flip": {"horizontal": False, "vertical": False},
            "rotation": 0.0,
            "scale": {"x": 1.0, "y": 1.0},
            "transform": {"x": 0.0, "y": _resolve_subtitle_transform_y(params)},
        },
        "uniform_scale": {"on": True, "value": 1.0},
        "render_index": 15000,
        "common_keyframes": [],
    }


def _parse_srt_entries(subtitle_path: str) -> list[tuple[float, float, str]]:
    if not subtitle_path or not os.path.exists(subtitle_path):
        return []

    with open(subtitle_path, "r", encoding="utf-8-sig") as f:
        content = f.read().strip()
    if not content:
        return []

    entries: list[tuple[float, float, str]] = []
    for block in re.split(r"\n\s*\n", content):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        time_line_index = next(
            (index for index, line in enumerate(lines) if "-->" in line),
            None,
        )
        if time_line_index is None or time_line_index + 1 >= len(lines):
            continue

        try:
            start_text, end_text = lines[time_line_index].split("-->", 1)
            start = parse_srt_like_time(start_text)
            end = parse_srt_like_time(end_text)
        except Exception:
            continue

        text = "\n".join(lines[time_line_index + 1:]).strip()
        if end <= start or not text:
            continue
        entries.append((start, end, text))

    return entries


def _add_subtitle_track_from_srt(
    draft: dict[str, Any],
    subtitle_path: str,
    params: VideoClipParams,
) -> int:
    entries = _parse_srt_entries(subtitle_path)
    if not entries:
        return 0

    text_track = _create_track("text", "字幕轨道")
    text_track["is_default_name"] = False
    max_end_us = 0
    for start, end, text in entries:
        start_us = _seconds_to_microseconds(start)
        duration_us = _seconds_to_microseconds(end - start)
        if duration_us <= 0:
            continue

        text_material = _create_text_material(text, params)
        draft["materials"]["texts"].append(text_material)
        text_track["segments"].append(_create_text_segment(
            text_material["id"],
            start_us,
            duration_us,
            params,
        ))
        max_end_us = max(max_end_us, start_us + duration_us)

    if text_track["segments"]:
        draft["tracks"].append(text_track)
    return max_end_us


def _write_json_file(file_path: str, data: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))


def _floor_duration_to_milliseconds(duration: float) -> float:
    return int((max(float(duration), 0.0) + 1e-9) * 1000) / 1000.0


def _seconds_to_microseconds(seconds: float) -> int:
    return int(round(max(seconds, 0.0) * MICROSECONDS))


def _get_media_duration_ffprobe(media_file: str) -> float:
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "csv=p=0",
        media_file,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return float(result.stdout.strip())


def _get_cached_media_duration(media_file: str, duration_cache: dict[str, float]) -> float:
    if media_file not in duration_cache:
        duration_cache[media_file] = _floor_duration_to_milliseconds(
            _get_media_duration_ffprobe(media_file)
        )
    return duration_cache[media_file]


def _normalize_segment(
    segment: dict[str, Any],
    track_type: str,
    track_index: int,
    track_attribute: int,
) -> dict[str, Any]:
    is_video = track_type == "video"
    result = {
        "caption_info": None,
        "cartoon": False,
        "color_correct_alg_result": "",
        "common_keyframes": [],
        "desc": "",
        "digital_human_template_group_id": "",
        "enable_adjust": is_video,
        "enable_adjust_mask": is_video,
        "enable_color_adjust_pro": False,
        "enable_color_correct_adjust": False,
        "enable_color_curves": True,
        "enable_color_match_adjust": False,
        "enable_color_wheels": True,
        "enable_hsl": is_video,
        "enable_hsl_curves": True,
        "enable_lut": is_video,
        "enable_mask_shadow": False,
        "enable_mask_stroke": False,
        "enable_smart_color_adjust": False,
        "enable_video_mask": True,
        "extra_material_refs": [],
        "group_id": "",
        "hdr_settings": segment.get("hdr_settings"),
        "intensifies_audio": False,
        "is_loop": False,
        "is_placeholder": False,
        "is_tone_modify": False,
        "keyframe_refs": [],
        "last_nonzero_volume": 1.0,
        "lyric_keyframes": None,
        "raw_segment_id": "",
        "render_index": 0,
        "render_timerange": {"duration": 0, "start": 0},
        "responsive_layout": {
            "enable": False,
            "horizontal_pos_layout": 0,
            "size_layout": 0,
            "target_follow": "",
            "vertical_pos_layout": 0,
        },
        "reverse": False,
        "source": "segmentsourcenormal",
        "state": 0,
        "template_id": "",
        "template_scene": "default",
        "uniform_scale": {"on": True, "value": 1.0},
        "visible": True,
    }
    result.update(segment)
    result["track_attribute"] = track_attribute
    result["track_render_index"] = track_index
    return result


def _normalize_tracks(draft: dict[str, Any]) -> list[dict[str, Any]]:
    tracks = []
    for index, track in enumerate(draft.get("tracks", [])):
        track_copy = dict(track)
        track_attribute = int(track_copy.get("attribute", 0) or 0)
        track_copy["segments"] = [
            _normalize_segment(segment, track_copy.get("type", ""), index, track_attribute)
            for segment in track.get("segments", [])
        ]
        tracks.append(track_copy)
    return tracks


def _normalize_audio_material(material: dict[str, Any]) -> dict[str, Any]:
    fallback_path = f"assets/audio/{material.get('name') or 'audio.mp3'}"
    result = dict(material)
    result["path"] = _to_draft_material_path(
        _normalize_asset_path(material.get("path"), fallback_path)
    )
    result["type"] = "extract_music"
    return result


def _normalize_materials(draft: dict[str, Any]) -> dict[str, list[Any]]:
    source = draft.get("materials", {})
    materials = {
        key: source.get(key, []) if isinstance(source.get(key, []), list) else []
        for key in MATERIAL_COLLECTION_KEYS
    }
    materials["videos"] = []
    materials["audios"] = [_normalize_audio_material(item) for item in source.get("audios", [])]
    return materials


def _create_draft_info(
    draft: dict[str, Any],
    draft_name: str,
    draft_root_path: str,
    new_version: str = "169.0.0",
) -> dict[str, Any]:
    info = json.loads(json.dumps(draft, ensure_ascii=False))
    canvas_config = info.get("canvas_config", {})
    platform_info = _create_platform_info(draft_root_path)
    info.update({
        "canvas_config": {
            "background": canvas_config.get("background"),
            "height": canvas_config.get("height", 1080),
            "ratio": canvas_config.get("ratio", "original"),
            "width": canvas_config.get("width", 1920),
        },
        "draft_type": "video",
        "function_assistant_info": _default_function_assistant_info(),
        "is_drop_frame_timecode": False,
        "last_modified_platform": platform_info,
        "lyrics_effects": [],
        "materials": _normalize_materials(info),
        "name": draft_name,
        "new_version": new_version,
        "path": "",
        "platform": platform_info,
        "render_index_track_mode_on": True,
        "smart_ads_info": {"draft_url": "", "page_from": "", "routine": ""},
        "tracks": _normalize_tracks(info),
        "uneven_animation_template_info": {
            "composition": "",
            "content": "",
            "order": "",
            "sub_template_info_list": [],
        },
    })
    return info


def _create_empty_template(draft: dict[str, Any], draft_root_path: str) -> dict[str, Any]:
    empty_draft = json.loads(json.dumps(draft, ensure_ascii=False))
    empty_draft["canvas_config"] = {
        "background": None,
        "height": 0,
        "ratio": "original",
        "width": 0,
    }
    empty_draft["color_space"] = -1
    empty_draft["duration"] = 0
    empty_draft["keyframes"] = {
        "adjusts": [],
        "audios": [],
        "effects": [],
        "filters": [],
        "handwrites": [],
        "stickers": [],
        "texts": [],
        "videos": [],
    }
    empty_draft["materials"] = _create_material_collections()
    empty_draft["tracks"] = []
    return _create_draft_info(empty_draft, "", draft_root_path, "75.0.0")


def _create_draft_material_index_item(
    material: dict[str, Any],
    file_name: str,
    metetype: str,
    width: int,
    height: int,
) -> dict[str, Any]:
    duration = int(material.get("duration", 0) or 0)
    return {
        "ai_group_type": "",
        "create_time": -1,
        "duration": duration,
        "enter_from": 0,
        "extra_info": file_name,
        "file_Path": material.get("path", ""),
        "height": height,
        "id": material.get("id", ""),
        "import_time": -1,
        "import_time_ms": -1,
        "item_source": 1,
        "md5": "",
        "metetype": metetype,
        "roughcut_time_range": {"duration": duration, "start": 0},
        "sub_time_range": {"duration": -1, "start": -1},
        "type": 0,
        "width": width,
    }


def _create_draft_material_index(draft: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for audio in draft.get("materials", {}).get("audios", []):
        relative_path = _normalize_asset_path(
            audio.get("path"),
            f"assets/audio/{audio.get('name') or 'audio.mp3'}",
        )
        items.append(_create_draft_material_index_item(
            {**audio, "path": _to_draft_material_path(relative_path)},
            os.path.basename(relative_path),
            "music",
            0,
            0,
        ))
    return items


def _create_meta_info(
    draft: dict[str, Any],
    draft_name: str,
    draft_id: str,
    draft_root_path: str,
    draft_path: str,
    asset_size: int,
) -> dict[str, Any]:
    return {
        "cloud_draft_cover": False,
        "cloud_draft_sync": False,
        "draft_cloud_last_action_download": False,
        "draft_cloud_purchase_info": "",
        "draft_cloud_template_id": "",
        "draft_cloud_tutorial_info": "",
        "draft_cloud_videocut_purchase_info": "",
        "draft_cover": os.path.join(draft_path, "draft_cover.jpg"),
        "draft_fold_path": draft_path,
        "draft_id": _format_draft_uuid(draft_id),
        "draft_is_ai_shorts": False,
        "draft_is_cloud_temp_draft": False,
        "draft_is_invisible": False,
        "draft_is_web_article_video": False,
        "draft_json_file": os.path.join(draft_path, "draft_info.json"),
        "draft_name": draft_name,
        "draft_new_version": "",
        "draft_root_path": draft_root_path,
        "draft_timeline_materials_size": asset_size,
        "draft_type": "",
        "draft_web_article_video_enter_from": "",
        "streaming_edit_draft_ready": True,
        "tm_draft_cloud_completed": "",
        "tm_draft_cloud_entry_id": -1,
        "tm_draft_cloud_modified": 0,
        "tm_draft_cloud_parent_entry_id": -1,
        "tm_draft_cloud_space_id": -1,
        "tm_draft_cloud_user_id": -1,
        "tm_draft_create": draft.get("create_time", 0),
        "tm_draft_modified": draft.get("update_time", 0),
        "tm_draft_removed": 0,
        "tm_duration": draft.get("duration", 0),
    }


def _create_root_meta_entry(
    draft: dict[str, Any],
    draft_name: str,
    draft_id: str,
    draft_root_path: str,
    draft_path: str,
    asset_size: int,
) -> dict[str, Any]:
    return {
        "draft_cloud_capcut_purchase_info": "",
        "draft_cloud_last_action_download": False,
        "draft_cloud_package_type": "",
        "draft_cloud_purchase_info": "",
        "draft_cloud_template_id": "",
        "draft_cloud_tutorial_info": "",
        "draft_cloud_videocut_purchase_info": "",
        "draft_cover": "draft_cover.jpg",
        "draft_deeplink_url": "",
        "draft_enterprise_info": {
            "draft_enterprise_extra": "",
            "draft_enterprise_id": "",
            "draft_enterprise_name": "",
            "enterprise_material": [],
        },
        "draft_fold_path": draft_path,
        "draft_id": _format_draft_uuid(draft_id),
        "draft_is_ae_produce": False,
        "draft_is_ai_packaging_used": False,
        "draft_is_ai_shorts": False,
        "draft_is_ai_translate": False,
        "draft_is_article_video_draft": False,
        "draft_is_cloud_temp_draft": False,
        "draft_is_from_deeplink": "false",
        "draft_is_invisible": False,
        "draft_is_pippit_draft": False,
        "draft_is_web_article_video": False,
        "draft_materials": [
            {"type": 0, "value": _create_draft_material_index(draft)},
            {"type": 1, "value": []},
            {"type": 2, "value": []},
            {"type": 3, "value": []},
            {"type": 6, "value": []},
            {"type": 7, "value": []},
            {"type": 8, "value": []},
        ],
        "draft_materials_copied_info": [],
        "draft_name": draft_name,
        "draft_need_rename_folder": False,
        "draft_new_version": "",
        "draft_removable_storage_device": "",
        "draft_root_path": draft_root_path,
        "draft_segment_extra_info": [],
        "draft_timeline_materials_size_": asset_size,
        "draft_type": "",
        "draft_web_article_video_enter_from": "",
        "tm_draft_cloud_completed": "",
        "tm_draft_cloud_entry_id": -1,
        "tm_draft_cloud_modified": 0,
        "tm_draft_cloud_parent_entry_id": -1,
        "tm_draft_cloud_space_id": -1,
        "tm_draft_cloud_user_id": -1,
        "tm_draft_create": draft.get("create_time", 0),
        "tm_draft_modified": draft.get("update_time", 0),
        "tm_draft_removed": 0,
        "tm_duration": draft.get("duration", 0),
    }


def _merge_root_meta_info(
    existing_value: Any,
    next_entry: dict[str, Any],
    root_path: str,
) -> dict[str, Any]:
    existing = existing_value if isinstance(existing_value, dict) else {}
    existing_store = existing.get("all_draft_store")
    if not isinstance(existing_store, list):
        existing_store = []

    all_draft_store = [
        next_entry,
        *[
            entry for entry in existing_store
            if (
                isinstance(entry, dict)
                and entry.get("draft_id") != next_entry.get("draft_id")
                and entry.get("draft_fold_path") != next_entry.get("draft_fold_path")
                and entry.get("draft_name") != next_entry.get("draft_name")
            )
        ],
    ]
    return {
        "all_draft_store": all_draft_store,
        "draft_ids": existing.get("draft_ids") if isinstance(existing.get("draft_ids"), int) else 1,
        "root_path": existing.get("root_path") or root_path,
    }


def _write_root_meta_info(draft_root_path: str, root_meta_entry: dict[str, Any]) -> None:
    root_meta_path = os.path.join(draft_root_path, "root_meta_info.json")
    existing_value: Any = {}
    if os.path.exists(root_meta_path):
        try:
            with open(root_meta_path, "r", encoding="utf-8") as f:
                existing_value = json.load(f)
        except Exception:
            pass

    _write_json_file(
        root_meta_path,
        _merge_root_meta_info(existing_value, root_meta_entry, draft_root_path),
    )


def _create_draft_settings(draft: dict[str, Any], draft_root_path: str) -> str:
    created_at = round(int(draft.get("create_time", 0) or 0) / MICROSECONDS)
    updated_at = round(int(draft.get("update_time", 0) or 0) / MICROSECONDS)
    return "\n".join([
        "[General]",
        f"cloud_last_modify_platform={_detect_platform(draft_root_path)}",
        f"draft_create_time={created_at}",
        f"draft_last_edit_time={updated_at}",
        "real_edit_keys=1",
        "real_edit_seconds=0",
        "",
    ])


def _create_draft_virtual_store(draft: dict[str, Any]) -> dict[str, Any]:
    materials = draft.get("materials", {}).get("audios", [])
    return {
        "draft_materials": [],
        "draft_virtual_store": [
            {
                "type": 0,
                "value": [
                    {
                        "creation_time": 0,
                        "display_name": "",
                        "filter_type": 0,
                        "id": "",
                        "import_time": 0,
                        "import_time_us": 0,
                        "sort_sub_type": 0,
                        "sort_type": 0,
                        "subdraft_filter_type": 0,
                    }
                ],
            },
            {
                "type": 1,
                "value": [
                    {"child_id": material.get("id", ""), "parent_id": ""}
                    for material in materials
                ],
            },
            {"type": 2, "value": []},
        ],
    }


def _write_plaintext_draft_files(
    draft_root_path: str,
    draft_path: str,
    draft_name: str,
    draft_id: str,
    draft: dict[str, Any],
    asset_size: int,
) -> None:
    draft_info = _create_draft_info(draft, draft_name, draft_root_path)
    empty_template = _create_empty_template(draft, draft_root_path)

    _write_json_file(
        os.path.join(draft_path, "draft_meta_info.json"),
        _create_meta_info(draft, draft_name, draft_id, draft_root_path, draft_path, asset_size),
    )
    with open(os.path.join(draft_path, "draft_settings"), "w", encoding="utf-8") as f:
        f.write(_create_draft_settings(draft, draft_root_path))
    with open(os.path.join(draft_path, "draft_cover.jpg"), "wb") as f:
        f.write(DEFAULT_DRAFT_COVER_BYTES)
    _write_json_file(os.path.join(draft_path, "draft_info.json"), draft_info)
    _write_json_file(os.path.join(draft_path, "template-2.tmp"), draft_info)
    _write_json_file(os.path.join(draft_path, "template.tmp"), empty_template)
    _write_json_file(
        os.path.join(draft_path, "common_attachment", "attachment_pc_timeline.json"),
        {
            "reference_lines_config": {
                "horizontal_lines": [],
                "is_lock": False,
                "is_visible": False,
                "vertical_lines": [],
            },
            "safe_area_type": 0,
        },
    )
    _write_json_file(os.path.join(draft_path, "common_attachment", "attachment_action_scene.json"), {})
    _write_json_file(os.path.join(draft_path, "common_attachment", "attachment_script_video.json"), {})
    _write_json_file(os.path.join(draft_path, "common_attachment", "attachment_gen_ai_info.json"), {})
    _write_json_file(
        os.path.join(draft_path, "attachment_editing.json"),
        {"editing_draft": {"edit_type": 0, "version": "1.0.0"}},
    )
    _write_json_file(
        os.path.join(draft_path, "draft_agency_config.json"),
        {
            "is_auto_agency_enabled": False,
            "is_auto_agency_popup": False,
            "is_single_agency_mode": False,
            "marterials": None,
            "use_converter": False,
            "video_resolution": draft.get("canvas_config", {}).get("height", 1080),
        },
    )
    with open(os.path.join(draft_path, "draft_biz_config.json"), "w", encoding="utf-8"):
        pass
    _write_json_file(
        os.path.join(draft_path, "draft_virtual_store.json"),
        _create_draft_virtual_store(draft),
    )
    _write_json_file(
        os.path.join(draft_path, "performance_opt_info.json"),
        {"manual_cancle_precombine_segs": None, "need_auto_precombine_segs": None},
    )
    _write_json_file(
        os.path.join(draft_path, "timeline_layout.json"),
        {
            "activeTimeline": draft_id,
            "dockItems": [
                {
                    "dockIndex": 0,
                    "ratio": 1,
                    "timelineIds": [draft_id],
                    "timelineNames": ["时间线01"],
                }
            ],
            "layoutOrientation": 1,
        },
    )
    _write_root_meta_info(
        draft_root_path,
        _create_root_meta_entry(draft, draft_name, draft_id, draft_root_path, draft_path, asset_size),
    )


def write_audio_only_jianying_draft(
    jianying_draft_path: str,
    draft_name: str,
    audio_files: list[str],
    volume: float = 1.0,
    width: int = 1920,
    height: int = 1080,
    subtitle_path: str = "",
    subtitle_params: VideoClipParams | None = None,
    audio_segments: list[dict[str, Any]] | None = None,
) -> tuple[str, str]:
    """创建不含视频轨、只含 TTS 音频和可选字幕轨的剪映草稿。"""
    # 彻底清理用户可能带有的外层单双引号
    cleaned_draft_root = str(jianying_draft_path or "").strip()
    if (cleaned_draft_root.startswith('"') and cleaned_draft_root.endswith('"')) or (
        cleaned_draft_root.startswith("'") and cleaned_draft_root.endswith("'")
    ):
        cleaned_draft_root = cleaned_draft_root[1:-1].strip()

    if not cleaned_draft_root or cleaned_draft_root in {"/", "\\"}:
        raise ValueError(f"剪映草稿目录无效: {jianying_draft_path}")

    cleaned_draft_root = str(Path(cleaned_draft_root).expanduser().resolve())

    if not audio_files and not audio_segments:
        raise ValueError("至少需要一个 TTS 音频文件")

    normalized_audio_files = []
    if audio_segments is not None:
        if not audio_segments:
            raise ValueError("至少需要一个分段音频文件")
        source_items = [
            (
                item.get("audio_file") or item.get("path"),
                item.get("start_time"),
            )
            for item in audio_segments
        ]
    else:
        source_items = [(audio_file, None) for audio_file in audio_files]

    for audio_file, target_start in source_items:
        normalized_path = os.path.abspath(os.path.expanduser(str(audio_file or "")))
        if not os.path.isfile(normalized_path):
            raise ValueError(f"音频文件不存在: {audio_file}")
        if target_start is None:
            normalized_audio_files.append((normalized_path, None))
        else:
            try:
                target_start_val = max(0.0, float(target_start))
            except (TypeError, ValueError) as error:
                raise ValueError(f"音频片段开始时间无效: {target_start}") from error
            normalized_audio_files.append((normalized_path, target_start_val))

    os.makedirs(cleaned_draft_root, exist_ok=True)
    display_name = draft_name or f"TTS_{int(time.time())}"
    folder_name, draft_path = _create_unique_draft_path(cleaned_draft_root, display_name)
    os.makedirs(draft_path, exist_ok=False)
    for rel_dir in DRAFT_PACKAGE_DIRECTORIES:
        os.makedirs(os.path.join(draft_path, *rel_dir.split("/")), exist_ok=True)

    draft_id = uuid.uuid4().hex
    draft = _create_draft_template(
        draft_id,
        display_name,
        cleaned_draft_root,
        width=max(1, int(width)),
        height=max(1, int(height)),
    )
    audio_track = _create_track("audio", "TTS 音频轨道")
    draft["tracks"] = [audio_track]

    duration_cache: dict[str, float] = {}
    used_asset_paths: set[str] = set()
    asset_path_cache: dict[str, str] = {}
    current_time_us = 0

    for index, (audio_file, target_start) in enumerate(normalized_audio_files, start=1):
        duration = _get_cached_media_duration(audio_file, duration_cache)
        duration_us = _seconds_to_microseconds(duration)
        if duration_us <= 0:
            raise ValueError(f"无法获取有效音频时长: {audio_file}")

        extension = os.path.splitext(audio_file)[1] or ".wav"
        relative_path = _register_asset(
            audio_file,
            draft_path,
            "assets/audio",
            f"tts_{index:03d}{extension}",
            used_asset_paths,
            asset_path_cache,
        )
        audio_material = _create_audio_material(relative_path, duration_us)
        draft["materials"]["audios"].append(audio_material)
        segment_start_us = (
            _seconds_to_microseconds(target_start)
            if target_start is not None
            else current_time_us
        )
        audio_track["segments"].append(
            _create_audio_segment(
                audio_material["id"],
                duration_us,
                segment_start_us,
                float(volume),
            )
        )
        current_time_us = max(current_time_us, segment_start_us + duration_us)

    subtitle_end_us = 0
    if subtitle_path:
        subtitle_params = subtitle_params or VideoClipParams(subtitle_enabled=True)
        subtitle_end_us = _add_subtitle_track_from_srt(
            draft,
            subtitle_path,
            subtitle_params,
        )

    draft["duration"] = max(current_time_us, subtitle_end_us)
    draft["update_time"] = int(time.time() * MICROSECONDS)
    asset_size = sum(
        os.path.getsize(source_file)
        for source_file in asset_path_cache
        if os.path.exists(source_file)
    )
    _write_plaintext_draft_files(
        cleaned_draft_root,
        draft_path,
        display_name,
        draft_id,
        draft,
        asset_size,
    )

    return draft_path, display_name
