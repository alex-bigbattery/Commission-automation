"""
Transcribe comisiones.mp3 to English using faster-whisper.

Outputs:
  - data/output/comisiones_transcript.txt   (plain text with timestamps)
  - data/output/comisiones_transcript.srt   (SRT subtitle format)
  - data/output/comisiones_segments.json    (raw segments with start/end)
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import timedelta
from pathlib import Path

# Ensure ffmpeg is in PATH for this process
os.environ["PATH"] = (
    r"C:\Users\Bigbattery\AppData\Local\Microsoft\WinGet\Links;"
    + os.environ.get("PATH", "")
)

from faster_whisper import WhisperModel  # noqa: E402

BASE_DIR = Path(__file__).resolve().parent.parent
AUDIO_PATH = Path(r"C:\Users\Bigbattery\Downloads\comisiones.mp3")
OUTPUT_DIR = BASE_DIR / "data" / "output"
TRANSCRIPT_TXT = OUTPUT_DIR / "comisiones_transcript.txt"
TRANSCRIPT_SRT = OUTPUT_DIR / "comisiones_transcript.srt"
SEGMENTS_JSON = OUTPUT_DIR / "comisiones_segments.json"
PROGRESS_LOG = OUTPUT_DIR / "comisiones_progress.log"


def fmt_timestamp(seconds: float, *, srt: bool = False) -> str:
    td = timedelta(seconds=seconds)
    total_ms = int(td.total_seconds() * 1000)
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, ms = divmod(rem, 1000)
    sep = "," if srt else "."
    return f"{hours:02d}:{minutes:02d}:{secs:02d}{sep}{ms:03d}"


def log(msg: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with PROGRESS_LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    if PROGRESS_LOG.exists():
        PROGRESS_LOG.unlink()

    log(f"Source: {AUDIO_PATH}")
    log(f"  size: {AUDIO_PATH.stat().st_size:,} bytes")

    log("Loading whisper model 'small' (will download first time, ~500 MB)...")
    t0 = time.time()
    model = WhisperModel(
        "small",
        device="cpu",
        compute_type="int8",
        cpu_threads=4,
    )
    log(f"Model loaded in {time.time() - t0:.1f}s")

    log("Starting transcription (task=translate, source language auto, target English)")
    log("This will take 30-60 minutes for ~94 minutes of audio on CPU...")

    t0 = time.time()
    segments_iter, info = model.transcribe(
        str(AUDIO_PATH),
        task="translate",     # any language -> English
        beam_size=5,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500},
    )
    log(f"Detected language: {info.language!r} (probability {info.language_probability:.2f})")
    log(f"Audio duration: {info.duration:.1f}s ({info.duration / 60:.1f} min)")

    # Collect segments
    segments: list[dict] = []
    last_log_time = time.time()
    for i, seg in enumerate(segments_iter):
        segments.append({
            "id": seg.id,
            "start": seg.start,
            "end": seg.end,
            "text": seg.text.strip(),
        })
        # Log progress every 30s of wall time, or every 20 segments
        now = time.time()
        if now - last_log_time >= 30 or (i + 1) % 20 == 0:
            elapsed = now - t0
            audio_done = seg.end
            speed = audio_done / elapsed if elapsed > 0 else 0
            remaining_audio = info.duration - audio_done
            eta_min = (remaining_audio / speed) / 60 if speed > 0 else float("inf")
            log(
                f"  segment {i + 1}, audio @ {audio_done / 60:.1f}/{info.duration / 60:.1f} min "
                f"({audio_done / info.duration * 100:.1f}%), speed {speed:.2f}x, ETA {eta_min:.1f} min"
            )
            last_log_time = now

    log(f"Transcription complete in {(time.time() - t0) / 60:.1f} min — {len(segments)} segments")

    # Write outputs
    with TRANSCRIPT_TXT.open("w", encoding="utf-8") as f:
        f.write(f"# Transcript of {AUDIO_PATH.name}\n")
        f.write(f"# Source language: {info.language} (auto-detected, probability {info.language_probability:.2f})\n")
        f.write(f"# Translated to English\n")
        f.write(f"# Duration: {info.duration:.1f}s ({info.duration / 60:.1f} min)\n")
        f.write(f"# Segments: {len(segments)}\n\n")
        for s in segments:
            f.write(f"[{fmt_timestamp(s['start'])} -> {fmt_timestamp(s['end'])}] {s['text']}\n")

    with TRANSCRIPT_SRT.open("w", encoding="utf-8") as f:
        for i, s in enumerate(segments, start=1):
            f.write(f"{i}\n{fmt_timestamp(s['start'], srt=True)} --> {fmt_timestamp(s['end'], srt=True)}\n{s['text']}\n\n")

    with SEGMENTS_JSON.open("w", encoding="utf-8") as f:
        json.dump({
            "language": info.language,
            "language_probability": info.language_probability,
            "duration": info.duration,
            "segments": segments,
        }, f, indent=2)

    log(f"Wrote: {TRANSCRIPT_TXT}")
    log(f"Wrote: {TRANSCRIPT_SRT}")
    log(f"Wrote: {SEGMENTS_JSON}")
    log("DONE")


if __name__ == "__main__":
    main()
