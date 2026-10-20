"""TTS synthesis (Azure Speech & Fish Audio), boundary alignment, and audio segmentation."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from xml.sax.saxutils import escape, quoteattr

DEFAULT_CHUNK_SIZE = 2500
SEGMENT_HEAD_GUARD_SECONDS = 0.08
SEGMENT_TAIL_GUARD_SECONDS = 0.12
DEFAULT_SILENCE_THRESHOLD_DB = -45.0
DEFAULT_INTERNAL_SILENCE_SECONDS = 0.08
DEFAULT_SEGMENT_GAP_SECONDS = 0.1
DEFAULT_FISH_API_URL = "https://api.fish.audio/v1/tts/stream/with-timestamp"


def read_narration(input_path: str | None, inline_text: str | None) -> str:
    if inline_text is not None:
        text = inline_text
    elif input_path:
        text = Path(input_path).expanduser().read_text(encoding="utf-8-sig")
    elif not sys.stdin.isatty():
        text = sys.stdin.read()
    else:
        raise ValueError("请传入解说稿文件路径，或使用 --text 传入文本")

    text = text.strip()
    if not text:
        raise ValueError("解说稿内容为空")
    return text


def _split_oversized_text(text: str, max_chars: int) -> list[str]:
    chunks = []
    remaining = text.strip()
    punctuation = "，,、：:。！？!?；; \t"
    while len(remaining) > max_chars:
        window = remaining[: max_chars + 1]
        split_at = max(window.rfind(char) for char in punctuation)
        if split_at < max_chars // 2:
            split_at = max_chars
        else:
            split_at += 1
        chunks.append(remaining[:split_at].strip())
        remaining = remaining[split_at:].strip()
    if remaining:
        chunks.append(remaining)
    return chunks


def split_narration(text: str, max_chars: int = DEFAULT_CHUNK_SIZE) -> list[str]:
    """优先按段落和句号分块，避免超过单次 TTS 请求的实用长度。"""
    if max_chars < 100:
        raise ValueError("chunk_size 不能小于 100")

    normalized = re.sub(r"\r\n?", "\n", text).strip()
    units = [
        unit.strip()
        for unit in re.findall(r"[^\n。！？!?；;]+[。！？!?；;]*|\n+", normalized)
        if unit.strip()
    ]
    chunks: list[str] = []
    current = ""
    for unit in units:
        separator = "\n" if "\n" in unit else ""
        if unit.startswith("\n"):
            unit = unit.strip()
        candidate = f"{current}{separator}{unit}" if current else unit
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            chunks.append(current)
            current = ""
        oversized_parts = _split_oversized_text(unit, max_chars)
        chunks.extend(oversized_parts[:-1])
        current = oversized_parts[-1] if oversized_parts else ""
    if current:
        chunks.append(current)
    return chunks


def split_subtitle_lines(text: str, max_chars: int = 30) -> list[str]:
    """按强、弱标点切分字幕，避免在短语中间硬切。"""
    if max_chars < 1:
        raise ValueError("subtitle_max_chars 必须大于 0")
    normalized = re.sub(r"\s+", " ", text).strip()
    strong_sentences = re.findall(r"[^。！？!?；;]+(?:[。！？!?；;]+|$)", normalized)
    lines: list[str] = []
    for sentence in strong_sentences or [normalized]:
        sentence = sentence.strip()
        if len(sentence) <= max_chars:
            if sentence:
                lines.append(sentence)
            continue
        phrases = re.findall(r"[^，,、：:]+(?:[，,、：:]+|$)", sentence) or [sentence]
        current = ""
        for phrase in phrases:
            phrase = phrase.strip()
            candidate = f"{current}{phrase}" if current else phrase
            if current and len(candidate) > max_chars:
                lines.append(current)
                current = phrase
            else:
                current = candidate
        if current:
            lines.append(current)
    return lines


def _normalize_alignment_text(text: str) -> str:
    return re.sub(r"[\W_]+", "", str(text or ""), flags=re.UNICODE).casefold()


def _azure_duration_seconds(duration: Any) -> float:
    if hasattr(duration, "total_seconds"):
        return float(duration.total_seconds())
    if isinstance(duration, int):
        return duration / 10_000_000
    try:
        hours, minutes, seconds = str(duration).split(":", 2)
        return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    except (TypeError, ValueError):
        return 0.0


def get_audio_duration(audio_file: Path | str) -> float:
    result = subprocess.run(
        [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "csv=p=0",
            str(audio_file),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    duration = float(result.stdout.strip())
    if duration <= 0:
        raise RuntimeError(f"无法获取有效音频时长: {audio_file}")
    return int((duration + 1e-9) * 1000) / 1000.0


def align_subtitle_lines(
    lines: list[str],
    boundary_events: list[tuple[float, float, str]],
    audio_duration: float,
    timeline_offset: float,
) -> list[tuple[float, float, str]]:
    """使用 Azure 词边界对齐。"""
    entries: list[tuple[float, float, str]] = []
    resolved_count = 0
    current_start: float | None = None
    accumulated = ""
    previous_end = 0.0

    for start, end, event_text in boundary_events:
        if resolved_count >= len(lines):
            break
        if current_start is None:
            current_start = start
        accumulated += event_text
        if _normalize_alignment_text(accumulated) == _normalize_alignment_text(lines[resolved_count]):
            entries.append(
                (timeline_offset + current_start, timeline_offset + end, lines[resolved_count])
            )
            previous_end = end
            resolved_count += 1
            current_start = None
            accumulated = ""

    remaining = lines[resolved_count:]
    if remaining:
        weights = [max(1, len(_normalize_alignment_text(line))) for line in remaining]
        total_weight = sum(weights)
        fallback_duration = max(0.001, audio_duration - previous_end)
        cursor = previous_end
        consumed_weight = 0
        for index, (line, weight) in enumerate(zip(remaining, weights), start=1):
            consumed_weight += weight
            end = (
                audio_duration
                if index == len(remaining)
                else previous_end + fallback_duration * consumed_weight / total_weight
            )
            entries.append((timeline_offset + cursor, timeline_offset + end, line))
            cursor = end
    return entries


def align_subtitle_lines_strict(
    lines: list[str],
    boundary_events: list[tuple[float, float, str]],
    timeline_offset: float,
) -> list[tuple[float, float, str]]:
    """只在时间戳文本完整匹配时生成字幕，不做按时长估算。"""
    expected = _normalize_alignment_text("".join(lines))
    actual = _normalize_alignment_text("".join(event[2] for event in boundary_events))
    if not expected or actual != expected:
        raise RuntimeError(
            "Fish Audio alignment 与输入文本不完整匹配，为避免错误切分已停止"
            f"（alignment {len(actual)}/{len(expected)} 字符）"
        )

    entries: list[tuple[float, float, str]] = []
    event_index = 0
    event_char_offset = 0
    previous_end = 0.0
    for line in lines:
        target_length = len(_normalize_alignment_text(line))
        if target_length <= 0:
            continue
        consumed = 0
        line_start: float | None = None
        line_end: float | None = None
        while consumed < target_length and event_index < len(boundary_events):
            start, end, event_text = boundary_events[event_index]
            normalized_event = _normalize_alignment_text(event_text)
            remaining_event = len(normalized_event) - event_char_offset
            if remaining_event <= 0:
                event_index += 1
                event_char_offset = 0
                continue

            take = min(target_length - consumed, remaining_event)
            event_duration = max(0.0, end - start)
            event_length = max(1, len(normalized_event))
            piece_start = start + event_duration * event_char_offset / event_length
            piece_end = start + event_duration * (event_char_offset + take) / event_length
            if line_start is None:
                line_start = piece_start
            line_end = piece_end
            consumed += take
            event_char_offset += take
            if event_char_offset >= len(normalized_event):
                event_index += 1
                event_char_offset = 0

        if consumed != target_length or line_start is None or line_end is None:
            raise RuntimeError("Fish Audio alignment 无法完整映射到字幕行")
        if line_end <= line_start or line_start + 1e-6 < previous_end:
            raise RuntimeError("Fish Audio alignment 时间顺序异常")
        entries.append((timeline_offset + line_start, timeline_offset + line_end, line))
        previous_end = line_end
    if len(entries) != len(lines):
        raise RuntimeError(f"Fish Audio 字幕映射数量不一致: {len(entries)}/{len(lines)}")
    return entries


def format_srt_time(seconds: float) -> str:
    total_ms = max(0, int(round(float(seconds) * 1000)))
    hours = total_ms // 3_600_000
    minutes = (total_ms % 3_600_000) // 60_000
    secs = (total_ms % 60_000) // 1000
    millis = total_ms % 1000
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def write_srt(subtitle_file: Path | str, entries: list[tuple[float, float, str]]) -> None:
    path = Path(subtitle_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig") as f:
        for index, (start, end, text) in enumerate(entries, start=1):
            f.write(
                f"{index}\n{format_srt_time(start)} --> {format_srt_time(end)}\n{text.strip()}\n\n"
            )


def cut_audio_segments(
    audio_ranges: list[tuple[float, float, Path]],
    subtitle_entries: list[tuple[float, float, str]],
    output_dir: Path,
    silence_threshold_db: float = DEFAULT_SILENCE_THRESHOLD_DB,
    internal_silence_seconds: float = DEFAULT_INTERNAL_SILENCE_SECONDS,
    segment_gap_seconds: float = DEFAULT_SEGMENT_GAP_SECONDS,
) -> tuple[list[dict], list[tuple[float, float, str]]]:
    """按字幕区间切分独立 WAV，使用 ffmpeg 移除首尾静音并保留素材间隔。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    if not -80.0 <= float(silence_threshold_db) <= -20.0:
        raise ValueError("静音阈值必须在 -80 到 -20 dB 之间")
    if not 0.03 <= float(internal_silence_seconds) <= 2.0:
        raise ValueError("句内静音触发长度必须在 0.03 到 2.0 秒之间")
    if not 0.0 <= float(segment_gap_seconds) <= 10.0:
        raise ValueError("素材间隔必须在 0 到 10 秒之间")

    segments: list[dict] = []
    adjusted_entries: list[tuple[float, float, str]] = []
    timeline_cursor = 0.0

    for entry_index, (start, end, text) in enumerate(subtitle_entries, start=1):
        if end <= start:
            continue
        for range_start, range_end, source_file in audio_ranges:
            if start < range_start or start >= range_end:
                continue

            previous_end = (
                subtitle_entries[entry_index - 2][1]
                if entry_index > 1
                else range_start
            )
            next_start = (
                subtitle_entries[entry_index][0]
                if entry_index < len(subtitle_entries)
                else range_end
            )
            guarded_start = max(
                range_start,
                start - SEGMENT_HEAD_GUARD_SECONDS,
                (previous_end + start) / 2 if previous_end <= start else range_start,
            )
            guarded_end = min(
                range_end,
                end + SEGMENT_TAIL_GUARD_SECONDS,
                (end + next_start) / 2 if next_start >= end else range_end,
            )
            if guarded_end - guarded_start <= 0.005:
                continue

            local_start = guarded_start - range_start
            local_end = guarded_end - range_start
            output_file = output_dir / f"tts_segment_{entry_index:03d}.wav"
            exact_trim = (
                f"atrim=start={local_start:.6f}:end={local_end:.6f},"
                "asetpts=PTS-STARTPTS"
            )
            trim_internal = (
                "silenceremove=stop_periods=-1:"
                f"stop_duration={float(internal_silence_seconds):.3f}:"
                f"stop_threshold={float(silence_threshold_db):.1f}dB:"
                "stop_silence=0:detection=rms"
            )
            trim_one_edge = (
                "silenceremove=start_periods=1:start_duration=0.015:"
                f"start_threshold={float(silence_threshold_db):.1f}dB:"
                "start_silence=0.005:detection=rms"
            )
            silence_filter = (
                f"{exact_trim},{trim_internal},{trim_one_edge},"
                f"areverse,{trim_one_edge},areverse"
            )
            command = [
                "ffmpeg",
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(source_file),
                "-vn",
                "-af",
                silence_filter,
                "-c:a",
                "pcm_s16le",
                "-ar",
                "48000",
                "-ac",
                "1",
                str(output_file),
            ]
            try:
                subprocess.run(command, check=True, capture_output=True, text=True)
            except (OSError, subprocess.CalledProcessError) as error:
                detail = getattr(error, "stderr", "") or str(error)
                raise RuntimeError(f"切分音频失败：ffmpeg {detail.strip()}") from error

            if not output_file.is_file() or output_file.stat().st_size <= 0:
                raise RuntimeError(f"切分后未生成有效音频: {output_file}")
            trimmed_duration = get_audio_duration(output_file)
            if trimmed_duration <= 0.02:
                raise RuntimeError(f"静音裁剪后的音频过短: {output_file}")
            timeline_start = round(timeline_cursor, 3)
            timeline_end = round(timeline_start + trimmed_duration, 3)
            segments.append({
                "audio_file": str(output_file),
                "start_time": timeline_start,
            })
            adjusted_entries.append((timeline_start, timeline_end, text))
            timeline_cursor = timeline_end + float(segment_gap_seconds)
            break

    if not segments:
        raise RuntimeError("没有可用的字幕区间可以切分音频")
    if len(segments) != len(subtitle_entries):
        raise RuntimeError(
            f"音频切分数量与字幕数量不一致: {len(segments)}/{len(subtitle_entries)}"
        )
    return segments, adjusted_entries


