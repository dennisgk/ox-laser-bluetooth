from __future__ import annotations

import math
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Optional, Tuple

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

        self.mode_var = tk.StringVar(value="select")
        self.close_var = tk.BooleanVar(value=False)
        self.color_var = tk.StringVar(value="#FFFFFF")
        self.frame_time_var = tk.StringVar(value="120")
        self.text_var = tk.StringVar(value="TEXT")
        self.text_font_var = tk.StringVar(value="normal")
        self.text_size_var = tk.StringVar(value="28")
        self.status_var = tk.StringVar(value="Ready")

        self._build_ui()
        self._refresh_scene_list()

    def _build_ui(self) -> None:
        self.root.columnconfigure(1, weight=1)
        self.root.rowconfigure(0, weight=1)

        left_wrap = ttk.Frame(self.root, padding=8)
        left_wrap.grid(row=0, column=0, sticky="ns")
        left_wrap.rowconfigure(0, weight=1)
        left_wrap.columnconfigure(0, weight=1)

        self.left_canvas = tk.Canvas(left_wrap, width=290, highlightthickness=0)
        self.left_canvas.grid(row=0, column=0, sticky="ns")
        left_scroll = ttk.Scrollbar(left_wrap, orient="vertical", command=self.left_canvas.yview)
        left_scroll.grid(row=0, column=1, sticky="ns")
        self.left_canvas.configure(yscrollcommand=left_scroll.set)

        left = ttk.Frame(self.left_canvas)
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
        canvas_wrap.grid(row=0, column=1, sticky="nsew")
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
        ttk.Combobox(props_box, textvariable=self.text_font_var, values=["normal", "monospace", "bold"], state="readonly").pack(fill="x")
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

    def _on_left_mousewheel(self, event: tk.Event) -> None:
        if not self.left_canvas.winfo_exists():
            return
        # Windows mouse wheel: delta steps of +/-120.
        self.left_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def world_to_canvas(self, x: float, y: float) -> Tuple[float, float]:
        return x * self.SCALE, y * self.SCALE

    def canvas_to_world(self, x: float, y: float) -> Tuple[float, float]:
        return clamp_coord(x / self.SCALE), clamp_coord(y / self.SCALE)

    def active_scene(self) -> Scene:
        return self.project.scenes[self.current_scene]

    def active_frame(self) -> Frame:
        return self.active_scene().frames[self.current_frame]

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
        sel = self.frame_list.curselection()
        if not sel:
            return
        self._commit_ui_to_model()
        self.current_frame = sel[0]
        self.selected_pattern = None
        self.selected_point = None
        self._refresh_pattern_list()

    def on_pattern_select(self, _event: object) -> None:
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
            selected = idx == self.selected_pattern
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
        if self.selected_pattern is None:
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
