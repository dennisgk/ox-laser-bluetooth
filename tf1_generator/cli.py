"""CLI for generating app-compatible TF1 payloads and optional ESP header output."""

from __future__ import annotations

# python -m tf1_generator.cli --input tf1_generator\examples\line_scene.json --output tf1_generator\examples\line_scene.tf1 --emit-header esp-proto\main\include\tf1_sample.h --show-frames
# cmd /c "set PATH=C:\Python313;%PATH% && call C:\Espressif\frameworks\esp-idf-v5.5.2\export.bat && cd /d C:\Users\Owner\Desktop\Projects\lasertest2\esp-proto && idf.py build flash"

import argparse
import json
from pathlib import Path

from .builder import (
    BuildOptions,
    build_chunk_frame,
    build_handshake_frame,
    build_tf1_payload,
    chunk_payload,
    encode_patterns,
    load_default_channels,
    scene_from_seq_entry,
    scene_from_simple_entry,
    simplify_scenes,
    write_header_file,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate TF1 payload from simple scene JSON or app .seq JSON")
    parser.add_argument("--input", "-i", type=Path, required=True, help="Input JSON (.seq JSON or simple scene JSON)")
    parser.add_argument("--output", "-o", type=Path, required=True, help="Output .tf1 payload path")
    parser.add_argument("--name", default="AUTO1", help="TF1 display name suffix")
    parser.add_argument("--weight", type=int, default=None, help="Override weight (default from input or 1)")
    parser.add_argument("--canvas-width", type=int, default=360)
    parser.add_argument("--canvas-height", type=int, default=360)
    parser.add_argument(
        "--device-config",
        type=Path,
        default=Path("WWW/static/DQF6_LS01_en.json"),
        help="Path to device channel config JSON",
    )
    parser.add_argument(
        "--emit-header",
        type=Path,
        default=Path("esp-proto/main/include/tf1_sample.h"),
        help="Write C header with sample_tf1_payload[]",
    )
    parser.add_argument(
        "--no-header",
        action="store_true",
        help="Do not write C header",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=500,
        help="Protocol chunk payload bytes for frame preview",
    )
    parser.add_argument("--show-frames", action="store_true", help="Print cmd17/cmd18 frames in hex")
    parser.add_argument(
        "--simplify-epsilon",
        type=float,
        default=0.0,
        help="Douglas-Peucker simplify tolerance in canvas units (0 disables)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.chunk_size <= 0:
        raise SystemExit("chunk-size must be > 0")

    data = json.loads(args.input.read_text(encoding="utf-8-sig"))

    default_channels = load_default_channels(args.device_config)
    scenes = []

    if isinstance(data, dict) and isinstance(data.get("sceneList"), list):
        for s in data["sceneList"]:
            scenes.append(scene_from_seq_entry(s, default_channels))
        weight = int(data.get("weight", 1))
        device_type = str(data.get("type", "DQF6_LS01"))
    elif isinstance(data, dict) and isinstance(data.get("scenes"), list):
        for s in data["scenes"]:
            scenes.append(scene_from_simple_entry(s, default_channels))
        weight = int(data.get("weight", 1))
        device_type = str(data.get("device_type", "DQF6_LS01"))
    else:
        raise SystemExit("Input JSON must contain either sceneList[] (.seq) or scenes[]")

    if not scenes:
        raise SystemExit("No scenes found in input")

    if args.simplify_epsilon < 0:
        raise SystemExit("simplify-epsilon must be >= 0")
    if args.simplify_epsilon > 0:
        scenes = simplify_scenes(scenes, args.simplify_epsilon)

    opts = BuildOptions(
        tf1_name=args.name,
        weight=(args.weight if args.weight is not None else weight),
        device_type=device_type,
        canvas_width=args.canvas_width,
        canvas_height=args.canvas_height,
    )

    pattern_hex_per_scene = [
        encode_patterns(s.patterns, opts.canvas_width, opts.canvas_height)
        for s in scenes
    ]
    raw_pattern_bytes = sum(len(hx) // 2 for hx in pattern_hex_per_scene)
    unique_pattern_hex = list(dict.fromkeys(pattern_hex_per_scene))
    unique_pattern_bytes = sum(len(hx) // 2 for hx in unique_pattern_hex)
    dedup_saved_bytes = raw_pattern_bytes - unique_pattern_bytes
    dedup_percent = (dedup_saved_bytes / raw_pattern_bytes * 100.0) if raw_pattern_bytes > 0 else 0.0
    reused_scene_count = len(pattern_hex_per_scene) - len(unique_pattern_hex)

    payload = build_tf1_payload(scenes, opts)
    args.output.write_bytes(payload)

    if not args.no_header:
        write_header_file(payload, args.emit_header)

    print(f"Wrote TF1 payload ({len(payload)} bytes): {args.output}")
    if not args.no_header:
        print(f"Updated ESP header: {args.emit_header}")
    print(
        "Pattern dedup: "
        f"{len(unique_pattern_hex)}/{len(pattern_hex_per_scene)} unique blobs, "
        f"saved {dedup_saved_bytes} bytes "
        f"({dedup_percent:.2f}%), reused-by-scenes={reused_scene_count}"
    )

    if args.show_frames:
        hs = build_handshake_frame(len(payload))
        print(f"handshake(cmd17): {hs.hex()}")
        for i, chunk in enumerate(chunk_payload(payload, args.chunk_size), start=1):
            frame = build_chunk_frame(i, chunk)
            print(f"chunk {i}(cmd18): {frame.hex()}")


if __name__ == "__main__":
    main()
