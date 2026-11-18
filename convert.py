#!/usr/bin/env python3
"""CLI: python3 convert.py video.mov [--mode copy|fast|quality]"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Конвертер MOV → MP4")
    parser.add_argument("input", type=Path, help="Путь к .mov файлу")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Путь к выходному .mp4 (по умолчанию рядом с исходником)",
    )
    parser.add_argument(
        "-m",
        "--mode",
        choices=("copy", "fast", "quality"),
        default="copy",
        help="copy=без перекодирования, fast=VideoToolbox, quality=x264",
    )
    args = parser.parse_args()

    src = args.input.expanduser().resolve()
    if not src.is_file():
        print(f"Файл не найден: {src}", file=sys.stderr)
        return 1
    if src.suffix.lower() != ".mov":
        print("Ожидается файл .mov", file=sys.stderr)
        return 1

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        print("ffmpeg не найден. Установите: brew install ffmpeg", file=sys.stderr)
        return 1

    dst = args.output.expanduser().resolve() if args.output else src.with_suffix(".mp4")
    dst.parent.mkdir(parents=True, exist_ok=True)

    cmd = [ffmpeg, "-y", "-hide_banner", "-i", str(src)]
    if args.mode == "copy":
        cmd += ["-c", "copy", "-movflags", "+faststart", str(dst)]
    elif args.mode == "fast":
        cmd += [
            "-c:v", "h264_videotoolbox", "-b:v", "8M",
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart", str(dst),
        ]
    else:
        cmd += [
            "-c:v", "libx264", "-preset", "medium", "-crf", "20",
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart", str(dst),
        ]

    print(" ".join(cmd))
    result = subprocess.run(cmd)
    if result.returncode != 0 and args.mode == "copy":
        print("Копирование потоков не удалось — пробую VideoToolbox…")
        cmd = [
            ffmpeg, "-y", "-hide_banner", "-i", str(src),
            "-c:v", "h264_videotoolbox", "-b:v", "8M",
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart", str(dst),
        ]
        result = subprocess.run(cmd)

    if result.returncode == 0:
        print(f"Готово: {dst}")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