def _safe_ssml_token(value: str) -> str:
    value = str(value or "").strip()
    return value if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", value) else ""


def build_azure_ssml(
    text: str,
    voice_name: str,
    rate: float,
    pitch_percent: int,
    volume_percent: int,
    style: str,
    role: str,
    style_degree: float,
) -> str:
    voice_name = voice_name.replace("-Female", "").replace("-Male", "").strip()
    locale_match = re.match(r"^([a-z]{2,3}-[A-Z]{2})-", voice_name)
    locale = locale_match.group(1) if locale_match else "zh-CN"
    rate_percent = round((rate - 1.0) * 100)
    rate_value = f"{rate_percent:+d}%"
    pitch_value = f"{int(pitch_percent):+d}Hz"
    volume_value = f"{int(volume_percent) - 100:+d}%"

    content = escape(text)
    safe_style = _safe_ssml_token(style)
    safe_role = _safe_ssml_token(role) if safe_style else ""
    if safe_style:
        attributes = [
            f"style={quoteattr(safe_style)}",
            f"styledegree={quoteattr(f'{style_degree:g}')}",
        ]
        if safe_role:
            attributes.append(f"role={quoteattr(safe_role)}")
        content = f"<mstts:express-as {' '.join(attributes)}>{content}</mstts:express-as>"

    return (
        "<speak version='1.0' "
        "xmlns='http://www.w3.org/2001/10/synthesis' "
        "xmlns:mstts='https://www.w3.org/2001/mstts' "
        f"xml:lang={quoteattr(locale)}>"
        f"<voice name={quoteattr(voice_name)}>"
        f"<prosody rate={quoteattr(rate_value)} pitch={quoteattr(pitch_value)} "
        f"volume={quoteattr(volume_value)}>{content}</prosody>"
        "</voice></speak>"
    )


