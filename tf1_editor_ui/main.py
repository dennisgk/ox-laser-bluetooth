
from __future__ import annotations

import math
import json
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Any, Dict, List, Optional, Tuple

from libs.models import (
    Frame,
    PathPattern,
    Project,
    Scene,
    TextPattern,
    clamp_coord,
    load_project,
    new_project,
    normalize_color,
    save_project,
)
from libs.text_vectorizer import text_to_paths


class TF1EditorApp:
    CANVAS_SIZE = 720
    WORLD_MAX = 360.0
    SCALE = CANVAS_SIZE / WORLD_MAX
    EXPORT_CHANNELS = [10, 40, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
    EXPORT_DEVICE_TYPE = "DQF6_LS01"

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("TF1 Vector Scene Editor")

        self.base_dir = Path(__file__).resolve().parent
        self.data_dir = self.base_dir / "data"
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.project: Project = new_project()
        self.current_scene = 0
        self.current_frame = 0
        self.selected_pattern: Optional[int] = None
        self.selected_point: Optional[int] = None
        self.drag_mode: Optional[str] = None
        self.current_file: Optional[Path] = None

        self.preview_running = False
        self.preview_after_id: Optional[str] = None
        self.preview_speed = 1.0
        self.preview_frame_idx = 0
        self.preview_start_scene: Optional[int] = None
        self.preview_start_frame: Optional[int] = None

        self.mode_var = tk.StringVar(value="select")
        self.close_var = tk.BooleanVar(value=False)
        self.color_var = tk.StringVar(value="#FFFFFF")
        self.frame_time_var = tk.StringVar(value="120")
        self.text_var = tk.StringVar(value="TEXT")
        self.text_font_var = tk.StringVar(value="normal")
        self.text_size_var = tk.StringVar(value="28")
        self.preview_multiplier_var = tk.StringVar(value="1x")
        self.status_var = tk.StringVar(value="Ready")

        self._build_ui()
        self._refresh_scene_list()

    def _build_ui(self) -> None:
        self.root.columnconfigure(1, weight=1)
        self.root.rowconfigure(1, weight=1)

        toolbar = ttk.Frame(self.root, padding=(8, 6))
        toolbar.grid(row=0, column=0, columnspan=2, sticky="ew")

        self.preview_btn = ttk.Button(toolbar, text="Preview", command=self.start_preview)
        self.preview_btn.pack(side="left")

        self.stop_btn = ttk.Button(toolbar, text="Stop", command=lambda: self.stop_preview(return_to_start=True), state="disabled")
        self.stop_btn.pack(side="left", padx=(6, 0))

        ttk.Label(toolbar, text="Speed").pack(side="left", padx=(12, 4))
        self.preview_entry = ttk.Entry(toolbar, textvariable=self.preview_multiplier_var, width=8)
        self.preview_entry.pack(side="left")

        self.import_btn = ttk.Button(toolbar, text="Import TXT", command=self.import_txt_scene)
        self.import_btn.pack(side="left", padx=(12, 0))

        self.export_btn = ttk.Button(toolbar, text="Export", command=self.export_runtime_json)
        self.export_btn.pack(side="left", padx=(6, 0))

        left_wrap = ttk.Frame(self.root, padding=8)
        left_wrap.grid(row=1, column=0, sticky="ns")
        left_wrap.rowconfigure(0, weight=1)
        left_wrap.columnconfigure(0, weight=1)

        self.left_canvas = tk.Canvas(left_wrap, width=290, highlightthickness=0)
        self.left_canvas.grid(row=0, column=0, sticky="ns")
        left_scroll = ttk.Scrollbar(left_wrap, orient="vertical", command=self.left_canvas.yview)
        left_scroll.grid(row=0, column=1, sticky="ns")
        self.left_canvas.configure(yscrollcommand=left_scroll.set)

        left = ttk.Frame(self.left_canvas)
        self.left_panel = left
        self.left_canvas_window = self.left_canvas.create_window((0, 0), window=left, anchor="nw")
        left.bind(
            "<Configure>",
            lambda _e: self.left_canvas.configure(scrollregion=self.left_canvas.bbox("all")),
        )
        self.left_canvas.bind(
            "<Configure>",
            lambda e: self.left_canvas.itemconfigure(self.left_canvas_window, width=e.width),
        )
        self.left_canvas.bind_all("<MouseWheel>", self._on_left_mousewheel)

        canvas_wrap = ttk.Frame(self.root, padding=8)
        canvas_wrap.grid(row=1, column=1, sticky="nsew")
        canvas_wrap.rowconfigure(0, weight=1)
        canvas_wrap.columnconfigure(0, weight=1)

        file_box = ttk.LabelFrame(left, text="Project", padding=6)
        file_box.pack(fill="x", pady=4)
        ttk.Button(file_box, text="New", command=self.new_project_cmd).pack(fill="x")
        ttk.Button(file_box, text="Open", command=self.open_project_cmd).pack(fill="x", pady=2)
        ttk.Button(file_box, text="Save", command=self.save_project_cmd).pack(fill="x")
        ttk.Button(file_box, text="Save As", command=self.save_as_project_cmd).pack(fill="x", pady=2)

        scene_box = ttk.LabelFrame(left, text="Scenes", padding=6)
        scene_box.pack(fill="x", pady=4)
        self.scene_list = tk.Listbox(scene_box, exportselection=False, height=6, width=28)
        self.scene_list.pack(fill="x")
        self.scene_list.bind("<<ListboxSelect>>", self.on_scene_select)
        ttk.Button(scene_box, text="Add Scene", command=self.add_scene).pack(fill="x", pady=2)
        ttk.Button(scene_box, text="Delete Scene", command=self.delete_scene).pack(fill="x")

        frame_box = ttk.LabelFrame(left, text="Frames", padding=6)
        frame_box.pack(fill="x", pady=4)
        frame_list_wrap = ttk.Frame(frame_box)
        frame_list_wrap.pack(fill="x")
        self.frame_list = tk.Listbox(frame_list_wrap, exportselection=False, height=6, width=28)
        self.frame_list.pack(side="left", fill="both", expand=True)
        self.frame_scroll = ttk.Scrollbar(frame_list_wrap, orient="vertical", command=self.frame_list.yview)
        self.frame_scroll.pack(side="right", fill="y")
        self.frame_list.configure(yscrollcommand=self.frame_scroll.set)
        self.frame_list.bind("<<ListboxSelect>>", self.on_frame_select)
        self.frame_list.bind("<ButtonRelease-1>", self.on_frame_click)
        self.frame_list.bind("<Up>", self.on_frame_up)
        self.frame_list.bind("<Down>", self.on_frame_down)
        ttk.Button(frame_box, text="Add Frame", command=self.add_frame).pack(fill="x", pady=2)
        ttk.Button(frame_box, text="Delete Frame", command=self.delete_frame).pack(fill="x")
        ttk.Entry(frame_box, textvariable=self.frame_time_var).pack(fill="x", pady=2)
        ttk.Button(frame_box, text="Apply Frame time_ms", command=self.apply_frame_time).pack(fill="x")

        pattern_box = ttk.LabelFrame(left, text="Patterns", padding=6)
        pattern_box.pack(fill="x", pady=4)
        self.pattern_list = tk.Listbox(pattern_box, exportselection=False, height=8, width=28)
        self.pattern_list.pack(fill="x")
        self.pattern_list.bind("<<ListboxSelect>>", self.on_pattern_select)
        ttk.Button(pattern_box, text="Add Trace", command=self.add_trace).pack(fill="x", pady=2)
        ttk.Button(pattern_box, text="Add Text", command=self.add_text).pack(fill="x")
        ttk.Button(pattern_box, text="Delete Selected Trace/Text", command=self.delete_selected_pattern).pack(fill="x", pady=2)
        ttk.Button(pattern_box, text="Delete Selected Point", command=self.delete_selected_point).pack(fill="x")

        mode_box = ttk.LabelFrame(left, text="Canvas Mode", padding=6)
        mode_box.pack(fill="x", pady=4)
        ttk.Radiobutton(mode_box, text="Select/Move", variable=self.mode_var, value="select").pack(anchor="w")
        ttk.Radiobutton(mode_box, text="Add Point", variable=self.mode_var, value="add_point").pack(anchor="w")

        props_box = ttk.LabelFrame(left, text="Selected Pattern", padding=6)
        props_box.pack(fill="x", pady=4)
        ttk.Label(props_box, text="Color (#RRGGBB)").pack(anchor="w")
        ttk.Entry(props_box, textvariable=self.color_var).pack(fill="x")
        ttk.Checkbutton(props_box, text="Close path", variable=self.close_var, command=self.apply_path_props).pack(anchor="w", pady=2)
        ttk.Button(props_box, text="Apply Path Props", command=self.apply_path_props).pack(fill="x")

        ttk.Label(props_box, text="Text").pack(anchor="w", pady=(6, 0))
        ttk.Entry(props_box, textvariable=self.text_var).pack(fill="x")
        ttk.Label(props_box, text="Text Font").pack(anchor="w")
        self.text_font_combo = ttk.Combobox(props_box, textvariable=self.text_font_var, values=["normal", "monospace", "bold"], state="readonly")
        self.text_font_combo.pack(fill="x")
        ttk.Label(props_box, text="Text Size").pack(anchor="w")
        ttk.Entry(props_box, textvariable=self.text_size_var).pack(fill="x")
        ttk.Button(props_box, text="Apply Text Props", command=self.apply_text_props).pack(fill="x", pady=2)

        self.status_label = ttk.Label(left, textvariable=self.status_var, wraplength=220)
        self.status_label.pack(fill="x", pady=4)

        self.canvas = tk.Canvas(
            canvas_wrap,
            width=self.CANVAS_SIZE,
            height=self.CANVAS_SIZE,
            bg="#101010",
            highlightthickness=1,
            highlightbackground="#3a3a3a",
        )
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.canvas.bind("<Button-1>", self.on_canvas_press)
        self.canvas.bind("<B1-Motion>", self.on_canvas_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_canvas_release)

    def _walk_widgets(self, parent: tk.Misc) -> List[tk.Misc]:
        widgets: List[tk.Misc] = []
        for child in parent.winfo_children():
            widgets.append(child)
            widgets.extend(self._walk_widgets(child))
        return widgets

    def _set_editing_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        for widget in self._walk_widgets(self.left_panel):
            try:
                widget.configure(state=state)
            except tk.TclError:
                pass

        if enabled:
            self.text_font_combo.configure(state="readonly")
            self.preview_btn.configure(state="normal")
            self.import_btn.configure(state="normal")
            self.export_btn.configure(state="normal")
            self.preview_entry.configure(state="normal")
            self.stop_btn.configure(state="disabled")
        else:
            self.preview_btn.configure(state="disabled")
            self.import_btn.configure(state="disabled")
            self.export_btn.configure(state="disabled")
            self.preview_entry.configure(state="disabled")
            self.stop_btn.configure(state="normal")

    def _on_left_mousewheel(self, event: tk.Event) -> None:
        if not self.left_canvas.winfo_exists():
            return
        self.left_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
    def world_to_canvas(self, x: float, y: float) -> Tuple[float, float]:
        return x * self.SCALE, y * self.SCALE

    def canvas_to_world(self, x: float, y: float) -> Tuple[float, float]:
        return clamp_coord(x / self.SCALE), clamp_coord(y / self.SCALE)

    def active_scene(self) -> Scene:
        return self.project.scenes[self.current_scene]

    def active_frame(self) -> Frame:
        return self.active_scene().frames[self.current_frame]

    def _parse_speed_multiplier(self) -> float:
        raw = self.preview_multiplier_var.get().strip().lower().replace("x", "")
        try:
            mult = float(raw)
        except ValueError:
            mult = 1.0
        mult = max(0.1, min(20.0, mult))
        self.preview_multiplier_var.set(f"{mult:g}x")
        return mult

    def start_preview(self) -> None:
        if self.preview_running:
            return
        self.preview_speed = self._parse_speed_multiplier()
        self.preview_running = True
        self.preview_start_scene = self.current_scene
        self.preview_start_frame = self.current_frame
        self.preview_frame_idx = self.current_frame
        self.selected_pattern = None
        self.selected_point = None
        self._set_editing_enabled(False)
        self.status_var.set(f"Preview playing at {self.preview_speed:g}x")
        self._preview_tick()

    def _preview_tick(self) -> None:
        if not self.preview_running:
            return
        scene = self.active_scene()
        if self.preview_frame_idx >= len(scene.frames):
            self.stop_preview(return_to_start=True)
            return

        self.current_frame = self.preview_frame_idx
        self.preview_frame_idx += 1
        self.frame_list.selection_clear(0, tk.END)
        self.frame_list.selection_set(self.current_frame)
        self.frame_list.activate(self.current_frame)
        self.frame_list.see(self.current_frame)
        self.frame_time_var.set(str(scene.frames[self.current_frame].time_ms))
        self.selected_pattern = None
        self.selected_point = None
        self._refresh_pattern_list()

        delay = max(1, int(scene.frames[self.current_frame].time_ms / self.preview_speed))
        self.preview_after_id = self.root.after(delay, self._preview_tick)

    def stop_preview(self, return_to_start: bool = True) -> None:
        if self.preview_after_id is not None:
            try:
                self.root.after_cancel(self.preview_after_id)
            except tk.TclError:
                pass
            self.preview_after_id = None

        was_running = self.preview_running
        self.preview_running = False
        self._set_editing_enabled(True)

        if return_to_start and self.preview_start_scene is not None and self.preview_start_frame is not None:
            self.current_scene = self.preview_start_scene
            self.current_frame = self.preview_start_frame
            self._refresh_scene_list()

        self.preview_start_scene = None
        self.preview_start_frame = None
        if was_running:
            self.status_var.set("Preview stopped")

    def new_project_cmd(self) -> None:
        self.project = new_project()
        self.current_scene = 0
        self.current_frame = 0
        self.selected_pattern = None
        self.selected_point = None
        self.current_file = None
        self.status_var.set("New project")
        self._refresh_scene_list()

    def open_project_cmd(self) -> None:
        path = filedialog.askopenfilename(
            title="Open TF1 JSON",
            initialdir=str(self.data_dir),
            filetypes=[("JSON", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            self.project = load_project(path)
        except Exception as exc:
            messagebox.showerror("Open failed", str(exc))
            return
        self.current_file = Path(path)
        self.current_scene = 0
        self.current_frame = 0
        self.selected_pattern = None
        self.selected_point = None
        self.status_var.set(f"Opened {self.current_file.name}")
        self._refresh_scene_list()

    def save_project_cmd(self) -> None:
        if self.current_file is None:
            self.save_as_project_cmd()
            return
        self._commit_ui_to_model()
        save_project(self.current_file, self.project)
        self.status_var.set(f"Saved {self.current_file.name}")

    def save_as_project_cmd(self) -> None:
        path = filedialog.asksaveasfilename(
            title="Save TF1 JSON",
            defaultextension=".json",
            initialdir=str(self.data_dir),
            filetypes=[("JSON", "*.json")],
        )
        if not path:
            return
        self.current_file = Path(path)
        self.save_project_cmd()

    def export_runtime_json(self) -> None:
        self._commit_ui_to_model()
        scene = self.active_scene()
        payload = {
            "weight": 1,
            "device_type": self.EXPORT_DEVICE_TYPE,
            "scenes": [self._export_frame(frame) for frame in scene.frames],
        }

        path = filedialog.asksaveasfilename(
            title="Export Runtime JSON",
            defaultextension=".json",
            initialdir=str(self.data_dir),
            filetypes=[("JSON", "*.json")],
        )
        if not path:
            return

        Path(path).write_text(json.dumps(payload, indent=2), encoding="utf-8")
        self.status_var.set(f"Exported runtime JSON: {Path(path).name}")

    def _export_frame(self, frame: Frame) -> Dict[str, Any]:
        return {
            "time_ms": int(frame.time_ms),
            "play_mode": 0,
            "channels": list(self.EXPORT_CHANNELS),
            "patterns": self._export_patterns(frame),
        }

    def _export_patterns(self, frame: Frame) -> List[Dict[str, Any]]:
        exported: List[Dict[str, Any]] = []
        for pattern in frame.patterns:
            if isinstance(pattern, PathPattern):
                points = [self._export_point(pt[0], pt[1]) for pt in pattern.points]
                if len(points) >= 2:
                    exported.append(
                        {
                            "close": bool(pattern.close),
                            "color": normalize_color(pattern.color),
                            "points": points,
                        }
                    )
                continue

            if isinstance(pattern, TextPattern):
                raw_strokes = [s for s in text_to_paths(pattern.text, pattern.x, pattern.y, pattern.size, pattern.font) if len(s) >= 2]
                merged_strokes = self._merge_connected_strokes(raw_strokes)
                for stroke in merged_strokes:
                    if len(stroke) < 2:
                        continue
                    exported.append(
                        {
                            "close": False,
                            "color": normalize_color(pattern.color),
                            "points": [self._export_point(p[0], p[1]) for p in stroke],
                        }
                    )

        return exported

    def _export_point(self, x: float, y: float) -> List[int]:
        return [int(round(clamp_coord(x))), int(round(clamp_coord(y)))]

    def _merge_connected_strokes(self, strokes: List[List[List[float]]]) -> List[List[List[float]]]:
        if not strokes:
            return []

        def key(p: List[float]) -> Tuple[int, int]:
            return (int(round(p[0] * 1000)), int(round(p[1] * 1000)))

        segments: List[Tuple[int, Tuple[int, int], Tuple[int, int], List[float], List[float]]] = []
        adjacency: Dict[Tuple[int, int], List[int]] = {}

        for idx, seg in enumerate(strokes):
            a = [float(seg[0][0]), float(seg[0][1])]
            b = [float(seg[-1][0]), float(seg[-1][1])]
            ka = key(a)
            kb = key(b)
            segments.append((idx, ka, kb, a, b))
            adjacency.setdefault(ka, []).append(idx)
            adjacency.setdefault(kb, []).append(idx)

        used: set[int] = set()
        merged: List[List[List[float]]] = []

        def follow_chain(start_node: Tuple[int, int], first_seg_idx: int) -> List[List[float]]:
            _, ka, kb, a, b = segments[first_seg_idx]
            used.add(first_seg_idx)

            if ka == start_node:
                chain = [a, b]
                current_node = kb
            else:
                chain = [b, a]
                current_node = ka

            while True:
                candidates = [sid for sid in adjacency.get(current_node, []) if sid not in used]
                if not candidates:
                    break
                sid = candidates[0]
                _, sa, sb, pa, pb = segments[sid]
                used.add(sid)
                if sa == current_node:
                    chain.append(pb)
                    current_node = sb
                else:
                    chain.append(pa)
                    current_node = sa
            return chain

        # Start with open-chain endpoints (degree != 2) so connected lines become single polylines.
        for node, seg_ids in adjacency.items():
            if len(seg_ids) == 2:
                continue
            for sid in seg_ids:
                if sid in used:
                    continue
                merged.append(follow_chain(node, sid))

        # Remaining unvisited edges are loops.
        for sid, ka_kb in enumerate(segments):
            if sid in used:
                continue
            _, ka, _, _, _ = ka_kb
            merged.append(follow_chain(ka, sid))

        return merged

    def import_txt_scene(self) -> None:
        path = filedialog.askopenfilename(
            title="Import TXT Timeline",
            initialdir=str(self.data_dir),
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if not path:
            return

        try:
            lines = Path(path).read_text(encoding="utf-8").splitlines()
            items = self._parse_import_lines(lines)
            settings = self._show_import_settings_dialog(Path(path).stem)
            if settings is None:
                return
            scene = self._build_scene_from_import(items, settings, Path(path).stem)
        except Exception as exc:
            messagebox.showerror("Import failed", str(exc))
            return

        self.project.scenes.append(scene)
        self.current_scene = len(self.project.scenes) - 1
        self.current_frame = 0
        self.selected_pattern = None
        self.selected_point = None
        self._refresh_scene_list()
        self.status_var.set(f"Imported scene from {Path(path).name}")

    def _parse_import_lines(self, lines: List[str]) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        i = 0
        while i < len(lines):
            if not lines[i].strip():
                i += 1
                continue

            line_no = i + 1
            offset_sec, style = self._parse_import_offset_line(lines[i], line_no)
            if i + 1 >= len(lines):
                raise ValueError(f"Missing text line after offset line {line_no}")
            text = lines[i + 1]

            items.append(
                {
                    "offset_sec": float(offset_sec),
                    "text": text,
                    "style": style,
                    "line_no": line_no,
                }
            )
            i += 3

        if not items:
            raise ValueError("No timeline records found")
        if abs(items[0]["offset_sec"]) > 1e-9:
            raise ValueError(f"First offset must be 0 (line {items[0]['line_no']})")
        return items

    def _parse_import_offset_line(self, raw_line: str, line_no: int) -> Tuple[float, Dict[str, Any]]:
        tokens = raw_line.split()
        if not tokens:
            raise ValueError(f"Empty offset line at {line_no}")

        try:
            offset_sec = float(tokens[0])
        except ValueError as exc:
            raise ValueError(f"Invalid offset seconds at line {line_no}: '{tokens[0]}'") from exc

        param_names = {"COLOR", "COLOR_SWITCH_WORD", "COLOR_SWITCH_LETTER", "BOLD", "UNDERLINE", "BOX"}
        style: Dict[str, Any] = {
            "base_color": "#FFFFFF",
            "color_switch_word": None,
            "color_switch_letter": None,
            "bold": False,
            "underline": None,
            "box": None,
        }

        idx = 1
        while idx < len(tokens):
            name = tokens[idx].upper()
            if name not in param_names:
                raise ValueError(f"Unknown parameter '{tokens[idx]}' at line {line_no}")
            idx += 1

            if name == "BOLD":
                style["bold"] = True
                continue

            if name == "COLOR":
                if idx >= len(tokens):
                    raise ValueError(f"COLOR requires a hex value at line {line_no}")
                color = tokens[idx].upper()
                if not self._is_valid_hex_color(color):
                    raise ValueError(f"Invalid COLOR '{tokens[idx]}' at line {line_no}")
                style["base_color"] = color
                idx += 1
                continue

            if name in {"COLOR_SWITCH_WORD", "COLOR_SWITCH_LETTER"}:
                colors: List[str] = []
                while idx < len(tokens) and tokens[idx].upper() not in param_names:
                    c = tokens[idx].upper()
                    if not self._is_valid_hex_color(c):
                        raise ValueError(f"Invalid color '{tokens[idx]}' for {name} at line {line_no}")
                    colors.append(c)
                    idx += 1
                if not colors:
                    raise ValueError(f"{name} requires one or more colors at line {line_no}")
                style["color_switch_word" if name == "COLOR_SWITCH_WORD" else "color_switch_letter"] = colors
                continue

            if name in {"UNDERLINE", "BOX"}:
                indices: List[int] = []
                colors: List[str] = []
                while idx < len(tokens) and tokens[idx].upper() not in param_names:
                    tok = tokens[idx]
                    if self._is_nonnegative_int(tok) and not colors:
                        indices.append(int(tok))
                    else:
                        c = tok.upper()
                        if not self._is_valid_hex_color(c):
                            raise ValueError(f"Invalid token '{tok}' for {name} at line {line_no}")
                        colors.append(c)
                    idx += 1

                if not colors:
                    colors = ["#FFFFFF"]
                target = {"indices": (indices if indices else None), "colors": colors}
                if target["indices"] is None and len(target["colors"]) > 1:
                    target["colors"] = [target["colors"][0]]
                style["underline" if name == "UNDERLINE" else "box"] = target
                continue

        if style["color_switch_word"] is not None and style["color_switch_letter"] is not None:
            raise ValueError(f"Use only one of COLOR_SWITCH_WORD or COLOR_SWITCH_LETTER at line {line_no}")

        return offset_sec, style

    def _is_valid_hex_color(self, token: str) -> bool:
        return len(token) == 7 and token.startswith("#") and all(ch in "0123456789ABCDEFabcdef" for ch in token[1:])

    def _is_nonnegative_int(self, token: str) -> bool:
        return token.isdigit()

    def _show_import_settings_dialog(self, source_name: str) -> Optional[Dict[str, Any]]:
        dialog = tk.Toplevel(self.root)
        dialog.title(f"Import Settings: {source_name}")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.resizable(False, False)

        offset_x_var = tk.StringVar(value="40")
        offset_y_var = tk.StringVar(value="120")
        center_var = tk.BooleanVar(value=False)
        size_var = tk.StringVar(value="28")

        body = ttk.Frame(dialog, padding=10)
        body.grid(row=0, column=0, sticky="nsew")

        ttk.Label(body, text="Offset X (0..360)").grid(row=0, column=0, sticky="w")
        ttk.Entry(body, textvariable=offset_x_var, width=16).grid(row=0, column=1, pady=3, sticky="ew")

        ttk.Label(body, text="Offset Y (0..360)").grid(row=1, column=0, sticky="w")
        ttk.Entry(body, textvariable=offset_y_var, width=16).grid(row=1, column=1, pady=3, sticky="ew")

        ttk.Checkbutton(body, text="Center text", variable=center_var).grid(row=2, column=0, columnspan=2, sticky="w", pady=3)

        ttk.Label(body, text="Text Size").grid(row=3, column=0, sticky="w")
        ttk.Entry(body, textvariable=size_var, width=16).grid(row=3, column=1, pady=3, sticky="ew")

        actions = ttk.Frame(body)
        actions.grid(row=4, column=0, columnspan=2, sticky="e", pady=(8, 0))

        result: Dict[str, Any] = {}

        def on_ok() -> None:
            try:
                ox = float(offset_x_var.get())
                oy = float(offset_y_var.get())
                size = max(8.0, min(120.0, float(size_var.get())))
            except ValueError:
                messagebox.showerror("Invalid settings", "Offset and size must be numbers.", parent=dialog)
                return

            result["offset_x"] = ox
            result["offset_y"] = oy
            result["center"] = bool(center_var.get())
            result["size"] = size
            dialog.destroy()

        def on_cancel() -> None:
            dialog.destroy()

        ttk.Button(actions, text="Cancel", command=on_cancel).pack(side="right")
        ttk.Button(actions, text="Import", command=on_ok).pack(side="right", padx=(0, 6))

        dialog.wait_window()
        if not result:
            return None
        return result

    def _build_scene_from_import(self, items: List[Dict[str, Any]], settings: Dict[str, Any], stem: str) -> Scene:
        frames: List[Frame] = []
        default_last = 1.0
        if len(items) > 1:
            default_last = max(0.001, items[-1]["offset_sec"] - items[-2]["offset_sec"])

        for idx, item in enumerate(items):
            offset_sec = item["offset_sec"]
            text = item["text"]
            style = item["style"]
            line_no = item["line_no"]
            font = "bold" if style["bold"] else "normal"

            if idx + 1 < len(items):
                duration_sec = max(0.001, items[idx + 1]["offset_sec"] - offset_sec)
            else:
                duration_sec = default_last

            x, y = self._import_text_position(
                text=text,
                font=font,
                size=settings["size"],
                center=settings["center"],
                offset_x=settings["offset_x"],
                offset_y=settings["offset_y"],
            )

            patterns = self._build_import_patterns(text, x, y, settings["size"], font, style, line_no)
            frame = Frame(
                time_ms=max(1, int(round(duration_sec * 1000.0))),
                patterns=patterns,
            )
            frames.append(frame)

        return Scene(name=f"Imported {stem}", frames=frames)

    def _build_import_patterns(
        self,
        text: str,
        x: float,
        y: float,
        size: float,
        font: str,
        style: Dict[str, Any],
        line_no: int,
    ) -> List[PathPattern]:
        patterns: List[PathPattern] = []
        scale = size / 7.0
        char_w = 5.0 * scale
        gap = scale * (1.8 if font in {"normal", "monospace"} else 1.4)
        step = char_w + gap

        if style["color_switch_letter"] is not None:
            colors = style["color_switch_letter"]
            letter_i = 0
            for i, ch in enumerate(text):
                if ch == " ":
                    continue
                color = colors[letter_i % len(colors)]
                letter_i += 1
                strokes = text_to_paths(ch, x + i * step, y, size, font)
                patterns.extend(self._strokes_to_path_patterns(strokes, color))
        elif style["color_switch_word"] is not None:
            colors = style["color_switch_word"]
            words = self._word_spans(text)
            for word_i, (start_idx, _end_idx, word) in enumerate(words):
                color = colors[word_i % len(colors)]
                strokes = text_to_paths(word, x + start_idx * step, y, size, font)
                patterns.extend(self._strokes_to_path_patterns(strokes, color))
        else:
            strokes = text_to_paths(text, x, y, size, font)
            patterns.extend(self._strokes_to_path_patterns(strokes, style["base_color"]))

        words = self._word_spans(text)
        if style["underline"] is not None:
            patterns.extend(
                self._build_decorations(
                    text=text,
                    words=words,
                    x=x,
                    y=y,
                    char_w=char_w,
                    step=step,
                    scale=scale,
                    kind="underline",
                    config=style["underline"],
                    line_no=line_no,
                )
            )
        if style["box"] is not None:
            patterns.extend(
                self._build_decorations(
                    text=text,
                    words=words,
                    x=x,
                    y=y,
                    char_w=char_w,
                    step=step,
                    scale=scale,
                    kind="box",
                    config=style["box"],
                    line_no=line_no,
                )
            )
        return patterns

    def _word_spans(self, text: str) -> List[Tuple[int, int, str]]:
        spans: List[Tuple[int, int, str]] = []
        i = 0
        while i < len(text):
            if text[i] == " ":
                i += 1
                continue
            start = i
            while i < len(text) and text[i] != " ":
                i += 1
            spans.append((start, i, text[start:i]))
        return spans

    def _strokes_to_path_patterns(self, strokes: List[List[List[float]]], color: str) -> List[PathPattern]:
        out: List[PathPattern] = []
        c = normalize_color(color)
        for stroke in strokes:
            if len(stroke) < 2:
                continue
            out.append(
                PathPattern(
                    close=False,
                    color=c,
                    points=[[clamp_coord(p[0]), clamp_coord(p[1])] for p in stroke],
                )
            )
        return out

    def _build_decorations(
        self,
        text: str,
        words: List[Tuple[int, int, str]],
        x: float,
        y: float,
        char_w: float,
        step: float,
        scale: float,
        kind: str,
        config: Dict[str, Any],
        line_no: int,
    ) -> List[PathPattern]:
        indices = config["indices"]
        colors = config["colors"] or ["#FFFFFF"]
        out: List[PathPattern] = []

        targets: List[Tuple[int, int]] = []
        if indices is None:
            if not text.strip():
                return out
            if words:
                targets = [(words[0][0], words[-1][1])]
            else:
                targets = [(0, len(text))]
            colors = [colors[0]]
        else:
            if not words:
                raise ValueError(f"{kind.upper()} at line {line_no} requires words in the text line")
            for idx in indices:
                if idx < 0 or idx >= len(words):
                    raise ValueError(f"{kind.upper()} word index {idx} out of range at line {line_no}")
                targets.append((words[idx][0], words[idx][1]))

        for i, (start_idx, end_idx) in enumerate(targets):
            if end_idx <= start_idx:
                continue
            color = normalize_color(colors[i % len(colors)])
            x0 = clamp_coord(x + start_idx * step)
            x1 = clamp_coord(x + (end_idx - 1) * step + char_w)
            if kind == "underline":
                yy = clamp_coord(y + 7.6 * scale)
                out.append(PathPattern(close=False, color=color, points=[[x0, yy], [x1, yy]]))
            else:
                top = clamp_coord(y - 0.6 * scale)
                bottom = clamp_coord(y + 7.6 * scale)
                out.append(
                    PathPattern(
                        close=True,
                        color=color,
                        points=[[x0, top], [x1, top], [x1, bottom], [x0, bottom]],
                    )
                )
        return out

    def _import_text_position(self, text: str, font: str, size: float, center: bool, offset_x: float, offset_y: float) -> Tuple[float, float]:
        if not center:
            return clamp_coord(offset_x), clamp_coord(offset_y)

        strokes = text_to_paths(text, 0.0, 0.0, size, font)
        if not strokes:
            return clamp_coord(180.0 + offset_x), clamp_coord(180.0 + offset_y)

        xs = [pt[0] for stroke in strokes for pt in stroke]
        ys = [pt[1] for stroke in strokes for pt in stroke]

        width = max(xs) - min(xs)
        height = max(ys) - min(ys)
        x = ((360.0 - width) / 2.0) - min(xs) + offset_x
        y = ((360.0 - height) / 2.0) - min(ys) + offset_y
        return clamp_coord(x), clamp_coord(y)

    def add_scene(self) -> None:
        self._commit_ui_to_model()
        self.project.scenes.append(Scene(name=f"Scene {len(self.project.scenes) + 1}", frames=[Frame(time_ms=120, patterns=[])]))
        self.current_scene = len(self.project.scenes) - 1
        self.current_frame = 0
        self.selected_pattern = None
        self.selected_point = None
        self._refresh_scene_list()

    def delete_scene(self) -> None:
        if len(self.project.scenes) <= 1:
            return
        del self.project.scenes[self.current_scene]
        self.current_scene = max(0, self.current_scene - 1)
        self.current_frame = 0
        self.selected_pattern = None
        self.selected_point = None
        self._refresh_scene_list()

    def add_frame(self) -> None:
        self._commit_ui_to_model()
        scene = self.active_scene()
        scene.frames.append(Frame(time_ms=120, patterns=[]))
        self.current_frame = len(scene.frames) - 1
        self.selected_pattern = None
        self.selected_point = None
        self._refresh_frame_list()

    def delete_frame(self) -> None:
        scene = self.active_scene()
        if len(scene.frames) <= 1:
            return
        del scene.frames[self.current_frame]
        self.current_frame = max(0, self.current_frame - 1)
        self.selected_pattern = None
        self.selected_point = None
        self._refresh_frame_list()

    def apply_frame_time(self, refresh: bool = True) -> None:
        try:
            ms = int(self.frame_time_var.get())
        except ValueError:
            ms = 120
        ms = max(1, min(60000, ms))
        self.active_frame().time_ms = ms
        self.frame_time_var.set(str(ms))
        if refresh:
            self._refresh_frame_list()

    def on_scene_select(self, _event: object) -> None:
        if self.preview_running:
            return
        sel = self.scene_list.curselection()
        if not sel:
            return
        self._commit_ui_to_model()
        self.current_scene = sel[0]
        self.current_frame = 0
        self.selected_pattern = None
        self.selected_point = None
        self._refresh_frame_list()

    def on_frame_select(self, _event: object) -> None:
        if self.preview_running:
            return
        sel = self.frame_list.curselection()
        if not sel:
            return
        self._commit_ui_to_model()
        self.current_frame = sel[0]
        self.selected_pattern = None
        self.selected_point = None
        self._refresh_pattern_list()

    def on_frame_click(self, event: tk.Event) -> None:
        if self.preview_running:
            return
        if self.frame_list.size() == 0:
            return
        idx = self.frame_list.nearest(event.y)
        if idx < 0:
            return
        self.frame_list.selection_clear(0, tk.END)
        self.frame_list.selection_set(idx)
        self.frame_list.activate(idx)
        self.on_frame_select(event)

    def _move_frame_selection(self, delta: int) -> None:
        if self.preview_running or self.frame_list.size() == 0:
            return
        current = self.frame_list.curselection()
        idx = current[0] if current else self.current_frame
        target = max(0, min(self.frame_list.size() - 1, idx + delta))
        if target == idx:
            return
        self.frame_list.selection_clear(0, tk.END)
        self.frame_list.selection_set(target)
        self.frame_list.activate(target)
        self.frame_list.see(target)
        self.on_frame_select(None)

    def on_frame_up(self, _event: tk.Event) -> str:
        self._move_frame_selection(-1)
        return "break"

    def on_frame_down(self, _event: tk.Event) -> str:
        self._move_frame_selection(1)
        return "break"

    def on_pattern_select(self, _event: object) -> None:
        if self.preview_running:
            return
        sel = self.pattern_list.curselection()
        if not sel:
            return
        self.selected_pattern = sel[0]
        self.selected_point = None
        self._load_pattern_props_to_ui()
        self.redraw_canvas()

    def add_trace(self) -> None:
        frame = self.active_frame()
        frame.patterns.append(PathPattern(points=[[40.0, 40.0], [120.0, 100.0]], color="#FFFFFF", close=False))
        self.selected_pattern = len(frame.patterns) - 1
        self.selected_point = None
        self._refresh_pattern_list()

    def add_text(self) -> None:
        frame = self.active_frame()
        frame.patterns.append(TextPattern(text="TEXT", x=80.0, y=120.0, size=28.0, color="#FFFFFF", font="normal"))
        self.selected_pattern = len(frame.patterns) - 1
        self.selected_point = None
        self._refresh_pattern_list()

    def delete_selected_pattern(self) -> None:
        if self.selected_pattern is None:
            return
        frame = self.active_frame()
        if self.selected_pattern < 0 or self.selected_pattern >= len(frame.patterns):
            return
        del frame.patterns[self.selected_pattern]
        self.selected_pattern = None
        self.selected_point = None
        self._refresh_pattern_list()

    def delete_selected_point(self) -> None:
        if self.selected_pattern is None or self.selected_point is None:
            return
        frame = self.active_frame()
        pat = frame.patterns[self.selected_pattern]
        if not isinstance(pat, PathPattern):
            return
        if self.selected_point < 0 or self.selected_point >= len(pat.points):
            return
        del pat.points[self.selected_point]
        self.selected_point = None
        self.redraw_canvas()

    def apply_path_props(self) -> None:
        pat = self._selected_pattern_obj()
        if not isinstance(pat, PathPattern):
            return
        pat.color = normalize_color(self.color_var.get())
        self.color_var.set(pat.color)
        pat.close = bool(self.close_var.get())
        self.redraw_canvas()

    def apply_text_props(self) -> None:
        pat = self._selected_pattern_obj()
        if not isinstance(pat, TextPattern):
            return
        pat.color = normalize_color(self.color_var.get())
        self.color_var.set(pat.color)
        pat.text = self.text_var.get() or "TEXT"
        pat.font = self.text_font_var.get() if self.text_font_var.get() in {"normal", "monospace", "bold"} else "normal"
        try:
            pat.size = max(8.0, min(120.0, float(self.text_size_var.get())))
        except ValueError:
            pat.size = 28.0
        self.text_size_var.set(str(int(pat.size) if pat.size.is_integer() else pat.size))
        self.redraw_canvas()

    def _commit_ui_to_model(self) -> None:
        self.apply_frame_time(refresh=False)

    def _selected_pattern_obj(self):
        if self.selected_pattern is None:
            return None
        frame = self.active_frame()
        if self.selected_pattern < 0 or self.selected_pattern >= len(frame.patterns):
            return None
        return frame.patterns[self.selected_pattern]

    def _load_pattern_props_to_ui(self) -> None:
        pat = self._selected_pattern_obj()
        if pat is None:
            return
        self.color_var.set(getattr(pat, "color", "#FFFFFF"))
        if isinstance(pat, PathPattern):
            self.close_var.set(pat.close)
        if isinstance(pat, TextPattern):
            self.text_var.set(pat.text)
            self.text_font_var.set(pat.font)
            self.text_size_var.set(str(int(pat.size) if float(pat.size).is_integer() else pat.size))

    def _refresh_scene_list(self) -> None:
        self.scene_list.delete(0, tk.END)
        for idx, scene in enumerate(self.project.scenes):
            self.scene_list.insert(tk.END, f"{idx + 1}. {scene.name}")
        self.scene_list.selection_set(self.current_scene)
        self._refresh_frame_list()

    def _refresh_frame_list(self) -> None:
        self.frame_list.delete(0, tk.END)
        scene = self.active_scene()
        for idx, frame in enumerate(scene.frames):
            self.frame_list.insert(tk.END, f"{idx + 1}. time_ms={frame.time_ms}")
        self.current_frame = max(0, min(self.current_frame, len(scene.frames) - 1))
        self.frame_list.selection_set(self.current_frame)
        self.frame_time_var.set(str(self.active_frame().time_ms))
        self._refresh_pattern_list()

    def _refresh_pattern_list(self) -> None:
        self.pattern_list.delete(0, tk.END)
        frame = self.active_frame()
        for idx, pat in enumerate(frame.patterns):
            if isinstance(pat, PathPattern):
                self.pattern_list.insert(tk.END, f"{idx + 1}. TRACE pts={len(pat.points)}")
            else:
                self.pattern_list.insert(tk.END, f"{idx + 1}. TEXT '{pat.text[:12]}'")
        if self.selected_pattern is not None and 0 <= self.selected_pattern < len(frame.patterns):
            self.pattern_list.selection_set(self.selected_pattern)
        else:
            self.selected_pattern = None
        self._load_pattern_props_to_ui()
        self.redraw_canvas()
    def redraw_canvas(self) -> None:
        self.canvas.delete("all")
        self._draw_grid()

        frame = self.active_frame()
        for idx, pat in enumerate(frame.patterns):
            selected = (idx == self.selected_pattern) and (not self.preview_running)
            if isinstance(pat, PathPattern):
                self._draw_path_pattern(pat, selected, idx)
            else:
                self._draw_text_pattern(pat, selected)

    def _draw_grid(self) -> None:
        step = 30
        for g in range(0, 361, step):
            c = "#1f1f1f" if g % 60 else "#2e2e2e"
            x0, y0 = self.world_to_canvas(g, 0)
            x1, y1 = self.world_to_canvas(g, 360)
            self.canvas.create_line(x0, y0, x1, y1, fill=c)
            x0, y0 = self.world_to_canvas(0, g)
            x1, y1 = self.world_to_canvas(360, g)
            self.canvas.create_line(x0, y0, x1, y1, fill=c)

    def _draw_path_pattern(self, pat: PathPattern, selected: bool, idx: int) -> None:
        pts = pat.points
        if len(pts) >= 2:
            coords = []
            for p in pts:
                cx, cy = self.world_to_canvas(p[0], p[1])
                coords.extend([cx, cy])
            self.canvas.create_line(*coords, fill=pat.color, width=2)
            if pat.close and len(pts) > 2:
                a = self.world_to_canvas(pts[-1][0], pts[-1][1])
                b = self.world_to_canvas(pts[0][0], pts[0][1])
                self.canvas.create_line(*a, *b, fill=pat.color, width=2)

        for pidx, p in enumerate(pts):
            cx, cy = self.world_to_canvas(p[0], p[1])
            r = 5 if selected and self.selected_point == pidx else 3
            col = "#ffd966" if selected and self.selected_point == pidx else ("#6fa8dc" if selected else "#cfcfcf")
            self.canvas.create_oval(cx - r, cy - r, cx + r, cy + r, fill=col, outline="")

        if selected:
            self.canvas.create_text(8, 8 + idx * 16, anchor="nw", fill="#f8f8f8", text=f"Selected trace {idx + 1}")

    def _draw_text_pattern(self, pat: TextPattern, selected: bool) -> None:
        strokes = text_to_paths(pat.text, pat.x, pat.y, pat.size, pat.font)
        for stroke in strokes:
            if len(stroke) < 2:
                continue
            coords = []
            for p in stroke:
                cx, cy = self.world_to_canvas(p[0], p[1])
                coords.extend([cx, cy])
            self.canvas.create_line(*coords, fill=pat.color, width=2)

        if selected:
            minx, miny, maxx, maxy = self._text_bounds(pat)
            x0, y0 = self.world_to_canvas(minx, miny)
            x1, y1 = self.world_to_canvas(maxx, maxy)
            self.canvas.create_rectangle(x0, y0, x1, y1, outline="#ffd966", dash=(4, 2))

    def _text_bounds(self, pat: TextPattern) -> Tuple[float, float, float, float]:
        strokes = text_to_paths(pat.text, pat.x, pat.y, pat.size, pat.font)
        if not strokes:
            return pat.x, pat.y, pat.x + 10, pat.y + 10
        xs = [pt[0] for stroke in strokes for pt in stroke]
        ys = [pt[1] for stroke in strokes for pt in stroke]
        return max(0.0, min(xs)), max(0.0, min(ys)), min(360.0, max(xs)), min(360.0, max(ys))

    def on_canvas_press(self, event: tk.Event) -> None:
        if self.preview_running:
            return
        wx, wy = self.canvas_to_world(event.x, event.y)

        if self.mode_var.get() == "add_point":
            pat = self._selected_pattern_obj()
            if isinstance(pat, PathPattern):
                pat.points.append([wx, wy])
                self.selected_point = len(pat.points) - 1
                self.redraw_canvas()
                return
            self.status_var.set("Select a trace first, then use Add Point")
            return

        hit = self._hit_test(wx, wy)
        if hit is None:
            self.selected_point = None
            self.redraw_canvas()
            return

        pidx, point_idx, hit_kind = hit
        self.selected_pattern = pidx
        self.selected_point = point_idx
        self.pattern_list.selection_clear(0, tk.END)
        self.pattern_list.selection_set(self.selected_pattern)
        self._load_pattern_props_to_ui()

        if hit_kind == "point":
            self.drag_mode = "point"
        elif hit_kind == "text":
            self.drag_mode = "text"
        else:
            self.drag_mode = None
        self.redraw_canvas()

    def on_canvas_drag(self, event: tk.Event) -> None:
        if self.preview_running or self.selected_pattern is None:
            return
        wx, wy = self.canvas_to_world(event.x, event.y)
        pat = self._selected_pattern_obj()
        if pat is None:
            return

        if self.drag_mode == "point" and isinstance(pat, PathPattern) and self.selected_point is not None:
            if 0 <= self.selected_point < len(pat.points):
                pat.points[self.selected_point][0] = wx
                pat.points[self.selected_point][1] = wy
                self.redraw_canvas()
        elif self.drag_mode == "text" and isinstance(pat, TextPattern):
            pat.x = wx
            pat.y = wy
            self.redraw_canvas()

    def on_canvas_release(self, _event: tk.Event) -> None:
        self.drag_mode = None

    def _hit_test(self, wx: float, wy: float) -> Optional[Tuple[int, Optional[int], str]]:
        frame = self.active_frame()

        for pidx, pat in enumerate(frame.patterns):
            if isinstance(pat, PathPattern):
                for idx, pt in enumerate(pat.points):
                    if self._distance(wx, wy, pt[0], pt[1]) <= 4.0:
                        return pidx, idx, "point"

        for pidx, pat in enumerate(frame.patterns):
            if isinstance(pat, TextPattern):
                minx, miny, maxx, maxy = self._text_bounds(pat)
                if minx - 2 <= wx <= maxx + 2 and miny - 2 <= wy <= maxy + 2:
                    return pidx, None, "text"

        nearest: Optional[Tuple[int, float]] = None
        for pidx, pat in enumerate(frame.patterns):
            if not isinstance(pat, PathPattern):
                continue
            for i in range(len(pat.points) - 1):
                d = self._distance_to_segment(wx, wy, pat.points[i], pat.points[i + 1])
                if nearest is None or d < nearest[1]:
                    nearest = (pidx, d)
            if pat.close and len(pat.points) > 2:
                d = self._distance_to_segment(wx, wy, pat.points[-1], pat.points[0])
                if nearest is None or d < nearest[1]:
                    nearest = (pidx, d)

        if nearest is not None and nearest[1] <= 3.5:
            return nearest[0], None, "trace"
        return None

    @staticmethod
    def _distance(x1: float, y1: float, x2: float, y2: float) -> float:
        return math.hypot(x1 - x2, y1 - y2)

    @staticmethod
    def _distance_to_segment(px: float, py: float, a: list[float], b: list[float]) -> float:
        ax, ay = a
        bx, by = b
        dx = bx - ax
        dy = by - ay
        if dx == 0 and dy == 0:
            return math.hypot(px - ax, py - ay)
        t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
        t = max(0.0, min(1.0, t))
        proj_x = ax + t * dx
        proj_y = ay + t * dy
        return math.hypot(px - proj_x, py - proj_y)


def main() -> None:
    root = tk.Tk()
    app = TF1EditorApp(root)
    app.redraw_canvas()
    root.mainloop()


if __name__ == "__main__":
    main()
