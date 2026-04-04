#!/usr/bin/env python3
"""
Analyze a video and split it into WhatsApp-size MP4 parts.
Uses ffmpeg and ffprobe on PATH (https://ffmpeg.org/download.html)
"""

import subprocess
import sys
from pathlib import Path


def get_duration_seconds(video_path: str) -> float:
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        video_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return float(result.stdout.strip())


def get_file_size_bytes(video_path: str) -> int:
    return Path(video_path).resolve().stat().st_size


def format_duration(seconds: float) -> str:
    total_seconds = int(round(seconds))
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    secs = total_seconds % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def pick_default_input() -> str:
    for name in ("video.mkv", "video.mp4"):
        if Path(name).resolve().is_file():
            return name
    return "video.mp4"


def run_segment(input_path: Path, start: float, duration: float, out_file: Path) -> str:
    base_cmd = [
        "ffmpeg",
        "-y",
        "-ss", str(start),
        "-i", str(input_path),
        "-t", str(duration),
    ]
    copy_cmd = base_cmd + [
        "-c", "copy",
        "-avoid_negative_ts", "1",
        str(out_file),
    ]
    try:
        subprocess.run(copy_cmd, check=True, capture_output=True)
        return "copy"
    except subprocess.CalledProcessError:
        reencode_cmd = base_cmd + [
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "23",
            "-c:a", "aac",
            "-b:a", "128k",
            "-movflags", "+faststart",
            str(out_file),
        ]
        subprocess.run(reencode_cmd, check=True, capture_output=True)
        return "reencode"


def split_by_max_size(video_path: str, max_mb: float, output_dir: str | None = None) -> list[str]:
    path = Path(video_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Video not found: {video_path}")

    out_dir = Path(output_dir or path.parent)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = path.stem

    duration = get_duration_seconds(str(path))
    if duration <= 0:
        raise ValueError("Invalid video duration.")
    size_bytes = get_file_size_bytes(str(path))
    max_bytes = int(max_mb * 1024 * 1024)
    part_count = max(1, (size_bytes + max_bytes - 1) // max_bytes)
    step = duration / part_count

    out_paths = []
    for i in range(1, part_count + 1):
        start = step * (i - 1)
        end = duration if i == part_count else step * i
        out_name = f"{stem}_part{i:02d}_of{part_count:02d}.mp4"
        out_file = out_dir / out_name
        method = run_segment(path, start, end - start, out_file)
        out_paths.append(str(out_file))
        print(f"  Part {i}/{part_count}: {out_file.name} ({method})")

    return out_paths


def main():
    video = sys.argv[1] if len(sys.argv) > 1 else pick_default_input()
    output_dir = sys.argv[2] if len(sys.argv) > 2 else None
    max_mb = float(sys.argv[3]) if len(sys.argv) > 3 else 180.0

    path = Path(video).resolve()
    if not path.is_file():
        print(f"Video not found: {video}")
        print("Usage: python split_video.py <video_file> [output_directory] [max_mb]")
        sys.exit(1)

    duration = get_duration_seconds(str(path))
    size_bytes = get_file_size_bytes(str(path))
    max_bytes = int(max_mb * 1024 * 1024)
    part_count = max(1, (size_bytes + max_bytes - 1) // max_bytes)
    avg_bps = size_bytes / duration if duration > 0 else 0
    est_part_seconds = duration / part_count if part_count > 0 else duration

    print(f"Target video: {path.name}")
    print(f"Duration: {format_duration(duration)}")
    print(f"Total size: {size_bytes / (1024 * 1024):.2f} MB")
    print(f"WhatsApp limit per part: {max_mb:.2f} MB")
    print(f"Estimated parts required: {part_count}")
    print(f"Estimated length per part: {format_duration(est_part_seconds)}")

    if part_count == 1:
        if path.suffix.lower() == ".mp4":
            print("No split required. Video is within WhatsApp size limit.")
            return
        out_dir = Path(output_dir or path.parent)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = out_dir / f"{path.stem}.mp4"
        print("No split required. Converting to MP4...")
        method = run_segment(path, 0, duration, out_file)
        print(f"Created: {out_file.name} ({method})")
        return

    print("Splitting...")
    paths = split_by_max_size(str(path), max_mb, output_dir)
    print(f"Done. Created {len(paths)} files.")


if __name__ == "__main__":
    main()