def synthesize_azure_chunk(
    speechsdk: Any,
    text: str,
    output_file: Path,
    speech_key: str,
    speech_region: str,
    voice_name: str,
    rate: float,
    pitch_percent: int,
    volume_percent: int,
    style: str,
    role: str,
    style_degree: float,
    retries: int = 3,
) -> list[tuple[float, float, str]]:
    ssml = build_azure_ssml(
        text,
        voice_name,
        rate,
        pitch_percent,
        volume_percent,
        style,
        role,
        style_degree,
    )
    last_error = "Azure 未返回成功状态"
    for attempt in range(1, retries + 1):
        boundary_events: list[tuple[float, float, str]] = []
        speech_config = speechsdk.SpeechConfig(
            subscription=speech_key,
            region=speech_region,
        )
        speech_config.speech_synthesis_voice_name = (
            voice_name.replace("-Female", "").replace("-Male", "").strip()
        )
        speech_config.set_speech_synthesis_output_format(
            speechsdk.SpeechSynthesisOutputFormat.Riff48Khz16BitMonoPcm
        )
        speech_config.set_property(
            property_id=speechsdk.PropertyId.SpeechServiceResponse_RequestWordBoundary,
            value="true",
        )
        audio_config = speechsdk.audio.AudioOutputConfig(filename=str(output_file))
        synthesizer = speechsdk.SpeechSynthesizer(
            speech_config=speech_config,
            audio_config=audio_config,
        )

        def word_boundary_callback(evt: Any) -> None:
            start = float(evt.audio_offset) / 10_000_000
            end = start + _azure_duration_seconds(evt.duration)
            boundary_events.append((start, max(start, end), str(evt.text or "")))

        synthesizer.synthesis_word_boundary.connect(word_boundary_callback)
        result = synthesizer.speak_ssml_async(ssml).get()
        if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
            if output_file.is_file() and output_file.stat().st_size > 0:
                deadline = time.monotonic() + 0.5
                expected_text = _normalize_alignment_text(text)
                while time.monotonic() < deadline:
                    actual_text = _normalize_alignment_text(
                        "".join(event[2] for event in boundary_events)
                    )
                    if actual_text == expected_text:
                        break
                    time.sleep(0.02)
                if actual_text == expected_text:
                    return boundary_events
                last_error = (
                    "Azure 词边界不完整，为避免按估算时间切分后吞字，已拒绝本次结果"
                    f"（{len(actual_text)}/{len(expected_text)} 字符）"
                )
            else:
                last_error = "Azure 报告成功，但未生成有效音频文件"
        elif result.reason == speechsdk.ResultReason.Canceled:
            details = result.cancellation_details
            last_error = f"Azure 取消了合成: {details.reason}"
            error_details = getattr(details, "error_details", "")
            if error_details:
                last_error += f"; {error_details}"

        if attempt < retries:
            time.sleep(1)
    raise RuntimeError(last_error)


