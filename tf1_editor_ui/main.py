
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
