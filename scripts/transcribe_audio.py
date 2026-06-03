"""
Local audio transcription with faster-whisper (offline, no API key).

Transcribes one or more audio files in their ORIGINAL language (auto-detected).
For each FILE it writes, next to the source:
  - <name>.txt        plain transcript
  - <name>.timed.txt  transcript with [mm:ss -> mm:ss] timestamps

Usage:
  python scripts/transcribe_audio.py FILE [FILE ...] [--model small] [--language es] [--translate]

Models (download once): tiny(75MB) base(140MB) small(480MB) medium(1.5GB).
'small' = good speed/accuracy on CPU; 'medium' = best accuracy, slower.
"""

from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

# Ensure the WinGet ffmpeg is on PATH for audio decoding in this process.
os.environ["PATH"] = (
    r"C:\Users\Bigbattery\AppData\Local\Microsoft\WinGet\Links;"
    + os.environ.get("PATH", "")
)


def fmt_ts(seconds: float) -> str:
    s = int(seconds or 0)
    return f"{s // 60:02d}:{s % 60:02d}"


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+", help="Audio files to transcribe.")
    ap.add_argument("--model", default="small",
                    help="tiny|base|small|medium|large-v3 (default small).")
    ap.add_argument("--language", default=None,
                    help="Force language (e.g. es, en). Default: auto-detect per file.")
    ap.add_argument("--translate", action="store_true",
                    help="Translate to English instead of verbatim transcription.")
    args = ap.parse_args()

    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise SystemExit("faster-whisper not installed. Run: python -m pip install faster-whisper")

    task = "translate" if args.translate else "transcribe"
    log(f"Loading model '{args.model}' (CPU, int8); first run downloads it...")
    t0 = time.time()
    model = WhisperModel(args.model, device="cpu", compute_type="int8", cpu_threads=4)
    log(f"Model ready in {time.time() - t0:.1f}s. Task = {task}.")

    summary: list[str] = []
    for raw in args.files:
        path = Path(raw)
        if not path.exists():
            log(f"!! NOT FOUND: {path}")
            continue

        log(f"==== {path.name} ====")
        t0 = time.time()
        segments_iter, info = model.transcribe(
            str(path),
            task=task,
            language=args.language,
            beam_size=5,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
        )
        log(f"  language={info.language} (p={info.language_probability:.2f}) "
            f"duration={fmt_ts(info.duration)}")

        plain: list[str] = []
        timed: list[str] = []
        for i, seg in enumerate(segments_iter):
            text = seg.text.strip()
            plain.append(text)
            timed.append(f"[{fmt_ts(seg.start)} -> {fmt_ts(seg.end)}] {text}")
            if (i + 1) % 15 == 0:
                pct = (seg.end / info.duration * 100) if info.duration else 0
                log(f"  ... {seg.end/60:.1f}/{info.duration/60:.1f} min ({pct:.0f}%)")

        full = " ".join(plain).strip()
        path.with_suffix(".txt").write_text(full + "\n", encoding="utf-8")
        path.with_suffix(".timed.txt").write_text("\n".join(timed) + "\n", encoding="utf-8")
        log(f"  done in {(time.time()-t0)/60:.1f} min -> {path.stem}.txt (+ .timed.txt)")
        summary.append(f"{path.name}: {info.language}, {fmt_ts(info.duration)}, {len(plain)} segments")

    log("ALL DONE")
    for s in summary:
        log("  " + s)


if __name__ == "__main__":
    main()