def _normalize_fish_api_url(api_url: str) -> str:
    normalized = str(api_url or DEFAULT_FISH_API_URL).strip().rstrip("/")
    if not normalized:
        return DEFAULT_FISH_API_URL
    if normalized.endswith("/stream/with-timestamp"):
        return normalized
    if normalized.endswith("/v1/tts"):
        return f"{normalized}/stream/with-timestamp"
    if normalized.endswith("/v1"):
        return f"{normalized}/tts/stream/with-timestamp"
    return f"{normalized}/v1/tts/stream/with-timestamp"


def _iter_fish_sse_events(response: Any):
    data_lines: list[str] = []

    def decode_event(lines: list[str]) -> Any:
        if not lines:
            return None
        last_error = None
        for candidate in ("\n".join(lines), "".join(lines)):
            if candidate.strip() == "[DONE]":
                return None
            try:
                return json.loads(candidate)
            except json.JSONDecodeError as error:
                last_error = error
        raise last_error

    for raw_line in response.iter_lines(decode_unicode=True):
        if isinstance(raw_line, bytes):
            raw_line = raw_line.decode("utf-8", errors="replace")
        line = str(raw_line or "").rstrip("\r")
        if not line:
            event = decode_event(data_lines)
            if event is not None:
                yield event
            data_lines = []
        elif line.startswith(":") or line.startswith("event:"):
            continue
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
        elif data_lines:
            data_lines.append(line)
    event = decode_event(data_lines)
    if event is not None:
        yield event


