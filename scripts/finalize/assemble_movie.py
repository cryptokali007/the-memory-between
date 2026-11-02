from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import stat
import sys
import time
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path
from typing import Any

CORE_FRAMES = 17_520
CLOSING_FRAMES = 480
TOTAL_FRAMES = 18_000
FPS = 60
SAMPLE_RATE = 48_000
CHANNELS = 2
AUDIO_SAMPLES = 300 * SAMPLE_RATE
AAC_BITRATE = "320k"
VIDEO_MATCH_FIELDS = ("codec_name", "profile", "width", "height", "pix_fmt", "time_base", "level", "codec_tag_string")
SUCCESS = {"complete", "completed", "success", "succeeded"}


class AssembleError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _is_link_or_reparse(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & reparse_flag)


def resolve_input(raw: str, label: str, suffixes: set[str] | None = None) -> Path:
    path = Path(raw).expanduser()
    if _is_link_or_reparse(path):
        raise AssembleError(f"{label} must not be a symlink or reparse point: {path}")
    try:
        path = path.resolve(strict=True)
    except OSError as exc:
        raise AssembleError(f"Cannot resolve {label}: {exc}") from exc
    if not path.is_file():
        raise AssembleError(f"{label} is not a regular file: {path}")
    if suffixes and path.suffix.casefold() not in suffixes:
        raise AssembleError(f"{label} must use one of these extensions: {', '.join(sorted(suffixes))}")
    return path


def resolve_executable(raw: str, label: str) -> str:
    candidate = Path(raw).expanduser()
    if candidate.is_file():
        if _is_link_or_reparse(candidate):
            raise AssembleError(f"{label} executable may not be a symlink or reparse point: {candidate}")
        return str(candidate.resolve(strict=True))
    located = shutil.which(raw)
    if not located:
        raise AssembleError(f"Cannot find {label} executable: {raw}")
    path = Path(located)
    if _is_link_or_reparse(path):
        raise AssembleError(f"{label} executable may not be a symlink or reparse point: {path}")
    return str(path.resolve(strict=True))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def describe_file(path: Path) -> dict[str, Any]:
    return {"path": str(path), "bytes": path.stat().st_size, "sha256": sha256_file(path)}


