
from __future__ import annotations

import math
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
        self.frame_list = tk.Listbox(frame_box, exportselection=False, height=6, width=28)
        self.frame_list.pack(fill="x")
        self.frame_list.bind("<<ListboxSelect>>", self.on_frame_select)
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
            self.preview_entry.configure(state="normal")
            self.stop_btn.configure(state="disabled")
        else:
            self.preview_btn.configure(state="disabled")
            self.import_btn.configure(state="disabled")
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
        self._commit_ui_to_model()
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
        self._refresh_frame_list()

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
        except Exception as exc:
            messagebox.showerror("Import failed", str(exc))
            return

        settings = self._show_import_settings_dialog(Path(path).stem)
        if settings is None:
            return

        scene = self._build_scene_from_import(items, settings, Path(path).stem)
        self.project.scenes.append(scene)
        self.current_scene = len(self.project.scenes) - 1
        self.current_frame = 0
        self.selected_pattern = None
        self.selected_point = None
        self._refresh_scene_list()
        self.status_var.set(f"Imported scene from {Path(path).name}")

    def _parse_import_lines(self, lines: List[str]) -> List[Tuple[float, str]]:
        items: List[Tuple[float, str]] = []
        i = 0
        while i < len(lines):
            if not lines[i].strip():
                i += 1
                continue

            try:
                offset_sec = float(lines[i].strip())
            except ValueError as exc:
                raise ValueError(f"Invalid time offset at line {i + 1}") from exc

            if i + 1 >= len(lines):
                raise ValueError(f"Missing text line after time offset on line {i + 1}")
            text = lines[i + 1]
            items.append((offset_sec, text))

            i += 3

        if not items:
            raise ValueError("No timeline records found")
        if abs(items[0][0]) > 1e-9:
            raise ValueError("First time offset must be 0")
        return items

    def _show_import_settings_dialog(self, source_name: str) -> Optional[Dict[str, Any]]:
        dialog = tk.Toplevel(self.root)
        dialog.title(f"Import Settings: {source_name}")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.resizable(False, False)

        offset_x_var = tk.StringVar(value="40")
        offset_y_var = tk.StringVar(value="120")
        center_var = tk.BooleanVar(value=False)
        font_var = tk.StringVar(value="normal")
        size_var = tk.StringVar(value="28")

        body = ttk.Frame(dialog, padding=10)
        body.grid(row=0, column=0, sticky="nsew")

        ttk.Label(body, text="Offset X (0..360)").grid(row=0, column=0, sticky="w")
        ttk.Entry(body, textvariable=offset_x_var, width=16).grid(row=0, column=1, pady=3, sticky="ew")

        ttk.Label(body, text="Offset Y (0..360)").grid(row=1, column=0, sticky="w")
        ttk.Entry(body, textvariable=offset_y_var, width=16).grid(row=1, column=1, pady=3, sticky="ew")

        ttk.Checkbutton(body, text="Center text", variable=center_var).grid(row=2, column=0, columnspan=2, sticky="w", pady=3)

        ttk.Label(body, text="Text Font").grid(row=3, column=0, sticky="w")
        ttk.Combobox(body, textvariable=font_var, values=["normal", "monospace", "bold"], state="readonly", width=14).grid(row=3, column=1, pady=3, sticky="ew")

        ttk.Label(body, text="Text Size").grid(row=4, column=0, sticky="w")
        ttk.Entry(body, textvariable=size_var, width=16).grid(row=4, column=1, pady=3, sticky="ew")

        actions = ttk.Frame(body)
        actions.grid(row=5, column=0, columnspan=2, sticky="e", pady=(8, 0))

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
            result["font"] = font_var.get() if font_var.get() in {"normal", "monospace", "bold"} else "normal"
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
    def _build_scene_from_import(self, items: List[Tuple[float, str]], settings: Dict[str, Any], stem: str) -> Scene:
        frames: List[Frame] = []
        default_last = 1.0
        if len(items) > 1:
            default_last = max(0.1, items[-1][0] - items[-2][0])

        for idx, (offset_sec, text) in enumerate(items):
            if idx + 1 < len(items):
                duration_sec = max(0.1, items[idx + 1][0] - offset_sec)
            else:
                duration_sec = default_last

            x, y = self._import_text_position(
                text=text,
                font=settings["font"],
                size=settings["size"],
                center=settings["center"],
                offset_x=settings["offset_x"],
                offset_y=settings["offset_y"],
            )

            frame = Frame(
                time_ms=max(1, int(duration_sec * 1000)),
                patterns=[
                    TextPattern(
                        text=text,
                        x=x,
                        y=y,
                        size=settings["size"],
                        color="#FFFFFF",
                        font=settings["font"],
                    )
                ],
            )
            frames.append(frame)

        return Scene(name=f"Imported {stem}", frames=frames)

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

    def apply_frame_time(self) -> None:
        try:
            ms = int(self.frame_time_var.get())
        except ValueError:
            ms = 120
        ms = max(1, min(60000, ms))
        self.active_frame().time_ms = ms
        self.frame_time_var.set(str(ms))
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
        self.apply_frame_time()

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