def _fish_alignment_events(alignment_by_chunk: dict[int, Any]) -> list[tuple[float, float, str]]:
    events: list[tuple[float, float, str]] = []
    for chunk_seq in sorted(alignment_by_chunk):
        snapshot = alignment_by_chunk[chunk_seq]
        try:
            offset = float(snapshot.get("offset", 0.0) or 0.0)
        except (TypeError, ValueError) as error:
            raise RuntimeError("Fish Audio alignment 的音频块偏移无效") from error
        alignment = snapshot.get("alignment") or {}
        chunk_event_start = len(events)
        pending_zero_duration_text = ""
        for segment in alignment.get("segments", []) or []:
            text = str(segment.get("text", "") or "").strip()
            if not text:
                continue
            try:
                start = offset + float(segment.get("start", 0.0) or 0.0)
                end = offset + float(segment.get("end", start) or start)
            except (TypeError, ValueError) as error:
                raise RuntimeError("Fish Audio alignment 时间戳无效") from error
            if start < 0 or end < start:
                raise RuntimeError(
                    f"Fish Audio alignment 包含反向时间区间: {text!r} ({start:g}-{end:g})"
                )
            if end == start:
                if _normalize_alignment_text(text):
                    pending_zero_duration_text += text
                continue
            events.append((start, end, pending_zero_duration_text + text))
            pending_zero_duration_text = ""
        if pending_zero_duration_text:
            if len(events) == chunk_event_start:
                raise RuntimeError("Fish Audio alignment 只有零时长正文，无法安全对齐")
            start, end, previous_text = events[-1]
            events[-1] = (start, end, previous_text + pending_zero_duration_text)

    previous_start = -1.0
    for start, _end, _text in events:
        if start + 1e-6 < previous_start:
            raise RuntimeError("Fish Audio alignment 时间戳顺序异常")
        previous_start = start
    return events


