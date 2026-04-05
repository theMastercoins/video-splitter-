#!/usr/bin/env python3
"""
Split a video into WhatsApp-size MP4 parts using stream-copy only.
- Starts at 4 parts, bumps to 5 then 6 if any part risks hitting 180 MB
- Pre-flight risk check based on average bitrate estimate
- Post-flight actual-size verification; restarts with +1 part if needed
- Zero re-encoding: always uses -c copy for speed and quality
Uses ffmpeg and ffprobe on PATH (https://ffmpeg.org/download.html)
"""

import subprocess
import sys
from pathlib import Path

# ── Knobs ─────────────────────────────────────────────────────────────────────
MIN_PARTS      = 4      # best case
MAX_PARTS      = 6      # hard ceiling
RISK_THRESHOLD = 0.90   # if estimated part > 90 % of limit → add a part
# ─────────────────────────────────────────────────────────────────────────────


def get_duration_seconds(video_path: str) -> float:
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        video_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return float(result.stdout.strip())


def get_file_size_bytes(path) -> int:
    return Path(path).resolve().stat().st_size


def format_duration(seconds: float) -> str:
    s = int(round(seconds))
    return f"{s//3600:02d}:{(s%3600)//60:02d}:{s%60:02d}"


def pick_default_input() -> str:
    for name in ("video.mkv", "video.mp4"):
        if Path(name).resolve().is_file():
            return name
    return "video.mp4"


def copy_segment(input_path: Path, start: float, duration: float, out_file: Path) -> None:
    """Stream-copy a segment. No re-encoding ever."""
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-ss", str(start),
            "-i", str(input_path),
            "-t", str(duration),
            "-c", "copy",
            "-avoid_negative_ts", "1",
            str(out_file),
        ],
        check=True,
        capture_output=True,
    )


def choose_num_parts(
    total_bytes: int,
    total_duration: float,
    max_bytes: int,
    start_parts: int,
) -> int:
    """
    Pre-flight: estimate part sizes using average bitrate.
    Bump the part count if any estimated part exceeds RISK_THRESHOLD * max_bytes.
    Returns the safest part count within [start_parts, MAX_PARTS].
    """
    bps = total_bytes / total_duration  # average bytes per second
    risk_bytes = max_bytes * RISK_THRESHOLD

    for n in range(start_parts, MAX_PARTS + 1):
        seg_sec = total_duration / n
        est_bytes = bps * seg_sec
        if est_bytes <= risk_bytes:
            return n
        if n == MAX_PARTS:
            return MAX_PARTS  # best we can do

    return start_parts


def cleanup_parts(out_dir: Path, stem: str, count: int) -> None:
    """Remove temp part files from a failed attempt."""
    for i in range(1, count + 1):
        f = out_dir / f"{stem}_part{i:02d}.mp4"
        if f.exists():
            f.unlink()


def attempt_split(
    path: Path,
    num_parts: int,
    total_duration: float,
    total_bytes: int,
    max_bytes: int,
    out_dir: Path,
    stem: str,
) -> list[Path] | None:
    """
    Try to split into num_parts using stream-copy.
    Returns list of output Paths if ALL parts are within max_bytes,
    or None if any part exceeded the limit (caller should retry with more parts).
    """
    seg_duration = total_duration / num_parts
    bps          = total_bytes / total_duration
    print(f"\n  Trying {num_parts} parts  (~{format_duration(seg_duration)} each, "
          f"estimated {bps * seg_duration / 1_048_576:.1f} MB/part)")

    parts: list[Path] = []
    cursor = 0.0

    for i in range(1, num_parts + 1):
        remaining = total_duration - cursor
        this_dur  = min(seg_duration, remaining)
        out_file  = out_dir / f"{stem}_part{i:02d}.mp4"

        copy_segment(path, cursor, this_dur, out_file)

        actual_bytes = get_file_size_bytes(out_file)
        actual_mb    = actual_bytes / 1_048_576

        if actual_bytes > max_bytes:
            print(f"    Part {i}: {actual_mb:.2f} MB  OVER — aborting, will retry with more parts")
            # Clean up this attempt
            for p in parts:
                p.unlink(missing_ok=True)
            out_file.unlink(missing_ok=True)
            return None

        print(f"    Part {i}/{num_parts}: {out_file.name}  {actual_mb:.2f} MB  [copy]  OK")
        parts.append(out_file)
        cursor += this_dur

    return parts


