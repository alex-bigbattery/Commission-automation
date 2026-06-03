"""Replace smart/curly quotes with straight quotes in frontend source files.

Smart quotes (U+201C/201D/2018/2019) are never required in JS/JSX source — when
used as attribute delimiters they break esbuild. Converting them to straight
quotes is harmless in display text too. Operates on raw bytes.
"""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "frontend" / "src"
REPLACEMENTS = {
    "“": '"', "”": '"',   # “ ”
    "‘": "'", "’": "'",   # ‘ ’
}

total_files = 0
total_subs = 0
for path in list(SRC.rglob("*.jsx")) + list(SRC.rglob("*.js")):
    text = path.read_text(encoding="utf-8")
    subs = sum(text.count(ch) for ch in REPLACEMENTS)
    if subs:
        for ch, repl in REPLACEMENTS.items():
            text = text.replace(ch, repl)
        path.write_text(text, encoding="utf-8")
        rel = path.relative_to(REPO)
        print(f"  fixed {subs:>3} smart quote(s) in {rel}")
        total_files += 1
        total_subs += subs

print(f"\nDone: {total_subs} smart quote(s) replaced across {total_files} file(s).")
