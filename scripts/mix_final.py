from __future__ import annotations

import argparse
import gc
import hashlib
import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

SR = 48_000
MAX_ACTIVE_SECONDS = 13.0
MAX_CLIP_SECONDS = 13.5
DUCK_DB = 5.0
TARGET_LUFS = -16.0
TARGET_TP_DBTP = -1.5
TARGET_LRA = 11.0


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def read_json(path: Path) -> dict[str, Any]:
    try:
        result = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read JSON file {path}: {exc}") from exc
    if not isinstance(result, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return result


def timecode(seconds: float, separator: str) -> str:
    milliseconds = max(0, int(round(seconds * 1000)))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d}{separator}{milliseconds:03d}"


def ass_time(seconds: float) -> str:
    centiseconds = max(0, int(round(seconds * 100)))
    hours, remainder = divmod(centiseconds, 360_000)
    minutes, remainder = divmod(remainder, 6_000)
    whole_seconds, centiseconds = divmod(remainder, 100)
    return f"{hours}:{minutes:02d}:{whole_seconds:02d}.{centiseconds:02d}"


def phrase_chunks(text: str, max_chars: int = 48) -> list[str]:
    chunks: list[str] = []
    current = ""
    for word in text.split():
        candidate = (current + " " + word).strip()
        if current and len(candidate) > max_chars:
            chunks.append(current)
            current = word
        else:
            current = candidate
        if re.search(r"[,;:.!?]$", word) and current:
            chunks.append(current)
            current = ""
    if current:
        chunks.append(current)
    return chunks


def resample_mono(samples: np.ndarray, source_sr: int) -> np.ndarray:
    if samples.ndim == 2:
        mono = np.mean(samples, axis=1, dtype=np.float32)
    elif samples.ndim == 1:
        mono = samples.astype(np.float32)
    else:
        raise ValueError("Expected mono or multichannel WAV data")
    if source_sr <= 0:
        raise ValueError("Invalid source sample rate")
    if source_sr == SR:
        return mono
    if len(mono) == 0:
        raise ValueError("Voice clip is empty")
    output_length = int(round(len(mono) * SR / source_sr))
    old_positions = np.arange(len(mono), dtype=np.float64)
    new_positions = np.arange(output_length, dtype=np.float64) * source_sr / SR
    return np.interp(new_positions, old_positions, mono).astype(np.float32)


