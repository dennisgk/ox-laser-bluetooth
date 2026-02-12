from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

FRAME_HEAD = 0xAA
FRAME_TAIL = 0x5A
CMD_TF1_HANDSHAKE = 17
CMD_TF1_CHUNK = 18


@dataclass
class Point:
    x: float
    y: float
    color: str = "#FFFFFF"
    start: bool = False


@dataclass
class Pattern:
    points: list[Point]
    close: bool = True


@dataclass
class Scene:
    time_ms: int
    play_mode: int
    patterns: list[Pattern]
    channel_values: list[int]


@dataclass
class BuildOptions:
    tf1_name: str = "AUTO1"
    weight: int = 1
    device_type: str = "DQF6_LS01"
    canvas_width: int = 360
    canvas_height: int = 360


def _int_to_le(value: int, size: int) -> list[int]:
    return [(value >> (8 * i)) & 0xFF for i in range(size)]


def _hex_to_bytes(hex_s: str) -> list[int]:
    hs = hex_s.strip().lower()
    if len(hs) % 2:
        hs = "0" + hs
    return [int(hs[i : i + 2], 16) for i in range(0, len(hs), 2)]


def _center_point(w: int, h: int) -> dict[str, float]:
    return {"x": w / 2.0, "y": h / 2.0}


def _point_angle(center: dict[str, float], p: Point) -> int:
    if center["x"] == p.x and center["y"] == p.y:
        return 0
    if center["x"] == p.x:
        return 256 if center["y"] > p.y else 768
    if center["y"] == p.y:
        return 0 if center["x"] < p.x else 512

    dx = abs(center["x"] - p.x)
    dy = abs(center["y"] - p.y)
    a = math.atan(dy / dx)
    if center["x"] < p.x and center["y"] > p.y:
        out = a / math.pi / 2 * 1024
    elif center["x"] > p.x and center["y"] > p.y:
        out = (math.pi - a) / math.pi / 2 * 1024
    elif center["x"] > p.x and center["y"] < p.y:
        out = (math.pi + a) / math.pi / 2 * 1024
    else:
        out = (2 * math.pi - a) / math.pi / 2 * 1024
    return round(out)


def _turn_bits(prev_p: Point, cur_p: Point, next_p: Point) -> int:
    a1 = math.atan2(cur_p.y - prev_p.y, prev_p.x - cur_p.x)
    a2 = math.atan2(cur_p.y - next_p.y, next_p.x - cur_p.x)
    d = abs(a2 - a1)
    if d > math.pi:
        d = 2 * math.pi - d
    return round(d / math.pi * 63) << 10


def _color_bits(color_hex: str) -> int:
    c = color_hex.strip()
    if not c.startswith("#") or len(c) != 7:
        c = "#FFFFFF"
    r = int(c[1:3], 16)
    g = int(c[3:5], 16)
    b = int(c[5:7], 16)
    threshold = max(r, g, b) / 2.0
    out = 0
    if b > threshold:
        out += 32768
    if g > threshold:
        out += 16384
    if r > threshold:
        out += 8192
    return out


def _encode_step(
    is_start: bool,
    prev_p: Point | None,
    cur_p: Point,
    next_p: Point | None,
    center: dict[str, float],
    canvas_size: int,
) -> str:
    first_word = 0
    if not is_start and prev_p is not None:
        first_word += _color_bits(prev_p.color)

    dx = abs(cur_p.x - center["x"]) / (canvas_size - 1) * 4095
    dy = abs(cur_p.y - center["y"]) / (canvas_size - 1) * 4095
    radius = round(math.sqrt(dx * dx + dy * dy) * 0.98)
    first_word += radius
    out = f"{first_word:04x}"[-2:] + f"{first_word:04x}"[-4:-2]

    second_word = 0
    if (not is_start) and prev_p is not None and next_p is not None and (not next_p.start):
        second_word += _turn_bits(prev_p, cur_p, next_p)
    second_word += _point_angle(center, cur_p)
    out += f"{second_word:04x}"[-2:] + f"{second_word:04x}"[-4:-2]
    return out


