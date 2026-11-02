"""Import approved audition inputs and match loudness without selecting a narrator."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import shutil
import subprocess
from pathlib import Path

import numpy as np
import soundfile as sf

PROJECT = Path(__file__).resolve().parent.parent
OUT = PROJECT / "auditions"
TEXT = "The first film began with a space between things. This one begins with something that survived: the knowledge of how we made it."


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(command: list[str]) -> str:
    result = subprocess.run(command, capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise RuntimeError(f"Audio conversion failed: {result.stderr[-3000:]}")
    return result.stderr


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("sources", type=Path)
    parser.add_argument("--pending-message", default="")
    args = parser.parse_args()
    request = json.loads(args.sources.read_text(encoding="utf-8-sig"))
    if request["text"] != TEXT:
        raise RuntimeError("Audition text differs from the unchanged opening excerpt.")
    narration = json.loads((PROJECT / "narration.json").read_text(encoding="utf-8"))
    if narration["beats"][0]["text"] != TEXT:
        raise RuntimeError("Film opening text changed; review before importing auditions.")
    runtime = json.loads((PROJECT / "runtime.json").read_text(encoding="utf-8"))
    ffmpeg = str(Path(runtime["ffmpegDirectory"]) / "ffmpeg.exe")
    (OUT / "raw").mkdir(parents=True, exist_ok=True)
    (OUT / "receipts").mkdir(exist_ok=True)
    samples = []
    seen = set()
    for item in request["samples"]:
        key = item["key"]
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,70}", key) or key in seen:
            raise RuntimeError("Invalid or duplicate audition key.")
        seen.add(key)
        source = Path(item["source_path"]).resolve(strict=True)
        audio, rate = sf.read(source, dtype="float64")
        if not np.isfinite(audio).all() or audio.size == 0 or np.max(np.abs(audio)) >= 1:
            raise RuntimeError(f"Invalid or clipped source: {key}")
        if not 3 <= len(audio) / rate <= 30:
            raise RuntimeError(f"Unexpected audition length: {key}")
        source_hash = sha(source)
        raw = OUT / "raw" / f"{key}.wav"
        target = OUT / f"{key}.wav"
        receipt_path = OUT / "receipts" / f"{key}.json"
        if raw.exists() and sha(raw) != source_hash:
            raise RuntimeError(f"Existing raw audition differs; preserve and review: {key}")
        if not raw.exists():
            shutil.copyfile(source, raw)
        if receipt_path.exists():
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            if receipt["raw_sha256"] != source_hash or not target.exists() or receipt["normalized_sha256"] != sha(target):
                raise RuntimeError(f"Existing normalized audition differs; preserve and review: {key}")
        else:
            if target.exists():
                raise RuntimeError(f"Output without receipt exists; preserve and review: {key}")
            log = run([ffmpeg, "-hide_banner", "-nostdin", "-i", str(raw), "-af", "loudnorm=I=-20:TP=-2:LRA=11:print_format=json", "-f", "null", "-"])
            measurements = json.loads(re.findall(r"\{[^{}]+\}", log)[-1])
            for field in ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset"):
                if not math.isfinite(float(measurements[field])):
                    raise RuntimeError(f"Nonfinite loudness measurement: {key}")
            filt = "loudnorm=I=-20:TP=-2:LRA=11:linear=true:" + ":".join(f"{dest}={measurements[src]}" for dest, src in [("measured_I", "input_i"), ("measured_TP", "input_tp"), ("measured_LRA", "input_lra"), ("measured_thresh", "input_thresh"), ("offset", "target_offset")])
            conversion_log = run([ffmpeg, "-hide_banner", "-nostdin", "-n", "-i", str(raw), "-af", filt + ":print_format=json", "-ar", "48000", "-ac", "1", "-c:a", "pcm_s24le", str(target)])
            output_measurement = json.loads(re.findall(r"\{[^{}]+\}", conversion_log)[-1])
            if abs(float(output_measurement["output_i"]) + 20) > 0.5:
                raise RuntimeError(f"Listening loudness target missed: {key}")
            playback, playback_rate = sf.read(target, dtype="float64")
            if playback_rate != 48000 or not np.isfinite(playback).all() or np.max(np.abs(playback)) >= 1:
                raise RuntimeError(f"Invalid playback WAV: {key}")
            receipt = {"raw_sha256": source_hash, "normalized_sha256": sha(target), "duration_seconds": len(playback) / playback_rate, "sample_rate": playback_rate, "peak": float(np.max(np.abs(playback))), "target_lufs": -20, "target_true_peak_db": -2, "source_measurement": measurements, "output_measurement": output_measurement, "text": TEXT, "timing_effects": "No time stretching, pitch change, reverb, music or narration edits."}
            receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
        samples.append({"key": key, "label": item["label"], "description": item["description"], "engine": item["engine"], "duration_seconds": receipt["duration_seconds"], "file": target.name})
    manifest = {"text": TEXT, "samples": samples, "pending_message": args.pending_message, "selection": "pending operator choice"}
    (OUT / "auditions.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"samples_ready": len(samples), "selection": "pending", "page": "http://127.0.0.1:4206/auditions/index.html"}))


if __name__ == "__main__":
    main()