def synthesize_fish_chunk(
    text: str,
    output_file: Path,
    api_key: str,
    model: str,
    reference_id: str,
    api_url: str,
    speed: float,
    temperature: float,
    top_p: float,
    chunk_length: int,
    latency: str,
    timeout: int,
    proxies: dict | None = None,
    retries: int = 3,
) -> list[tuple[float, float, str]]:
    try:
        import requests
    except ImportError as error:
        raise RuntimeError("未安装 requests，无法调用 Fish Audio") from error

    payload: dict[str, Any] = {
        "text": str(text or "").strip(),
        "temperature": min(1.0, max(0.0, float(temperature))),
        "top_p": min(1.0, max(0.0, float(top_p))),
        "prosody": {
            "speed": min(4.0, max(0.25, float(speed))),
            "normalize_loudness": True,
        },
        "chunk_length": min(300, max(100, int(chunk_length))),
        "normalize": True,
        "format": "wav",
        "latency": latency if latency in {"low", "normal", "balanced"} else "normal",
    }
    if reference_id:
        payload["reference_id"] = reference_id

    expected_text = _normalize_alignment_text(text)
    last_error = "Fish Audio 未返回成功结果"
    for attempt in range(1, retries + 1):
        try:
            response = requests.post(
                _normalize_fish_api_url(api_url),
                json=payload,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "Accept": "text/event-stream",
                    "model": model,
                },
                proxies=proxies or None,
                timeout=max(30, int(timeout)),
                stream=True,
            )
            if response.status_code != 200:
                detail = str(getattr(response, "text", "") or "").strip()[:500]
                raise RuntimeError(f"Fish Audio API 错误: HTTP {response.status_code} {detail}")

            response.encoding = "utf-8"
            audio_chunks: list[bytes] = []
            alignment_by_chunk: dict[int, Any] = {}
            for event in _iter_fish_sse_events(response):
                if not isinstance(event, dict):
                    continue
                audio_base64 = event.get("audio_base64")
                if audio_base64:
                    audio_chunks.append(base64.b64decode(audio_base64, validate=True))
                if event.get("alignment") is not None:
                    try:
                        chunk_seq = int(event.get("chunk_seq", 0))
                    except (TypeError, ValueError):
                        chunk_seq = 0
                    alignment_by_chunk[chunk_seq] = {
                        "offset": event.get("chunk_audio_offset_sec", 0.0),
                        "alignment": event["alignment"],
                    }

            if not audio_chunks:
                raise RuntimeError("Fish Audio 返回中没有音频数据")
            boundary_events = _fish_alignment_events(alignment_by_chunk)
            actual_text = _normalize_alignment_text(
                "".join(event[2] for event in boundary_events)
            )
            if not boundary_events or actual_text != expected_text:
                raise RuntimeError(
                    "Fish Audio alignment 不完整，为避免按估算时间切分后吞字，已拒绝本次结果"
                    f"（{len(actual_text)}/{len(expected_text)} 字符）"
                )
            output_file.write_bytes(b"".join(audio_chunks))
            if output_file.stat().st_size <= 0:
                raise RuntimeError("Fish Audio 未生成有效 WAV 音频")
            audio_duration = get_audio_duration(output_file)
            if boundary_events[-1][1] > audio_duration + 0.25:
                raise RuntimeError("Fish Audio alignment 超出实际音频时长，已拒绝本次结果")
            return boundary_events
        except Exception as error:
            last_error = str(error)
            output_file.unlink(missing_ok=True)
            if attempt < retries:
                print(f"  Fish Audio 第 {attempt} 次结果不可用，2 秒后重试：{last_error}")
                time.sleep(2)
    raise RuntimeError(f"Fish Audio 已重试 {retries} 次仍失败：{last_error}")