def _find_first_start(points: list[Point], idx: int) -> Point | None:
    for i in range(idx, -1, -1):
        if points[i].start:
            return points[i]
    return None


def encode_patterns(patterns: list[Pattern], canvas_w: int, canvas_h: int) -> str:
    center = _center_point(canvas_w, canvas_h)
    out = ""
    for pattern in patterns:
        pts = pattern.points
        for i, cur_p in enumerate(pts):
            prev_p = pts[i - 1] if i > 0 else None
            next_p = pts[i + 1] if i < len(pts) - 1 else None
            out += _encode_step(cur_p.start, prev_p, cur_p, next_p, center, canvas_w)
            if pattern.close and ((next_p is not None and next_p.start) or i == len(pts) - 1):
                start_point = _find_first_start(pts, i)
                if start_point is not None:
                    out += _encode_step(False, cur_p, start_point, next_p, center, canvas_w)
    return out


def _contains_cjk(value: str) -> bool:
    for ch in value:
        if "\u4e00" <= ch <= "\u9fff":
            return True
    return False


def build_tf1_payload(scenes: list[Scene], opts: BuildOptions) -> bytes:
    if not scenes:
        raise ValueError("At least one scene is required")

    payload: list[int] = [255] * 12
    dev = opts.device_type[:4]
    payload.extend(ord(c) for c in dev)
    payload.extend([255] * (16 - (12 + len(dev))))
    payload.extend([48, 0, 0, 0])

    unique_pattern_hex: list[str] = []
    scene_rows: list[tuple[str, Scene]] = []
    channel_count = 0
    for scene in scenes:
        pattern_hex = encode_patterns(scene.patterns, opts.canvas_width, opts.canvas_height)
        if pattern_hex not in unique_pattern_hex:
            unique_pattern_hex.append(pattern_hex)
        channel_count = len(scene.channel_values)
        scene_rows.append((pattern_hex, scene))

    index_table_start = 48 + 4 * len(unique_pattern_hex) + 4
    payload.extend(_int_to_le(index_table_start, 4))
    payload.extend([0, 0, channel_count + 3, 0])
    payload.extend(_int_to_le(len(scenes), 2))
    payload.extend(_int_to_le(len(unique_pattern_hex), 2))
    payload.extend(_int_to_le(opts.weight, 2))
    payload.extend([0] * 14)

    scene_block_end = index_table_start + (channel_count + 3) * len(scenes)
    payload.extend(_int_to_le(scene_block_end, 4))
    p_end = scene_block_end
    for pattern_hex in unique_pattern_hex:
        p_end += len(pattern_hex) // 2
        payload.extend(_int_to_le(p_end, 4))

    for i, (pattern_hex, scene) in enumerate(scene_rows):
        pattern_index = unique_pattern_hex.index(pattern_hex)
        duration_10ms = int(scene.time_ms // 10)
        play_mode = int(scene.play_mode)
        if i == len(scene_rows) - 1:
            play_mode = 0
        payload.extend(_int_to_le(duration_10ms, 2))
        payload.append(play_mode & 0xFF)
        for v in scene.channel_values:
            payload.append(max(0, min(255, int(v))) & 0xFF)
        # CH4/icon-tuan selects pattern index in app protocol.
        if channel_count > 3:
            payload[-channel_count + 3] = pattern_index & 0xFF

    for pattern_hex in unique_pattern_hex:
        payload.extend(_hex_to_bytes(pattern_hex))

    payload.append(0)
    payload.extend(ord(c) for c in "display:")
    name = "MyPro" if _contains_cjk(opts.tf1_name) else opts.tf1_name
    payload.extend(ord(c) for c in name)

    return bytes(payload)


def chunk_payload(payload: bytes, chunk_size: int) -> Iterable[bytes]:
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    for offset in range(0, len(payload), chunk_size):
        yield payload[offset : offset + chunk_size]


def build_handshake_frame(total_length: int, tag: bytes = b"TF1", file_type: int = 0) -> bytes:
    frame = bytearray(16)
    frame[0] = FRAME_HEAD
    frame[1] = CMD_TF1_HANDSHAKE
    frame[2] = 0
    frame[3] = FRAME_TAIL
    frame[4] = 16
    frame[5] = 0
    frame[6] = 0
    frame[7] = 0
    frame[8:11] = tag[:3]
    frame[11] = file_type & 0xFF
    frame[12:16] = total_length.to_bytes(4, "little")
    return bytes(frame)


def build_chunk_frame(sequence: int, chunk: bytes, tag: bytes = b"TF1", file_type: int = 0) -> bytes:
    frame = bytearray(12 + len(chunk))
    frame[0] = FRAME_HEAD
    frame[1] = CMD_TF1_CHUNK
    frame[2] = 0
    frame[3] = FRAME_TAIL
    frame_len = len(frame)
    frame[4] = frame_len & 0xFF
    frame[5] = (frame_len >> 8) & 0xFF
    frame[6] = sequence & 0xFF
    frame[7] = (sequence >> 8) & 0xFF
    frame[8:11] = tag[:3]
    frame[11] = file_type & 0xFF
    frame[12:] = chunk
    return bytes(frame)


def write_header_file(payload: bytes, header_path: Path) -> None:
    joined = ", ".join(str(b) for b in payload)
    text = (
        "#pragma once\n\n#include <stddef.h>\n\n"
        f"static const unsigned char sample_tf1_payload[] = {{ {joined} }};\n\n"
        f"static const size_t sample_tf1_payload_len = {len(payload)};\n"
    )
    header_path.write_text(text, encoding="utf-8")


def load_default_channels(device_json_path: Path) -> list[int]:
    data = json.loads(device_json_path.read_text(encoding="utf-8"))
    channels = data.get("channel_list", [])
    return [int(c.get("value", 0) or 0) for c in channels]


def scene_from_seq_entry(scene_entry: dict[str, Any], default_channels: list[int]) -> Scene:
    chan = list(default_channels)
    raw_ch = scene_entry.get("channelList", [])
    if raw_ch:
        chan = [int(c.get("value", 0) or 0) for c in raw_ch]

    patterns: list[Pattern] = []
    for pat in scene_entry.get("patternList", []):
        points = []
        for idx, p in enumerate(pat.get("points", [])):
            points.append(
                Point(
                    x=float(p.get("x", 0)),
                    y=float(p.get("y", 0)),
                    color=str(p.get("color", "#FFFFFF")),
                    start=bool(p.get("start", idx == 0)),
                )
            )
        if points:
            patterns.append(Pattern(points=points, close=bool(pat.get("close", True))))

    return Scene(
        time_ms=int(scene_entry.get("time", scene_entry.get("time_ms", 5000))),
        play_mode=int(scene_entry.get("playModeValue", scene_entry.get("play_mode", 0))),
        patterns=patterns,
        channel_values=chan,
    )


def scene_from_simple_entry(scene_entry: dict[str, Any], default_channels: list[int]) -> Scene:
    chan = list(default_channels)
    raw_channels = scene_entry.get("channels")
    if isinstance(raw_channels, list) and raw_channels:
        chan = [int(v) for v in raw_channels]
        if len(chan) < len(default_channels):
            chan.extend(default_channels[len(chan) :])

    patterns: list[Pattern] = []
    for pat in scene_entry.get("patterns", []):
        color = str(pat.get("color", "#FFFFFF"))
        points: list[Point] = []
        for idx, pt in enumerate(pat.get("points", [])):
            if isinstance(pt, (list, tuple)) and len(pt) >= 2:
                x, y = pt[0], pt[1]
                start = idx == 0
            elif isinstance(pt, dict):
                x, y = pt.get("x", 0), pt.get("y", 0)
                start = bool(pt.get("start", idx == 0))
                color = str(pt.get("color", color))
            else:
                continue
            points.append(Point(x=float(x), y=float(y), color=color, start=start))
        if points:
            patterns.append(Pattern(points=points, close=bool(pat.get("close", True))))

    return Scene(
        time_ms=int(scene_entry.get("time_ms", scene_entry.get("time", 5000))),
        play_mode=int(scene_entry.get("play_mode", scene_entry.get("playModeValue", 0))),
        patterns=patterns,
        channel_values=chan,
    )