def _run(argv: list[str], commands: list[dict[str, Any]], timeout: int, *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    started = time.monotonic()
    record: dict[str, Any] = {"argv": argv, "cwd": str(cwd) if cwd else None, "started_at": utc_now()}
    try:
        result = subprocess.run(
            argv, shell=False, cwd=str(cwd) if cwd else None, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
        )
        record.update({
            "returncode": result.returncode,
            "stdout_tail": result.stdout[-16_000:],
            "stderr_tail": result.stderr[-16_000:],
        })
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        record.update({"returncode": None, "timed_out": True, "stdout_tail": stdout[-16_000:], "stderr_tail": stderr[-16_000:]})
        record["elapsed_seconds"] = round(time.monotonic() - started, 3)
        record["completed_at"] = utc_now()
        commands.append(record)
        raise AssembleError(f"Command timed out after {timeout}s: {argv[0]}") from exc
    except OSError as exc:
        record.update({"returncode": None, "launch_error": str(exc), "stdout_tail": "", "stderr_tail": ""})
        record["elapsed_seconds"] = round(time.monotonic() - started, 3)
        record["completed_at"] = utc_now()
        commands.append(record)
        raise AssembleError(f"Cannot start {argv[0]}: {exc}") from exc
    record["elapsed_seconds"] = round(time.monotonic() - started, 3)
    record["completed_at"] = utc_now()
    commands.append(record)
    if result.returncode != 0:
        raise AssembleError(f"Command failed with exit {result.returncode}: {argv[0]} {argv[1] if len(argv) > 1 else ''}")
    return result


def _probe_json(ffprobe: str, path: Path, commands: list[dict[str, Any]], timeout: int, entries: str, *, select: str | None = None, count_frames: bool = False) -> dict[str, Any]:
    argv = [ffprobe, "-v", "error"]
    if select:
        argv += ["-select_streams", select]
    if count_frames:
        argv.append("-count_frames")
    argv += ["-show_entries", entries, "-of", "json", str(path)]
    result = _run(argv, commands, timeout)
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise AssembleError(f"ffprobe returned invalid JSON for {path}: {exc}") from exc
    return data


def probe_video(ffprobe: str, path: Path, commands: list[dict[str, Any]], timeout: int) -> dict[str, Any]:
    data = _probe_json(
        ffprobe, path, commands, timeout,
        "stream=codec_name,profile,width,height,pix_fmt,time_base,avg_frame_rate,r_frame_rate,level,codec_tag_string,nb_read_frames",
        select="v:0", count_frames=True,
    )
    streams = data.get("streams") or []
    if len(streams) != 1:
        raise AssembleError(f"Expected one video stream in {path}; found {len(streams)}")
    stream = streams[0]
    try:
        count = int(stream["nb_read_frames"])
    except (KeyError, TypeError, ValueError) as exc:
        raise AssembleError(f"ffprobe did not count decoded video frames for {path}") from exc
    stream["decoded_frame_count"] = count
    return stream


def parse_rate(value: str) -> Fraction:
    try:
        return Fraction(value)
    except (ValueError, ZeroDivisionError) as exc:
        raise AssembleError(f"Invalid frame rate: {value!r}") from exc


def validate_video_stream(stream: dict[str, Any], label: str, expected_frames: int | None = None) -> None:
    if int(stream.get("width") or 0) != 3840 or int(stream.get("height") or 0) != 2160:
        raise AssembleError(f"{label} must be 3840x2160; got {stream.get('width')}x{stream.get('height')}")
    if not stream.get("codec_name") or not stream.get("profile") or not stream.get("time_base"):
        raise AssembleError(f"{label} is missing codec, profile, or time-base evidence")
    for rate_field in ("avg_frame_rate", "r_frame_rate"):
        if parse_rate(str(stream.get(rate_field, "0/0"))) != FPS:
            raise AssembleError(f"{label} must be constant 60 fps by {rate_field}; got {stream.get(rate_field)}")
    if expected_frames is not None and stream.get("decoded_frame_count") != expected_frames:
        raise AssembleError(f"{label} decoded {stream.get('decoded_frame_count')} frames; expected {expected_frames}")


def validate_video_compatibility(core: dict[str, Any], closing: dict[str, Any]) -> None:
    differences = [field for field in VIDEO_MATCH_FIELDS if core.get(field) != closing.get(field)]
    for field in ("avg_frame_rate", "r_frame_rate"):
        if parse_rate(str(core.get(field, "0/0"))) != parse_rate(str(closing.get(field, "0/0"))):
            differences.append(field)
    if differences:
        raise AssembleError("Core and closing video streams are incompatible: " + ", ".join(sorted(set(differences))))


def probe_audio(ffprobe: str, path: Path, commands: list[dict[str, Any]], timeout: int) -> dict[str, Any]:
    data = _probe_json(
        ffprobe, path, commands, timeout,
        "stream=codec_name,sample_rate,channels,channel_layout,time_base,duration_ts,duration:format=duration",
        select="a:0",
    )
    streams = data.get("streams") or []
    if len(streams) != 1:
        raise AssembleError(f"Expected one audio stream in {path}; found {len(streams)}")
    stream = streams[0]
    stream["format_duration"] = (data.get("format") or {}).get("duration")
    try:
        timestamp_samples = int(stream["duration_ts"]) * Fraction(str(stream["time_base"])) * int(stream["sample_rate"])
        stream["sample_count_from_timestamps"] = f"{timestamp_samples.numerator}/{timestamp_samples.denominator}"
    except (KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
        raise AssembleError(f"Audio stream lacks exact duration/time-base sample evidence: {path}") from exc
    return stream


def decoded_audio_samples(ffprobe: str, path: Path, commands: list[dict[str, Any]], timeout: int) -> int:
    argv = [ffprobe, "-v", "error", "-select_streams", "a:0", "-show_frames", "-show_entries", "frame=nb_samples", "-of", "csv=p=0", str(path)]
    result = _run(argv, commands, timeout)
    count = 0
    frames = 0
    for line in result.stdout.splitlines():
        value = line.strip().split(",", 1)[0]
        if not value:
            continue
        try:
            count += int(value)
            frames += 1
        except ValueError as exc:
            raise AssembleError(f"Could not parse decoded audio sample count from ffprobe line {line!r}") from exc
    if frames == 0:
        raise AssembleError(f"ffprobe returned no decoded audio frames for {path}")
    return count


def validate_mix_audio(stream: dict[str, Any], decoded_samples: int) -> None:
    if int(stream.get("sample_rate") or 0) != SAMPLE_RATE or int(stream.get("channels") or 0) != CHANNELS:
        raise AssembleError(f"Mix WAV must be 48 kHz stereo; got {stream.get('sample_rate')} Hz / {stream.get('channels')} channels")
    expected = Fraction(AUDIO_SAMPLES, 1)
    if Fraction(str(stream["sample_count_from_timestamps"])) != expected or decoded_samples != AUDIO_SAMPLES:
        raise AssembleError(f"Mix WAV must be decoded to exactly {AUDIO_SAMPLES} samples per channel; timestamp={stream['sample_count_from_timestamps']} decoded={decoded_samples}")


def _concat_quote(path: Path) -> str:
    raw = str(path.resolve(strict=True)).replace("\\", "/")
    if "\n" in raw or "\r" in raw:
        raise AssembleError("Newlines in media paths are not supported by the concat manifest")
    return "file '" + raw.replace("'", "'\\''") + "'\n"


def write_concat_manifest(path: Path, core: Path, closing: Path) -> None:
    if path.exists():
        raise AssembleError(f"Refusing to overwrite concat manifest: {path}")
    text = "ffconcat version 1.0\n" + _concat_quote(core) + _concat_quote(closing)
    path.write_text(text, encoding="utf-8", newline="\n")


def build_master_command(ffmpeg: str, concat_file: Path, mix: Path, output: Path) -> list[str]:
    return [
        ffmpeg, "-nostdin", "-hide_banner", "-n", "-f", "concat", "-safe", "0", "-i", str(concat_file),
        "-i", str(mix), "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac",
        "-b:a", AAC_BITRATE, "-ar", str(SAMPLE_RATE), "-ac", str(CHANNELS), "-t", "300",
        "-movflags", "+faststart", str(output),
    ]


def _filter_escape(path: Path) -> str:
    value = str(path.resolve(strict=True)).replace("\\", "/")
    for char in ("\\", "'", ":", ",", ";", "[", "]"):
        value = value.replace(char, "\\" + char)
    return value


def build_video_filter(captions: Path | None = None, font_dir: Path | None = None) -> str:
    filters = ["scale=1920:1080:flags=lanczos", "fps=60"]
    if captions:
        escaped_captions = _filter_escape(captions)
        escaped_fonts = _filter_escape(font_dir) if font_dir else None
        filter_name = "ass" if captions.suffix.casefold() == ".ass" else "subtitles"
        expression = f"{filter_name}=filename={escaped_captions}"
        if escaped_fonts:
            expression += f":fontsdir={escaped_fonts}"
        filters.append(expression)
    return ",".join(filters)


def build_preview_command(ffmpeg: str, source: Path, output: Path, gpu_index: int,
                          captions: Path | None = None, font_dir: Path | None = None) -> list[str]:
    return [
        ffmpeg, "-nostdin", "-hide_banner", "-n", "-i", str(source), "-map", "0:v:0", "-map", "0:a:0",
        "-vf", build_video_filter(captions, font_dir), "-c:v", "h264_nvenc", "-gpu", str(gpu_index),
        "-preset", "p6", "-tune", "hq", "-rc", "vbr", "-cq", "18", "-b:v", "0", "-pix_fmt", "yuv420p",
        "-c:a", "copy", "-movflags", "+faststart", str(output),
    ]


def validate_preview(stream: dict[str, Any], expected_width: int = 1920, expected_height: int = 1080) -> None:
    if (int(stream.get("width") or 0), int(stream.get("height") or 0)) != (expected_width, expected_height):
        raise AssembleError(f"Preview must be {expected_width}x{expected_height}; got {stream.get('width')}x{stream.get('height')}")
    if parse_rate(str(stream.get("avg_frame_rate", "0/0"))) != FPS or parse_rate(str(stream.get("r_frame_rate", "0/0"))) != FPS:
        raise AssembleError("Preview must be 60 fps")
    if stream.get("decoded_frame_count") != TOTAL_FRAMES:
        raise AssembleError(f"Preview decoded {stream.get('decoded_frame_count')} frames; expected {TOTAL_FRAMES}")


def validate_audio_duration(stream: dict[str, Any], label: str, tolerance_seconds: float = 0.05) -> None:
    try:
        duration = Fraction(int(stream["duration_ts"])) * Fraction(str(stream["time_base"]))
    except (KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
        raise AssembleError(f"{label} lacks a stream duration/time-base measurement") from exc
    if abs(float(duration) - 300.0) > tolerance_seconds:
        raise AssembleError(f"{label} duration is {float(duration):.6f}s, outside 300s ± {tolerance_seconds:g}s")
    if int(stream.get("sample_rate") or 0) != SAMPLE_RATE or int(stream.get("channels") or 0) != CHANNELS:
        raise AssembleError(f"{label} audio must be 48 kHz stereo")


def _fully_decode(ffmpeg: str, path: Path, commands: list[dict[str, Any]], timeout: int, label: str, *, stream_map: str | None = None) -> None:
    argv = [ffmpeg, "-nostdin", "-hide_banner", "-v", "error", "-xerror", "-i", str(path)]
    if stream_map:
        argv += ["-map", stream_map]
    argv += ["-f", "null", "-"]
    _run(argv, commands, timeout)


def _probe_format_duration(ffprobe: str, path: Path, commands: list[dict[str, Any]], timeout: int) -> float:
    data = _probe_json(ffprobe, path, commands, timeout, "format=duration")
    try:
        return float(data["format"]["duration"])
    except (KeyError, TypeError, ValueError) as exc:
        raise AssembleError(f"ffprobe did not report duration for {path}") from exc


def _check_frame_total(core: dict[str, Any], closing: dict[str, Any]) -> None:
    if core["decoded_frame_count"] != CORE_FRAMES:
        raise AssembleError(f"Core video decoded {core['decoded_frame_count']} frames; expected {CORE_FRAMES}")
    if closing["decoded_frame_count"] != CLOSING_FRAMES:
        raise AssembleError(f"Closing video decoded {closing['decoded_frame_count']} frames; expected {CLOSING_FRAMES}")


def _safe_output_path(output: Path, filename: str) -> Path:
    path = output / filename
    if path.exists() or _is_link_or_reparse(path):
        raise AssembleError(f"Refusing to overwrite existing output: {path}")
    return path


def _write_receipt(path: Path, receipt: dict[str, Any]) -> None:
    if path.exists() or _is_link_or_reparse(path):
        raise AssembleError(f"Refusing to overwrite receipt: {path}")
    temporary = path.with_name(path.name + ".tmp")
    if temporary.exists():
        raise AssembleError(f"Refusing to overwrite receipt temporary: {temporary}")
    temporary.write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _output_inventory(output: Path) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for path in sorted(output.rglob("*")):
        if not path.is_file() or path.name == "ASSEMBLY_RECEIPT.json" or path.name.endswith(".tmp"):
            continue
        if _is_link_or_reparse(path):
            items.append({"path": str(path.relative_to(output).as_posix()), "error": "symlink/reparse output"})
        else:
            items.append({"path": str(path.relative_to(output).as_posix()), "bytes": path.stat().st_size, "sha256": sha256_file(path)})
    return items


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Assemble and verify the 300-second WALRUS film deliverables.")
    parser.add_argument("--core-video", required=True, help="17,520-frame 4K60 core video")
    parser.add_argument("--closing-video", required=True, help="480-frame 4K60 closing video")
    parser.add_argument("--mix", required=True, help="Exact 300-second 48 kHz stereo WAV mix")
    parser.add_argument("--captions", help="Optional SRT or ASS subtitles to burn into a separate viewing copy")
    parser.add_argument("--font-dir", help="Optional local font directory for subtitle rendering")
    parser.add_argument("--output", required=True, help="New output directory; existing directories are refused")
    parser.add_argument("--ffmpeg", required=True, help="Local ffmpeg executable")
    parser.add_argument("--ffprobe", required=True, help="Local ffprobe executable")
    parser.add_argument("--gpu-index", required=True, type=int, help="Explicit NVENC device index for 1080p copies")
    parser.add_argument("--master-only", action="store_true", help="Deliver the verified 4K master first; skip secondary viewing copies")
    parser.add_argument("--accepted-core-review", help="Reuse the independently accepted core decode/frame receipt only when its SHA256 matches this exact input")
    parser.add_argument("--command-timeout", type=int, default=10_800, help="Per-command timeout in seconds")
    return parser


def assemble(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    requested_out = Path(args.output).expanduser()
    if _is_link_or_reparse(requested_out):
        raise AssembleError("Output directory may not be a symlink or reparse point")
    output = requested_out.resolve(strict=False)
    if output.exists():
        raise AssembleError(f"Output directory already exists; refusing overwrite: {output}")
    output.mkdir(parents=True, exist_ok=False)
    receipt: dict[str, Any] = {
        "schema_version": 1,
        "status": "running",
        "started_at": utc_now(),
        "requirements": {"source_frames": {"core": CORE_FRAMES, "closing": CLOSING_FRAMES}, "total_frames": TOTAL_FRAMES, "fps": FPS, "audio_samples": AUDIO_SAMPLES, "sample_rate": SAMPLE_RATE, "channels": CHANNELS},
        "commands": [],
        "validations": {},
        "outputs": [],
        "limitations": ["No loudness value is asserted by this assembler; attach the supplied mix QA evidence if available."],
    }
    commands = receipt["commands"]
    try:
        if args.gpu_index < 0:
            raise AssembleError("GPU index must be zero or greater")
        if args.command_timeout < 1:
            raise AssembleError("Command timeout must be positive")
        core_path = resolve_input(args.core_video, "Core video", {".mp4", ".mov", ".mkv"})
        closing_path = resolve_input(args.closing_video, "Closing video", {".mp4", ".mov", ".mkv"})
        mix_path = resolve_input(args.mix, "Mix WAV", {".wav"})
        captions_path = resolve_input(args.captions, "Captions", {".srt", ".ass"}) if args.captions else None
        font_dir: Path | None = None
        if args.font_dir:
            font_dir = Path(args.font_dir).expanduser()
            if _is_link_or_reparse(font_dir):
                raise AssembleError("Font directory may not be a symlink or reparse point")
            font_dir = font_dir.resolve(strict=True)
            if not font_dir.is_dir():
                raise AssembleError("--font-dir must point to a local directory")
        ffmpeg = resolve_executable(args.ffmpeg, "ffmpeg")
        ffprobe = resolve_executable(args.ffprobe, "ffprobe")
        receipt["inputs"] = {"core_video": describe_file(core_path), "closing_video": describe_file(closing_path), "mix_wav": describe_file(mix_path)}
        if captions_path:
            receipt["inputs"]["captions"] = describe_file(captions_path)
        if font_dir:
            receipt["font_dir"] = str(font_dir)
        receipt["tools"] = {"ffmpeg": ffmpeg, "ffprobe": ffprobe}
        receipt["gpu_index"] = args.gpu_index

        if getattr(args, "accepted_core_review", None):
            review_path = resolve_input(args.accepted_core_review, "Accepted core review", {".json"})
            review = json.loads(review_path.read_text(encoding="utf-8"))
            render = review.get("remoteRender", {})
            current_hash = receipt["inputs"]["core_video"]["sha256"]
            if (review.get("status") != "pass" or current_hash != review.get("downloadedSha256")
                or current_hash != render.get("sha256") or render.get("frames") != CORE_FRAMES
                or render.get("duration") != 292 or render.get("width") != 3840
                or render.get("height") != 2160 or parse_rate(render.get("fps", "0/0")) != FPS):
                raise AssembleError("Accepted core review does not prove this exact 292-second source")
            header = _probe_json(ffprobe, core_path, commands, args.command_timeout,
                "stream=codec_name,profile,width,height,pix_fmt,time_base,avg_frame_rate,r_frame_rate,level,codec_tag_string", select="v:0")
            core = header["streams"][0]
            core["decoded_frame_count"] = CORE_FRAMES
            receipt["validations"]["accepted_core_reused"] = {"review": describe_file(review_path),
                "sha256_match": True, "note": "Prior independent full-decode/frame acceptance reused; final assembled movie is still fully decoded and counted below."}
        else:
            _fully_decode(ffmpeg, core_path, commands, args.command_timeout, "core source", stream_map="0:v:0")
            core = probe_video(ffprobe, core_path, commands, args.command_timeout)
        _fully_decode(ffmpeg, closing_path, commands, args.command_timeout, "closing source", stream_map="0:v:0")
        closing = probe_video(ffprobe, closing_path, commands, args.command_timeout)
        validate_video_stream(core, "Core source", CORE_FRAMES)
        validate_video_stream(closing, "Closing source", CLOSING_FRAMES)
        validate_video_compatibility(core, closing)
        _check_frame_total(core, closing)
        receipt["validations"]["core_video"] = core
        receipt["validations"]["closing_video"] = closing
        receipt["validations"]["source_compatibility"] = {"result": "pass", "matching_fields": list(VIDEO_MATCH_FIELDS) + ["avg_frame_rate", "r_frame_rate"]}

        _fully_decode(ffmpeg, mix_path, commands, args.command_timeout, "mix WAV", stream_map="0:a:0")
        audio_source = probe_audio(ffprobe, mix_path, commands, args.command_timeout)
        sample_count = decoded_audio_samples(ffprobe, mix_path, commands, args.command_timeout)
        validate_mix_audio(audio_source, sample_count)
        receipt["validations"]["mix_wav"] = {**audio_source, "decoded_sample_count": sample_count, "result": "pass"}

        concat_path = _safe_output_path(output, "concat-inputs.txt")
        write_concat_manifest(concat_path, core_path, closing_path)
        receipt["concat_manifest"] = {"path": concat_path.name, "sha256": sha256_file(concat_path)}
        master = _safe_output_path(output, "master-4k.mp4")
        _run(build_master_command(ffmpeg, concat_path, mix_path, master), commands, args.command_timeout)
        _fully_decode(ffmpeg, master, commands, args.command_timeout, "4K master")
        master_video = probe_video(ffprobe, master, commands, args.command_timeout)
        if master_video.get("decoded_frame_count") != TOTAL_FRAMES:
            raise AssembleError(f"4K master decoded {master_video.get('decoded_frame_count')} frames; expected {TOTAL_FRAMES}")
        for field in ("codec_name", "profile", "width", "height", "pix_fmt"):
            if master_video.get(field) != core.get(field):
                raise AssembleError(f"4K master {field} changed during stream-copy mux")
        if parse_rate(str(master_video.get("avg_frame_rate", "0/0"))) != FPS:
            raise AssembleError("4K master is not 60 fps")
        master_audio = probe_audio(ffprobe, master, commands, args.command_timeout)
        validate_audio_duration(master_audio, "4K master")
        receipt["validations"]["master_video"] = master_video
        receipt["validations"]["master_audio"] = master_audio
        receipt["validations"]["master_full_decode"] = "pass"

        if getattr(args, "master_only", False):
            receipt["outputs"] = _output_inventory(output)
            receipt.update({"status": "complete", "completed_at": utc_now(), "delivery_scope": "4K master only, per operator priority"})
            return 0, receipt

        preview = _safe_output_path(output, "viewing-1080p.mp4")
        _run(build_preview_command(ffmpeg, master, preview, args.gpu_index), commands, args.command_timeout)
        _fully_decode(ffmpeg, preview, commands, args.command_timeout, "1080p NVENC viewing copy")
        preview_video = probe_video(ffprobe, preview, commands, args.command_timeout)
        validate_preview(preview_video)
        preview_audio = probe_audio(ffprobe, preview, commands, args.command_timeout)
        validate_audio_duration(preview_audio, "1080p viewing copy")
        receipt["validations"]["preview_video"] = preview_video
        receipt["validations"]["preview_audio"] = preview_audio
        receipt["validations"]["preview_full_decode"] = "pass"

        if captions_path:
            captioned = _safe_output_path(output, "captioned-1080p.mp4")
            _run(build_preview_command(ffmpeg, master, captioned, args.gpu_index, captions_path, font_dir), commands, args.command_timeout)
            _fully_decode(ffmpeg, captioned, commands, args.command_timeout, "captioned 1080p NVENC copy")
            captioned_video = probe_video(ffprobe, captioned, commands, args.command_timeout)
            validate_preview(captioned_video)
            captioned_audio = probe_audio(ffprobe, captioned, commands, args.command_timeout)
            validate_audio_duration(captioned_audio, "captioned viewing copy")
            receipt["validations"]["captioned_video"] = captioned_video
            receipt["validations"]["captioned_audio"] = captioned_audio
            receipt["validations"]["captioned_full_decode"] = "pass"

        receipt["outputs"] = _output_inventory(output)
        receipt.update({"status": "complete", "completed_at": utc_now()})
        return 0, receipt
    except Exception as exc:
        receipt.update({"status": "failed", "error": str(exc), "completed_at": utc_now()})
        try:
            receipt["outputs"] = _output_inventory(output)
        except Exception as inventory_error:
            receipt["output_inventory_error"] = str(inventory_error)
        return 1, receipt


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    output = Path(args.output).expanduser()
    if output.exists() or _is_link_or_reparse(output):
        print(f"ERROR: output directory already exists or is unsafe; refusing overwrite: {output}", file=sys.stderr)
        return 2
    try:
        code, receipt = assemble(args)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    receipt_path = Path(args.output).expanduser().resolve(strict=True) / "ASSEMBLY_RECEIPT.json"
    try:
        _write_receipt(receipt_path, receipt)
    except Exception as exc:
        print(f"ERROR: assembly status={receipt.get('status')}; failed to write receipt: {exc}", file=sys.stderr)
        return 1
    if code == 0:
        print(f"Complete: verified master and viewing outputs. Receipt: {receipt_path}")
    else:
        print(f"Failed: {receipt.get('error')}. Receipt: {receipt_path}", file=sys.stderr)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
