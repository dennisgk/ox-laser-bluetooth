"""Data models and JSON persistence for TF1 editor intermediary format."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any, Dict, List, Literal, Union

FONT_TYPES = {"normal", "monospace", "bold"}


@dataclass
class PathPattern:
    type: Literal["path"] = "path"
    close: bool = False
    color: str = "#FFFFFF"
    points: List[List[float]] = field(default_factory=list)


@dataclass
class TextPattern:
    type: Literal["text"] = "text"
    text: str = "TEXT"
    x: float = 60.0
    y: float = 180.0
    size: float = 28.0
    color: str = "#FFFFFF"
    font: str = "normal"


Pattern = Union[PathPattern, TextPattern]


@dataclass
class Frame:
    time_ms: int = 100
    patterns: List[Pattern] = field(default_factory=list)


@dataclass
class Scene:
    name: str = "Scene"
    frames: List[Frame] = field(default_factory=lambda: [Frame()])


@dataclass
class Project:
    scenes: List[Scene] = field(default_factory=lambda: [Scene(name="Scene 1")])


def clamp_coord(v: Any) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(360.0, f))


def normalize_color(value: Any) -> str:
    if not isinstance(value, str):
        return "#FFFFFF"
    value = value.strip()
    if len(value) != 7 or not value.startswith("#"):
        return "#FFFFFF"
    hex_part = value[1:]
    if any(ch not in "0123456789abcdefABCDEF" for ch in hex_part):
        return "#FFFFFF"
    return "#" + hex_part.upper()


def normalize_font(value: Any) -> str:
    if isinstance(value, str) and value in FONT_TYPES:
        return value
    return "normal"


def pattern_from_dict(raw: Dict[str, Any]) -> Pattern:
    ptype = raw.get("type", "path")
    if ptype == "text":
        return TextPattern(
            type="text",
            text=str(raw.get("text", "TEXT")),
            x=clamp_coord(raw.get("x", 60)),
            y=clamp_coord(raw.get("y", 180)),
            size=max(8.0, min(120.0, float(raw.get("size", 28) or 28))),
            color=normalize_color(raw.get("color", "#FFFFFF")),
            font=normalize_font(raw.get("font", "normal")),
        )

    points: List[List[float]] = []
    for pair in raw.get("points", []):
        if isinstance(pair, list) and len(pair) >= 2:
            points.append([clamp_coord(pair[0]), clamp_coord(pair[1])])

    return PathPattern(
        type="path",
        close=bool(raw.get("close", False)),
        color=normalize_color(raw.get("color", "#FFFFFF")),
        points=points,
    )


def frame_from_dict(raw: Dict[str, Any]) -> Frame:
    patterns = []
    for item in raw.get("patterns", []):
        if isinstance(item, dict):
            patterns.append(pattern_from_dict(item))
    time_ms = raw.get("time_ms", 100)
    try:
        time_ms = int(time_ms)
    except (TypeError, ValueError):
        time_ms = 100
    time_ms = max(1, min(60000, time_ms))
    return Frame(time_ms=time_ms, patterns=patterns)


def scene_from_dict(raw: Dict[str, Any], idx: int) -> Scene:
    frames: List[Frame] = []
    for f in raw.get("frames", []):
        if isinstance(f, dict):
            frames.append(frame_from_dict(f))
    if not frames:
        frames = [Frame()]
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        name = f"Scene {idx + 1}"
    return Scene(name=name.strip(), frames=frames)


def project_from_dict(raw: Dict[str, Any]) -> Project:
    scenes: List[Scene] = []
    for idx, s in enumerate(raw.get("scenes", [])):
        if isinstance(s, dict):
            scenes.append(scene_from_dict(s, idx))
    if not scenes:
        scenes = [Scene(name="Scene 1")]
    return Project(scenes=scenes)


def pattern_to_dict(pattern: Pattern) -> Dict[str, Any]:
    if isinstance(pattern, TextPattern):
        return {
            "type": "text",
            "text": pattern.text,
            "x": round(pattern.x, 2),
            "y": round(pattern.y, 2),
            "size": round(pattern.size, 2),
            "color": normalize_color(pattern.color),
            "font": normalize_font(pattern.font),
        }

    return {
        "type": "path",
        "close": bool(pattern.close),
        "color": normalize_color(pattern.color),
        "points": [[round(p[0], 2), round(p[1], 2)] for p in pattern.points],
    }


def frame_to_dict(frame: Frame) -> Dict[str, Any]:
    return {
        "time_ms": int(frame.time_ms),
        "patterns": [pattern_to_dict(p) for p in frame.patterns],
    }


def scene_to_dict(scene: Scene) -> Dict[str, Any]:
    return {
        "name": scene.name,
        "frames": [frame_to_dict(f) for f in scene.frames],
    }


def project_to_dict(project: Project) -> Dict[str, Any]:
    return {"scenes": [scene_to_dict(s) for s in project.scenes]}


def load_project(path: str | Path) -> Project:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("JSON root must be an object")
    return project_from_dict(data)


def save_project(path: str | Path, project: Project) -> None:
    Path(path).write_text(
        json.dumps(project_to_dict(project), indent=2),
        encoding="utf-8",
    )


def new_project() -> Project:
    return Project(scenes=[Scene(name="Scene 1", frames=[Frame(time_ms=120, patterns=[])])])