def detect_activity(audio: np.ndarray) -> tuple[float, float, float]:
    hop = int(0.010 * SR)
    usable = (len(audio) // hop) * hop
    if usable == 0:
        raise ValueError("Voice clip is too short for activity detection")
    frames = audio[:usable].reshape(-1, hop)
    rms = np.sqrt(np.mean(frames * frames, axis=1))
    peak = float(np.max(np.abs(audio)))
    threshold = max(0.0008, peak * 0.025)
    active = np.flatnonzero(rms >= threshold)
    if active.size == 0:
        raise ValueError("No active speech detected in voice input")
    return round(active[0] * 0.010, 3), round((active[-1] + 1) * 0.010, 3), peak


def validate_voice_samples(
    samples: np.ndarray, source_sr: int, filename: str
) -> tuple[np.ndarray, dict[str, Any]]:
    if samples.size == 0:
        raise ValueError(f"Voice clip is empty: {filename}")
    if not np.isfinite(samples).all():
        raise ValueError(f"Voice clip contains non-finite samples: {filename}")
    mono = resample_mono(samples, source_sr)
    if mono.size == 0 or not np.isfinite(mono).all():
        raise ValueError(f"Voice clip is empty or invalid after resampling: {filename}")
    peak = float(np.max(np.abs(mono)))
    if peak >= 0.999:
        raise ValueError(f"Clipped source voice (peak >= 0.999): {filename}")
    onset, offset, _ = detect_activity(mono)
    active_span = offset - onset
    duration = len(mono) / SR
    if active_span > MAX_ACTIVE_SECONDS:
        raise ValueError(
            f"Beat voice span {active_span:.2f}s exceeds "
            f"{MAX_ACTIVE_SECONDS:.1f}s: {filename}"
        )
    if duration > MAX_CLIP_SECONDS:
        raise ValueError(
            f"Beat clip occupies {duration:.2f}s after its timeline start; "
            f"limit is {MAX_CLIP_SECONDS:.1f}s: {filename}"
        )
    return mono, {
        "source_sample_rate": int(source_sr),
        "source_channels": int(samples.shape[1]) if samples.ndim == 2 else 1,
        "duration_seconds": round(duration, 6),
        "active_start_seconds": onset,
        "active_end_seconds": offset,
        "spoken_duration_seconds": round(active_span, 6),
        "peak_dbfs": round(20 * math.log10(max(peak, 1e-12)), 3),
        "clipped_samples": 0,
    }


def read_voice_clip(path: Path, index: int) -> tuple[np.ndarray, dict[str, Any]]:
    try:
        samples, source_sr = sf.read(path, dtype="float32", always_2d=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ValueError(f"Malformed or unsupported voice WAV {path.name}: {exc}") from exc
    try:
        mono, metadata = validate_voice_samples(samples, int(source_sr), path.name)
    except ValueError as exc:
        raise ValueError(f"Beat {index:02d}: {exc}") from exc
    return mono, metadata


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def read_phrase_alignment(
    path: Path, beat_text: str, clip_meta: dict[str, Any], index: int
) -> list[dict[str, Any]]:
    data = read_json(path)
    if data.get("beat_index") not in (None, index):
        raise ValueError(f"Alignment beat_index does not match {path.name}")
    phrases = data.get("phrases")
    if not isinstance(phrases, list) or not phrases:
        raise ValueError(f"{path.name} must contain a non-empty phrases array")
    result: list[dict[str, Any]] = []
    for phrase_index, phrase in enumerate(phrases, 1):
        if not isinstance(phrase, dict) or not isinstance(phrase.get("text"), str):
            raise ValueError(f"{path.name} phrase {phrase_index} must include text")
        text = phrase["text"].strip()
        if not text:
            raise ValueError(f"{path.name} phrase {phrase_index} has empty text")
        start, end = phrase.get("start_seconds"), phrase.get("end_seconds")
        if isinstance(start, bool) or isinstance(end, bool):
            raise ValueError(f"{path.name} phrase {phrase_index} has invalid times")
        try:
            start, end = float(start), float(end)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{path.name} phrase {phrase_index} has invalid times") from exc
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start:
            raise ValueError(f"{path.name} phrase {phrase_index} has invalid time range")
        if start < clip_meta["active_start_seconds"] - 0.05:
            raise ValueError(f"{path.name} phrase {phrase_index} starts before detected speech")
        if end > clip_meta["active_end_seconds"] + 0.05:
            raise ValueError(f"{path.name} phrase {phrase_index} ends after detected speech")
        if result and start < result[-1]["end_seconds"]:
            raise ValueError(f"{path.name} phrase timings overlap or are out of order")
        result.append({"text": text, "start_seconds": start, "end_seconds": end})
    reconstructed = normalize_text(" ".join(item["text"] for item in result))
    if reconstructed != normalize_text(beat_text):
        raise ValueError(
            f"{path.name} phrase text must concatenate to the exact narration.json "
            "beat text, including punctuation"
        )
    return result


def write_captions(
    project: Path, beats: list[dict[str, Any]], clip_meta: list[dict[str, Any]]
) -> dict[str, Any]:
    cues: list[dict[str, Any]] = []
    aligned_count = estimated_count = 0
    sidecars: list[dict[str, Any]] = []
    meta_by_index = {item["index"]: item for item in clip_meta}
    for beat in beats:
        index = int(beat["index"])
        meta = meta_by_index[index]
        alignment = project / "voice_inputs" / f"beat_{index:02d}.alignment.json"
        if alignment.is_file():
            phrases = read_phrase_alignment(alignment, beat["text"], meta, index)
            sidecars.append(
                {
                    "beat": index,
                    "path": alignment.relative_to(project).as_posix(),
                    "sha256": sha256(alignment),
                }
            )
            for phrase in phrases:
                cues.append(
                    {
                        "start_seconds": round(float(beat["voice_start_seconds"]) + phrase["start_seconds"], 3),
                        "end_seconds": round(float(beat["voice_start_seconds"]) + phrase["end_seconds"], 3),
                        "text": phrase["text"],
                        "timing_method": "supplied_phrase_alignment",
                        "beat_index": index,
                    }
                )
                aligned_count += 1
            continue

        chunks = phrase_chunks(str(beat["text"]))
        if not chunks:
            continue
        weights = [max(1, len(re.sub(r"\W", "", chunk))) for chunk in chunks]
        total_weight = sum(weights) or 1
        absolute_start = float(beat["voice_start_seconds"]) + meta["active_start_seconds"]
        active_span = max(0.001, meta["active_end_seconds"] - meta["active_start_seconds"])
        cursor = absolute_start
        for chunk_index, (text, weight) in enumerate(zip(chunks, weights)):
            end = (
                absolute_start + active_span
                if chunk_index == len(chunks) - 1
                else cursor + active_span * weight / total_weight
            )
            cues.append(
                {
                    "start_seconds": round(cursor, 3),
                    "end_seconds": round(end, 3),
                    "text": text,
                    "timing_method": "estimated_character_weighted_phrase",
                    "beat_index": index,
                }
            )
            cursor = end
            estimated_count += 1

    vtt = ["WEBVTT", ""]
    srt: list[str] = []
    ass = [
        "[Script Info]", "Title: THE MEMORY BETWEEN", "ScriptType: v4.00+",
        "WrapStyle: 2", "ScaledBorderAndShadow: yes",
        "PlayResX: 1920", "PlayResY: 1080", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        "Style: Default,Arial,54,&H00FFFFFF,&H0000FFFF,&H80101010,&H70000000,0,0,0,0,100,100,0,0,1,2,1,2,100,100,70,1",
        "", "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for cue_index, cue in enumerate(cues, 1):
        start, end, text = cue["start_seconds"], cue["end_seconds"], cue["text"]
        vtt.extend([str(cue_index), f"{timecode(start, '.')} --> {timecode(end, '.')}", text, ""])
        srt.append(f"{cue_index}\n{timecode(start, ',')} --> {timecode(end, ',')}\n{text}\n")
        ass_text = text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")
        ass.append(f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Default,,0,0,0,,{ass_text}")
    (project / "captions.vtt").write_text("\n".join(vtt), encoding="utf-8")
    (project / "captions.srt").write_text("\n".join(srt), encoding="utf-8")
    (project / "captions.ass").write_text("\n".join(ass) + "\n", encoding="utf-8")
    return {
        "cue_count": len(cues),
        "supplied_phrase_alignment_cue_count": aligned_count,
        "estimated_phrase_cue_count": estimated_count,
        "alignment_sidecars": sidecars,
        "timing_note": (
            "Valid per-clip phrase alignment sidecars are used where present. "
            "Otherwise, phrase durations are estimated by non-punctuation "
            "character weight within the measured voice activity span. Estimated "
            "cues are phrase-level and do not imply word-level alignment."
        ),
    }


def resolve_executable(
    explicit: str | None, name: str, sibling_of: str | None = None
) -> str:
    if explicit:
        candidate = Path(explicit).expanduser()
        if candidate.is_file():
            return str(candidate.resolve())
        found = shutil.which(explicit)
        if found:
            return found
        raise FileNotFoundError(
            f"Cannot find requested executable {explicit!r}; provide a valid path "
            "or a command available on PATH"
        )
    found = shutil.which(name)
    if found:
        return found
    if sibling_of:
        sibling = Path(sibling_of).with_name(name + Path(sibling_of).suffix)
        if sibling.is_file():
            return str(sibling.resolve())
    raise FileNotFoundError(f"{name} was not supplied and was not found on PATH")


def ffmpeg_json(ffmpeg: str, command: list[str]) -> dict[str, Any]:
    process = subprocess.run(
        [ffmpeg, *command], capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    if process.returncode:
        raise RuntimeError("FFmpeg failed: " + process.stderr[-4000:])
    matches = re.findall(r"\{\s*\"input_i\".*?\}", process.stderr, re.S)
    if not matches:
        raise RuntimeError("FFmpeg loudnorm measurement missing")
    return json.loads(matches[-1])


def normalize(
    premix: Path, final: Path, ffmpeg: str, ffprobe: str, duration: int
) -> dict[str, Any]:
    base = ["-hide_banner", "-nostats", "-y", "-i", str(premix)]
    first = ffmpeg_json(
        ffmpeg,
        base + [
            "-af",
            f"loudnorm=I={TARGET_LUFS}:TP={TARGET_TP_DBTP}:LRA={TARGET_LRA}:print_format=json",
            "-f", "null", "-",
        ],
    )
    filter_arg = (
        f"loudnorm=I={TARGET_LUFS}:TP={TARGET_TP_DBTP}:LRA={TARGET_LRA}:"
        f"measured_I={first['input_i']}:measured_TP={first['input_tp']}:"
        f"measured_LRA={first['input_lra']}:measured_thresh={first['input_thresh']}:"
        f"offset={first['target_offset']}:linear=true:print_format=json"
    )
    second = ffmpeg_json(
        ffmpeg,
        base + [
            "-af", filter_arg, "-ar", str(SR), "-ac", "2", "-t", str(duration),
            "-c:a", "pcm_s24le", str(final),
        ],
    )
    measured = ffmpeg_json(
        ffmpeg,
        [
            "-hide_banner", "-nostats", "-i", str(final), "-af",
            f"loudnorm=I={TARGET_LUFS}:TP={TARGET_TP_DBTP}:LRA={TARGET_LRA}:print_format=json",
            "-f", "null", "-",
        ],
    )
    probe = subprocess.run(
        [ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(final)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
    )
    probe_data = json.loads(probe.stdout)
    stream = next(item for item in probe_data["streams"] if item.get("codec_type") == "audio")
    frames = sf.info(final).frames
    if int(stream["sample_rate"]) != SR or int(stream["channels"]) != 2 or frames != SR * duration:
        raise ValueError(
            f"Final mix format mismatch: sample_rate={stream.get('sample_rate')}, "
            f"channels={stream.get('channels')}, frames={frames}"
        )
    if abs(float(measured["input_i"]) - TARGET_LUFS) > 0.6:
        raise ValueError("Final integrated loudness is outside tolerance")
    if float(measured["input_tp"]) > TARGET_TP_DBTP + 0.15:
        raise ValueError("Final true peak exceeds tolerance")
    version = subprocess.run(
        [ffmpeg, "-version"], capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=True,
    ).stdout.splitlines()[0]
    return {
        "ffmpeg_version": version,
        "target": {
            "integrated_lufs": TARGET_LUFS, "true_peak_dbtp": TARGET_TP_DBTP,
            "lra_lu": TARGET_LRA,
        },
        "pass1": first, "pass2": second, "final_measurement": measured,
        "format": {
            "codec": stream.get("codec_name"), "sample_rate": SR, "channels": 2,
            "frames": frames, "duration_seconds": float(probe_data["format"]["duration"]),
        },
    }


def load_project(
    project: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    narration_path = project / "narration.json"
    script = read_json(narration_path)
    beats = script.get("beats")
    if not isinstance(beats, list) or len(beats) != 20:
        raise ValueError("narration.json must contain exactly 20 beats")
    if int(script.get("sample_rate", SR)) != SR:
        raise ValueError(f"narration.json sample_rate must be {SR}")
    if int(script.get("channels", 2)) != 2:
        raise ValueError("narration.json channels must be 2")
    duration = int(script.get("duration_seconds", 300))
    if duration <= 0 or float(script.get("duration_seconds", 300)) != duration:
        raise ValueError("duration_seconds must be a positive whole number")
    validated: list[dict[str, Any]] = []
    for expected, beat in enumerate(beats, 1):
        if not isinstance(beat, dict) or int(beat.get("index", -1)) != expected:
            raise ValueError(f"Expected narration beat index {expected}")
        if not isinstance(beat.get("text"), str) or not beat["text"].strip():
            raise ValueError(f"Beat {expected} is missing exact caption text")
        for name in ("voice_start_seconds", "chapter_start_seconds", "chapter_end_seconds"):
            try:
                value = float(beat[name])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"Beat {expected} has invalid {name}") from exc
            if not math.isfinite(value):
                raise ValueError(f"Beat {expected} has invalid {name}")
        if not 0 <= float(beat["voice_start_seconds"]) < duration:
            raise ValueError(f"Beat {expected} starts outside the timeline")
        validated.append(beat)

    manifest_path = project / "voice_inputs" / "voice_manifest.json"
    manifest = read_json(manifest_path)
    if not manifest.get("engine") or not manifest.get("voice"):
        raise ValueError("voice_manifest.json must identify engine and voice")
    try:
        gain = float(manifest.get("mix_gain_db", 0.0))
    except (TypeError, ValueError) as exc:
        raise ValueError("voice manifest mix_gain_db must be a number") from exc
    if not math.isfinite(gain):
        raise ValueError("voice manifest mix_gain_db must be finite")
    manifest["mix_gain_db"] = gain
    for beat in validated:
        clip = project / "voice_inputs" / f"beat_{int(beat['index']):02d}.wav"
        if not clip.is_file():
            raise FileNotFoundError(f"Missing selected voice clip {clip.relative_to(project).as_posix()}")
    return script, validated, manifest


def mix_project(project: Path, ffmpeg: str, ffprobe: str) -> dict[str, Any]:
    script, beats, voice_manifest = load_project(project)
    duration = int(script.get("duration_seconds", 300))
    total_frames = SR * duration
    audio_dir = project / "assets" / "audio"
    evidence_dir = project / "evidence"
    voice_clip_dir = audio_dir / "voice_clips"
    audio_dir.mkdir(parents=True, exist_ok=True)
    evidence_dir.mkdir(parents=True, exist_ok=True)
    voice_clip_dir.mkdir(parents=True, exist_ok=True)

    score_path, sfx_path = audio_dir / "score.wav", audio_dir / "sfx.wav"
    for stem in (score_path, sfx_path):
        if not stem.is_file():
            raise FileNotFoundError(f"Missing audio stem {stem.relative_to(project).as_posix()}")

    narration = np.zeros((total_frames, 2), dtype=np.float32)
    clip_meta: list[dict[str, Any]] = []
    for beat in beats:
        index = int(beat["index"])
        source_path = project / "voice_inputs" / f"beat_{index:02d}.wav"
        clip, source_meta = read_voice_clip(source_path, index)
        voice_start = float(beat["voice_start_seconds"])
        start = int(round(voice_start * SR))
        end = start + len(clip)
        if end > total_frames:
            raise ValueError(f"Beat {index} extends beyond the narration timeline")
        narration[start:end, 0] += clip
        narration[start:end, 1] += clip
        normalized_path = voice_clip_dir / f"beat_{index:02d}.wav"
        sf.write(normalized_path, clip, SR, subtype="PCM_24")
        clip_meta.append(
            {
                "index": index,
                "source_file": f"voice_inputs/{source_path.name}",
                "source_sha256": sha256(source_path),
                "normalized_file": f"assets/audio/voice_clips/{normalized_path.name}",
                "normalized_sha256": sha256(normalized_path),
                **source_meta,
                "voice_start_seconds": voice_start,
                "absolute_active_start_seconds": round(
                    voice_start + source_meta["active_start_seconds"], 6
                ),
                "absolute_active_end_seconds": round(
                    voice_start + source_meta["active_end_seconds"], 6
                ),
                "sample_rate": SR, "channels": 1,
            }
        )
        print(
            f"VOICE {index:02d}/20 {source_meta['spoken_duration_seconds']:.2f}s "
            f"active, {source_meta['duration_seconds']:.2f}s file"
        )

    narration_path = audio_dir / "narration.wav"
    sf.write(narration_path, narration, SR, subtype="PCM_24")
    captions = write_captions(project, beats, clip_meta)

    try:
        score, score_sr = sf.read(score_path, dtype="float32", always_2d=True)
        sfx, sfx_sr = sf.read(sfx_path, dtype="float32", always_2d=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ValueError(f"Cannot read score or SFX stem: {exc}") from exc
    if score_sr != SR or sfx_sr != SR or score.shape != (total_frames, 2) or sfx.shape != (total_frames, 2):
        raise ValueError(f"Score and SFX must each be {duration}s stereo at {SR} Hz")

    hop = int(0.05 * SR)
    usable = (total_frames // hop) * hop
    rms = np.sqrt(np.mean(narration[:usable].reshape(-1, hop, 2) ** 2, axis=(1, 2)))
    target = np.clip((rms - 0.0025) / 0.025, 0, 1)
    envelope = np.zeros_like(target)
    value = 0.0
    attack, release = math.exp(-0.05 / 0.12), math.exp(-0.05 / 0.80)
    for frame_index, item in enumerate(target):
        coefficient = attack if item > value else release
        value = item + coefficient * (value - item)
        envelope[frame_index] = value
    duck = np.repeat(10 ** ((-DUCK_DB * envelope) / 20), hop)[:total_frames].astype(np.float32)
    voice_gain = 10 ** (float(voice_manifest["mix_gain_db"]) / 20)
    mix = (score + sfx) * duck[:, None] + narration * np.float32(voice_gain)
    peak = float(np.max(np.abs(mix)))
    peak_guard_scale = min(1.0, 0.95 / max(peak, 1e-9))
    if peak_guard_scale < 1.0:
        mix *= np.float32(peak_guard_scale)
    premix_stats = {
        "peak_dbfs": round(20 * math.log10(max(float(np.max(np.abs(mix))), 1e-12)), 3),
        "rms_dbfs": round(
            20 * math.log10(max(float(np.sqrt(np.mean(mix.astype(np.float64) ** 2))), 1e-12)), 3
        ),
        "speech_gain_db": float(voice_manifest["mix_gain_db"]),
        "music_sfx_duck_db": DUCK_DB,
        "peak_guard_scale": peak_guard_scale,
    }

    final_path = audio_dir / "final_mix.wav"
    with tempfile.TemporaryDirectory(prefix="audio-mix-", dir=str(audio_dir)) as temp_dir:
        premix_path = Path(temp_dir) / "premix.wav"
        sf.write(premix_path, mix, SR, subtype="PCM_24")
        del score, sfx, mix, duck, envelope, target, rms, narration
        gc.collect()
        measurement = normalize(premix_path, final_path, ffmpeg, ffprobe, duration)

    final_info = sf.info(final_path)
    if final_info.frames < SR:
        raise ValueError("Final mix is shorter than one second")
    tail, _ = sf.read(
        final_path, start=final_info.frames - SR, dtype="float32", always_2d=True
    )
    tail_rms = float(np.sqrt(np.mean(tail.astype(np.float64) ** 2)))
    if tail_rms > 0.025:
        raise ValueError(f"End tail too loud: RMS {tail_rms:.6f}")

    outputs = [
        narration_path, final_path, project / "captions.vtt",
        project / "captions.srt", project / "captions.ass",
    ]
    outputs.extend(
        voice_clip_dir / f"beat_{int(beat['index']):02d}.wav" for beat in beats
    )
    qa = {
        "status": "passed",
        "title": script.get("title"),
        "duration_seconds": duration,
        "sample_rate": SR,
        "channels": 2,
        "bit_depth": 24,
        "source_script": {
            "path": "narration.json",
            "sha256": sha256(project / "narration.json"),
            "total_word_count": script.get("total_word_count"),
            "beat_count": len(beats),
            "text_preserved": True,
        },
        "voice_manifest": voice_manifest,
        "voice_clips": clip_meta,
        "caption_timing": captions,
        "mix": {
            "measurement": measurement,
            "premix": premix_stats,
            "final_tail_one_second_rms_dbfs": round(
                20 * math.log10(max(tail_rms, 1e-12)), 3
            ),
        },
        "inputs": {
            "score": {"path": "assets/audio/score.wav", "sha256": sha256(score_path)},
            "sfx": {"path": "assets/audio/sfx.wav", "sha256": sha256(sfx_path)},
        },
        "outputs": [
            {
                "path": path.relative_to(project).as_posix(),
                "sha256": sha256(path),
                "bytes": path.stat().st_size,
            }
            for path in outputs
        ],
    }
    write_json(evidence_dir / "AUDIO_QA.json", qa)
    print(
        f"COMPLETE {script.get('total_word_count', '?')} words; "
        f"{measurement['final_measurement']['input_i']} LUFS / "
        f"{measurement['final_measurement']['input_tp']} dBTP"
    )
    return qa


def resolve_executable(explicit: str | None, name: str, sibling_of: str | None = None) -> str:
    if explicit:
        candidate = Path(explicit).expanduser()
        if candidate.is_file():
            return str(candidate.resolve())
        found = shutil.which(explicit)
        if found:
            return found
        raise FileNotFoundError(f"Cannot find executable {explicit!r}")
    found = shutil.which(name)
    if found:
        return found
    if sibling_of:
        sibling = Path(sibling_of).with_name(name + Path(sibling_of).suffix)
        if sibling.is_file():
            return str(sibling.resolve())
    raise FileNotFoundError(f"{name} was not supplied and is not on PATH")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create narration, captions, and a loudness-checked project mix."
    )
    parser.add_argument("--project", required=True, type=Path, help="Project directory")
    parser.add_argument("--ffmpeg", help="Executable path/name; defaults to PATH lookup")
    parser.add_argument("--ffprobe", help="Executable path/name; defaults to PATH/sibling lookup")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    project = args.project.expanduser().resolve()
    if not project.is_dir():
        raise FileNotFoundError(f"Project directory does not exist: {project}")
    ffmpeg = resolve_executable(args.ffmpeg, "ffmpeg")
    ffprobe = resolve_executable(args.ffprobe, "ffprobe", sibling_of=ffmpeg)
    mix_project(project, ffmpeg, ffprobe)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (FileNotFoundError, RuntimeError, ValueError, KeyError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(2)