def split_video(
    video_path: str,
    max_mb: float = 180.0,
    output_dir: str | None = None,
) -> list[str]:
    path = Path(video_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Video not found: {video_path}")

    out_dir = Path(output_dir or path.parent)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = path.stem

    total_duration = get_duration_seconds(str(path))
    total_bytes    = get_file_size_bytes(path)
    max_bytes      = int(max_mb * 1024 * 1024)

    print(f"\nSource   : {path.name}")
    print(f"Size     : {total_bytes / 1_048_576:.2f} MB  |  "
          f"Duration : {format_duration(total_duration)}")
    print(f"Limit    : {max_mb} MB")

    # ── Pre-flight: choose safest starting part count ─────────────────────────
    size_based = max(1, -(-total_bytes // max_bytes))   # ceiling division
    start      = max(MIN_PARTS, size_based)
    chosen     = choose_num_parts(total_bytes, total_duration, max_bytes, start)

    print(f"Strategy : start at {chosen} parts "
          f"(size needs {size_based}, min={MIN_PARTS}, risk-adjusted={chosen})")

    # ── Attempt splits, bumping parts on failure ──────────────────────────────
    result_parts: list[Path] | None = None

    for num_parts in range(chosen, MAX_PARTS + 1):
        result_parts = attempt_split(
            path, num_parts, total_duration, total_bytes,
            max_bytes, out_dir, stem,
        )
        if result_parts is not None:
            break
        if num_parts == MAX_PARTS:
            print(f"\nERROR: Could not split into {MAX_PARTS} parts within {max_mb} MB.")
            print("The video may have an extremely uneven bitrate distribution.")
            sys.exit(1)

    assert result_parts is not None

    # ── Rename with final part count ──────────────────────────────────────────
    total_parts = len(result_parts)
    final_paths: list[str] = []
    for i, p in enumerate(result_parts, 1):
        new_name = p.parent / f"{stem}_part{i:02d}_of{total_parts:02d}.mp4"
        p.rename(new_name)
        final_paths.append(str(new_name))

    # ── Final verification table ──────────────────────────────────────────────
    print(f"\n{'─'*58}")
    print(f"  {'File':<40} {'MB':>7}   Status")
    print(f"{'─'*58}")
    all_ok = True
    for fp in final_paths:
        sz = get_file_size_bytes(fp) / 1_048_576
        ok = sz <= max_mb
        if not ok:
            all_ok = False
        print(f"  {Path(fp).name:<40} {sz:>7.2f}   {'OK' if ok else 'OVER!'}")
    print(f"{'─'*58}")

    if all_ok:
        print(f"\nAll {total_parts} parts are within {max_mb} MB.\n")
    else:
        print(f"\nWARNING: Some parts still exceeded the limit.\n")

    return final_paths


def main():
    video      = sys.argv[1] if len(sys.argv) > 1 else pick_default_input()
    output_dir = sys.argv[2] if len(sys.argv) > 2 else None
    max_mb     = float(sys.argv[3]) if len(sys.argv) > 3 else 180.0

    path = Path(video).resolve()
    if not path.is_file():
        print(f"Video not found: {video}")
        print("Usage: python split_video.py <video_file> [output_directory] [max_mb]")
        sys.exit(1)

    split_video(str(path), max_mb=max_mb, output_dir=output_dir)


if __name__ == "__main__":
    main()
