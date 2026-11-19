"""Borderless desktop clock.

The window has no title bar (``overrideredirect``), so Windows will not draw
resize borders. Pixels painted with the color key are click-through, so the
plate behind the clock is what actually receives those clicks. Dragging the
plate or the digits moves the clock. Dragging a corner of the plate resizes
it. Nothing is drawn on the corners.

A gear opens three settings: always on top, and whether each slider is
visible. The time and date fonts are recomputed from the window size as it
changes.

Two sliders sit on the bottom edge. Glass runs from a solid plate at 0 to
fully clear at 100. Font brightness runs from invisible at 0 to the full
digit colors at 100. They do not move together. The clock window uses a
color key so its painted background drops out and the plate shows through.
Dimming the digits recolors only the time and the date, so the gear, the
close button, and the sliders stay easy to see.
"""

import ctypes
import os
import tkinter as tk
import tkinter.font as tkfont
from datetime import datetime

user32 = ctypes.windll.user32
user32.GetParent.argtypes = [ctypes.c_void_p]
user32.GetParent.restype = ctypes.c_void_p
user32.SetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
user32.SetWindowLongPtrW.restype = ctypes.c_void_p
user32.SetWindowPos.argtypes = [
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_uint,
]
user32.SetWindowPos.restype = ctypes.c_bool
user32.GetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int]
user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t

# Places the glass immediately below the clock without focusing either one.
GWL_HWNDPARENT = -8
GWL_EXSTYLE = -20
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020
# A click must not activate the plate. Activation would raise it over the digits.
WS_EX_NOACTIVATE = 0x08000000

# The smoke test sets this so the window is never mapped on the desktop.
SMOKE = os.environ.get("CLOCK_SMOKE") == "1"

# Palette shared by every widget. BG is also the color key: those pixels are
# not drawn, so the glass plate behind the window shows through. Text colors
# must not equal BG or the glyphs would disappear with the plate.
BG = "#1a1d21"
FG = "#eceff4"
MUTED = "#88c0d0"
DANGER = "#bf616a"

# Invisible grab area at each corner, in pixels.
GRIP_SIZE = 18

# Smallest type that still draws every stroke of the time and the date.
# Segoe UI at 6pt keeps the counters of 0, 6, 8, and 9, so nothing is lost.
MIN_TIME_PT = 6
MIN_DATE_PT = 6
# Date stays about a third of the time once the window has room to grow.
DATE_RATIO = 0.34
# Gap between the time and the date, and air at the sides so ink is not cut.
DATE_GAP = 1
FACE_PAD = 2
# Trough still long enough to drag when the window is at its narrowest.
SLIDER_MIN_TROUGH = 32

# Opening size, before the user drags a corner.
START_W = 320
START_H = 200

# Font brightness when the clock opens. 100 is the full digit color. 0
# matches the color key, so the glyphs disappear. 62 keeps the dimness the
# old shared slider produced at its midpoint.
FONT_BRIGHTNESS = 62

# Windows treats a layered window at alpha 0 as click-through. Two units out
# of 255 stay visually clear and still take the pointer, so a fully clear
# plate can be dragged and its corners can resize.
GLASS_ALPHA_FLOOR = 2 / 255


class ClockApp:
    """One borderless clock window and its settings."""

    def __init__(self):
        self.root = tk.Tk()
        if SMOKE:
            self.root.withdraw()
        self.root.title("Clock")
        # Remove the Windows frame. Resize and drag are implemented below.
        self.root.overrideredirect(True)
        self.root.configure(bg=BG)
        # Overrideredirect windows ignore the normal resize cursors unless
        # resizable is left on. Size is still enforced by the corner code.
        self.root.resizable(True, True)

        self._drag_x = 0
        self._drag_y = 0
        self._dragging = False
        # Set only while a corner is held. None means a normal drag.
        self._resize = None
        self._time_size = 0
        self._date_size = 0
        self._typefaces = {}
        self._label_extra_w = 0
        self._label_extra_h = 0
        # Replaced once the widgets have been measured.
        self._min_w = START_W
        self._min_h = START_H
        self._time_sample = "00:00:00"
        self._date_sample = "Wednesday, 30 September 2026"
        self._prepare_metrics()

        # Pinned, glass half clear, digits at FONT_BRIGHTNESS. Both sliders
        # start visible; the gear can hide either one.
        self.top_var = tk.BooleanVar(value=True)
        self.show_glass_var = tk.BooleanVar(value=True)
        self.show_font_var = tk.BooleanVar(value=True)
        self.glass_var = tk.IntVar(value=50)
        self.font_var = tk.IntVar(value=FONT_BRIGHTNESS)
        self._settings_open = False

        self.root.attributes("-topmost", True)
        # The window itself stays fully opaque. Glass alpha is the plate.
        # Font brightness recolors the digits. The color key removes BG.
        self.root.attributes("-alpha", 1.0)
        self.root.attributes("-transparentcolor", BG)
        self._build_glass()

        shell = tk.Frame(self.root, bg=BG)
        # Children must not be allowed to grow the window. The corner drag
        # is the only thing that changes the size.
        shell.pack(fill=tk.BOTH, expand=True)
        shell.pack_propagate(False)
        self.shell = shell

        self._build_header(shell)
        self._build_settings(shell)
        self._build_face(shell)
        self._build_slider(shell)
        self._apply_minsize()

        # These widgets only see clicks on pixels that are not the color key,
        # which is the digit ink. The plate covers every punched-out pixel.
        for widget in (shell, self.body, self.face, self.time_lbl, self.date_lbl):
            widget.bind("<Button-1>", self._start_drag)
            widget.bind("<B1-Motion>", self._on_drag)
            widget.bind("<ButtonRelease-1>", self._end_pointer)

        self.root.bind("<Escape>", lambda _e: self.root.destroy())
        self.root.bind("<Configure>", self._on_root_configure)

        self.root.geometry(f"{START_W}x{START_H}")
        self._place_near_tray()
        self._apply_glass(self.glass_var.get())
        self._apply_font(self.font_var.get())
        self._sync_glass()
        self._tick()

    def _outer_hwnd(self, widget):
        """Win32 handle of the framed window Tk wraps around a widget."""
        child = widget.winfo_id()
        parent = user32.GetParent(child)
        return parent or child

    def _build_glass(self):
        """Frameless plate behind the clock. Its alpha is the slider.

        A Toplevel owned by the clock would be forced to stay above it, which
        would cover the digits. The owner link is cleared so the plate can
        sit underneath.
        """
        self.glass = tk.Toplevel(self.root)
        if SMOKE:
            self.glass.withdraw()
        self.glass.overrideredirect(True)
        self.glass.configure(bg=BG, cursor="fleur")
        self.glass.attributes("-alpha", 1.0)
        self.glass.attributes("-topmost", True)
        self.glass.update_idletasks()
        user32.SetWindowLongPtrW(self._outer_hwnd(self.glass), GWL_HWNDPARENT, None)
        # Color-key clicks land here. Corners resize; the rest of the plate moves.
        self.glass.bind("<Button-1>", self._glass_press)
        self.glass.bind("<B1-Motion>", self._glass_motion)
        self.glass.bind("<ButtonRelease-1>", self._end_pointer)
        self.glass.bind("<Motion>", self._glass_hover)
        self._no_activate(self.glass)

    def _sync_glass(self):
        """Match the plate to the clock, then keep it directly underneath."""
        try:
            if not self.root.winfo_exists() or not self.glass.winfo_exists():
                return
        except tk.TclError:
            return
        w = max(self.root.winfo_width(), 1)
        h = max(self.root.winfo_height(), 1)
        x = self.root.winfo_x()
        y = self.root.winfo_y()
        self.glass.geometry(f"{w}x{h}+{x}+{y}")
        user32.SetWindowPos(
            self._outer_hwnd(self.glass),
            self._outer_hwnd(self.root),
            0,
            0,
            0,
            0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
        )

    def _build_header(self, shell):
        """Top row: empty drag space, then the gear and the close button."""
        header = tk.Frame(shell, bg=BG)
        header.pack(side=tk.TOP, fill=tk.X)
        self.header = header
        # The empty part of the bar moves the window. The gear and the close mark do not.
        spacer = tk.Frame(header, bg=BG, cursor="fleur")
        spacer.pack(side=tk.LEFT, fill=tk.X, expand=True)
        spacer.bind("<Button-1>", self._start_drag)
        spacer.bind("<B1-Motion>", self._on_drag)
        spacer.bind("<ButtonRelease-1>", self._end_pointer)

        # Labels, not buttons: a button adds empty pixels above the glyph and
        # those pixels are the color key, so they never helped the click.
        self.gear_btn = tk.Label(
            header,
            text="\u2699",
            font=("Segoe UI Symbol", 12),
            fg=MUTED,
            bg=BG,
            bd=0,
            highlightthickness=0,
            padx=2,
            pady=0,
            cursor="hand2",
        )
        self.gear_btn.pack(side=tk.LEFT)
        self.gear_btn.bind("<Button-1>", lambda _e: self._toggle_settings())

        close = tk.Label(
            header,
            text="\u00d7",
            font=("Segoe UI", 12, "bold"),
            fg=FG,
            bg=BG,
            bd=0,
            highlightthickness=0,
            padx=2,
            pady=0,
            cursor="hand2",
        )
        close.pack(side=tk.LEFT, padx=(0, 2))
        close.bind("<Button-1>", lambda _e: self.root.destroy())

    def _build_settings(self, shell):
        """Gear panel. Packed only while the gear is on."""
        self.settings = tk.Frame(shell, bg=BG, padx=12, pady=2)
        check = dict(
            bg=BG,
            fg=FG,
            selectcolor="#3b4252",
            activebackground=BG,
            activeforeground="#ffffff",
            font=("Segoe UI", 10),
            bd=0,
            highlightthickness=0,
            anchor="w",
            takefocus=False,
        )
        tk.Checkbutton(
            self.settings,
            text="Always on top",
            variable=self.top_var,
            command=self._apply_top,
            **check,
        ).pack(fill=tk.X)
        tk.Checkbutton(
            self.settings,
            text="Glass slider",
            variable=self.show_glass_var,
            command=self._apply_sliders,
            **check,
        ).pack(fill=tk.X)
        tk.Checkbutton(
            self.settings,
            text="Font brightness",
            variable=self.show_font_var,
            command=self._apply_sliders,
            **check,
        ).pack(fill=tk.X)

    def _build_face(self, shell):
        """Time and date, centered in whatever space the window leaves them."""
        self.body = tk.Frame(shell, bg=BG)
        self.body.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self.face = tk.Frame(self.body, bg=BG)
        # relx/rely 0.5 keeps the digits in the middle as the window grows.
        self.face.place(relx=0.5, rely=0.5, anchor="center")

        self.time_lbl = tk.Label(
            self.face,
            text="00:00:00",
            font=("Segoe UI", 42, "bold"),
            fg=FG,
            bg=BG,
            cursor="fleur",
            bd=0,
            highlightthickness=0,
            padx=0,
            pady=0,
        )
        self.time_lbl.pack()

        self.date_lbl = tk.Label(
            self.face,
            text="",
            font=("Segoe UI", 14),
            fg=MUTED,
            bg=BG,
            cursor="fleur",
            bd=0,
            highlightthickness=0,
            padx=0,
            pady=0,
        )
        self.date_lbl.pack(pady=(DATE_GAP, 0))

    def _make_slider(self, caption, variable, command):
        """One labeled scale. The caption is not dimmed with the clock face."""
        row = tk.Frame(self.shell, bg=BG)
        tk.Label(
            row,
            text=caption,
            font=("Segoe UI", 8),
            fg=MUTED,
            bg=BG,
            anchor="w",
            padx=0,
            pady=0,
        ).pack(side=tk.LEFT, padx=(GRIP_SIZE, 4))
        scale = tk.Scale(
            row,
            from_=0,
            to=100,
            orient=tk.HORIZONTAL,
            variable=variable,
            command=command,
            showvalue=False,
            sliderlength=16,
            width=8,
            bd=0,
            highlightthickness=0,
            troughcolor="#3b4252",
            fg=MUTED,
            bg=BG,
            activebackground=MUTED,
            relief=tk.FLAT,
            takefocus=False,
        )
        # Stay clear of the corners so the plate, not the trough, gets those clicks.
        scale.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, GRIP_SIZE))
        return row

    def _build_slider(self, shell):
        """Glass and font scales along the bottom. Each can be packed away."""
        # Pack the font row first so it stays the lower of the two.
        self.font_row = self._make_slider("Font", self.font_var, self._set_font)
        self.glass_row = self._make_slider("Glass", self.glass_var, self._set_glass)
        self.font_row.pack(side=tk.BOTTOM, fill=tk.X)
        self.glass_row.pack(side=tk.BOTTOM, fill=tk.X)

    def _toggle_settings(self):
        """Show or hide the gear panel without changing the window size."""
        self._settings_open = not self._settings_open
        if self._settings_open:
            # Sit under the header and above the centered face.
            self.settings.pack(side=tk.TOP, fill=tk.X, before=self.body)
            self.gear_btn.configure(fg=FG)
        else:
            self.settings.pack_forget()
            self.gear_btn.configure(fg=MUTED)
        self._apply_minsize()
        self._fit_fonts(self.root.winfo_width(), self.root.winfo_height())

    def _apply_top(self):
        """Turn the always-on-top flag on or off for the clock and the plate."""
        top = bool(self.top_var.get())
        self.glass.attributes("-topmost", top)
        self.root.attributes("-topmost", top)
        self._no_activate(self.glass)
        self._sync_glass()

    def _apply_sliders(self):
        """Show or hide each scale. Hiding one does not change its value."""
        self.glass_row.pack_forget()
        self.font_row.pack_forget()
        # Pack bottom-up so Font stays under Glass whenever both are shown.
        if self.show_font_var.get():
            self.font_row.pack(side=tk.BOTTOM, fill=tk.X)
        if self.show_glass_var.get():
            self.glass_row.pack(side=tk.BOTTOM, fill=tk.X)
        self._apply_minsize()
        self._fit_fonts(self.root.winfo_width(), self.root.winfo_height())

    def _place_near_tray(self):
        """Open above the taskbar, at the right edge of the primary screen."""
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = sw - START_W - 24
        y = sh - START_H - 72
        self.root.geometry(f"{START_W}x{START_H}+{x}+{y}")

    def _no_activate(self, widget):
        """Let the plate take clicks without rising above the clock.

        Windows caches the extended style, so the change is published with
        ``SWP_FRAMECHANGED`` or a later click still activates the plate.
        """
        hwnd = self._outer_hwnd(widget)
        style = int(user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE) or 0)
        new_style = style | WS_EX_NOACTIVATE
        if new_style != style:
            user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, ctypes.c_void_p(new_style))
        user32.SetWindowPos(
            hwnd,
            None,
            0,
            0,
            0,
            0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED,
        )

    def _grab(self, widget):
        """Keep motion events if the pointer leaves the widget mid-drag."""
        try:
            widget.grab_set()
        except tk.TclError:
            pass

    def _release(self, widget):
        try:
            widget.grab_release()
        except tk.TclError:
            pass

    def _corner_at(self, x_root, y_root):
        """Return which corner the pointer is in, or None when it is not."""
        local_x = x_root - self.root.winfo_x()
        local_y = y_root - self.root.winfo_y()
        width = self.root.winfo_width()
        height = self.root.winfo_height()
        on_left = local_x < GRIP_SIZE
        on_right = local_x >= width - GRIP_SIZE
        on_top = local_y < GRIP_SIZE
        on_bottom = local_y >= height - GRIP_SIZE
        if on_top and on_left:
            return "nw"
        if on_top and on_right:
            return "ne"
        if on_bottom and on_left:
            return "sw"
        if on_bottom and on_right:
            return "se"
        return None

    def _glass_press(self, event):
        """Resize from a corner of the plate. Otherwise start a move."""
        corner = self._corner_at(event.x_root, event.y_root)
        if corner is not None:
            self._start_resize(event, corner)
        else:
            self._start_drag(event)
        self._sync_glass()

    def _glass_motion(self, event):
        if self._resize is not None:
            self._on_resize(event)
        elif self._dragging:
            self._on_drag(event)

    def _glass_hover(self, event):
        """Resize cursor on the corners, move cursor on the rest of the plate."""
        if self._resize is not None:
            return
        corner = self._corner_at(event.x_root, event.y_root)
        if corner in ("nw", "se"):
            cursor = "size_nw_se"
        elif corner in ("ne", "sw"):
            cursor = "size_ne_sw"
        else:
            cursor = "fleur"
        if str(self.glass.cget("cursor")) != cursor:
            self.glass.configure(cursor=cursor)

    def _start_drag(self, event):
        """Remember where the pointer grabbed the window, if not resizing."""
        if self._resize is not None:
            return
        self._dragging = True
        self._drag_x = event.x_root - self.root.winfo_x()
        self._drag_y = event.y_root - self.root.winfo_y()
        self._grab(event.widget)

    def _on_drag(self, event):
        """Move the window. Corner drags are ignored here."""
        if self._resize is not None or not self._dragging:
            return
        x = event.x_root - self._drag_x
        y = event.y_root - self._drag_y
        self.root.geometry(f"+{x}+{y}")
        self._sync_glass()

    def _start_resize(self, event, corner):
        """Record the window rectangle at the moment a corner is grabbed."""
        self._dragging = False
        self._resize = {
            "corner": corner,
            "mx": event.x_root,
            "my": event.y_root,
            "x": self.root.winfo_x(),
            "y": self.root.winfo_y(),
            "w": self.root.winfo_width(),
            "h": self.root.winfo_height(),
        }
        self._grab(event.widget)

    def _end_pointer(self, _event):
        """Drop both the move and the resize, whichever one was active."""
        widget = getattr(_event, "widget", None)
        self._resize = None
        self._dragging = False
        if widget is not None:
            self._release(widget)

    def _on_resize(self, event):
        """Grow or shrink from the grabbed corner. The opposite corner stays."""
        if self._resize is None:
            return
        state = self._resize
        dx = event.x_root - state["mx"]
        dy = event.y_root - state["my"]
        corner = state["corner"]

        # Left corners: dragging left (negative dx) increases width and
        # shifts the origin left so the right edge does not move.
        if "w" in corner:
            width = state["w"] - dx
            x = state["x"] + dx
        else:
            width = state["w"] + dx
            x = state["x"]
        if "n" in corner:
            height = state["h"] - dy
            y = state["y"] + dy
        else:
            height = state["h"] + dy
            y = state["y"]

        # If the drag passes the minimum, pin that edge instead of letting
        # the window slide while the size stays stuck at the minimum.
        if width < self._min_w:
            if "w" in corner:
                x = state["x"] + state["w"] - self._min_w
            width = self._min_w
        if height < self._min_h:
            if "n" in corner:
                y = state["y"] + state["h"] - self._min_h
            height = self._min_h

        self.root.geometry(f"{int(width)}x{int(height)}+{int(x)}+{int(y)}")
        self._sync_glass()

    def _on_root_configure(self, event):
        """Refit the type whenever the root window's pixel size changes."""
        if event.widget is not self.root:
            return
        self._fit_fonts(event.width, event.height)
        self._sync_glass()

    def _prepare_metrics(self):
        """Find the widest time and date, and how much a label adds around them."""
        normal = self._typeface(MIN_DATE_PT, "normal")
        wide_digit = max("0123456789", key=normal.measure)
        bold = self._typeface(MIN_TIME_PT, "bold")
        wide_bold = max("0123456789", key=bold.measure)
        self._time_sample = f"{wide_bold}{wide_bold}:{wide_bold}{wide_bold}:{wide_bold}{wide_bold}"
        best = ""
        best_w = -1
        for day in range(7):
            weekday = datetime(2026, 11, 16 + day).strftime("%A")
            for month in range(1, 13):
                month_name = datetime(2026, month, 1).strftime("%B")
                text = f"{weekday}, {wide_digit}{wide_digit} {month_name} {wide_digit * 4}"
                width = normal.measure(text)
                if width > best_w:
                    best, best_w = text, width
        self._date_sample = best
        probe = tk.Label(
            self.root,
            text=self._time_sample,
            font=bold,
            bd=0,
            highlightthickness=0,
            padx=0,
            pady=0,
        )
        probe.update_idletasks()
        self._label_extra_w = max(0, probe.winfo_reqwidth() - bold.measure(self._time_sample))
        self._label_extra_h = max(0, probe.winfo_reqheight() - bold.metrics("linespace"))
        probe.destroy()

    def _typeface(self, size, weight):
        """One cached Segoe face. ``size`` is in points."""
        key = (size, weight)
        font = self._typefaces.get(key)
        if font is None:
            font = tkfont.Font(root=self.root, family="Segoe UI", size=size, weight=weight)
            self._typefaces[key] = font
        return font

    def _line_height(self, size, weight):
        """Pixel height of one label at this size, including the label's own pad."""
        return self._typeface(size, weight).metrics("linespace") + self._label_extra_h

    def _max_point(self, weight, text, limit, lo, hi):
        """Largest point size in ``lo``..``hi`` whose text is no wider than ``limit``."""
        best = lo
        while lo <= hi:
            mid = (lo + hi) // 2
            if self._typeface(mid, weight).measure(text) <= limit:
                best = mid
                lo = mid + 1
            else:
                hi = mid - 1
        return best

    def _chrome_height(self):
        """Header, settings, and slider rows. The face uses whatever is left."""
        height = self.header.winfo_reqheight()
        if self._settings_open:
            height += self.settings.winfo_reqheight()
        if self.show_glass_var.get():
            height += self.glass_row.winfo_reqheight()
        if self.show_font_var.get():
            height += self.font_row.winfo_reqheight()
        return height

    def _minimum_size(self):
        """Smallest window that still shows the full time, the full date, and the open chrome."""
        self.root.update_idletasks()
        text_limit_pad = self._label_extra_w
        time_w = self._typeface(MIN_TIME_PT, "bold").measure(self._time_sample) + text_limit_pad
        date_w = self._typeface(MIN_DATE_PT, "normal").measure(self._date_sample) + text_limit_pad
        width = max(time_w, date_w) + FACE_PAD * 2
        for row in (self.glass_row, self.font_row):
            label = row.winfo_children()[0]
            slider_w = GRIP_SIZE + 4 + label.winfo_reqwidth() + SLIDER_MIN_TROUGH + GRIP_SIZE
            width = max(width, slider_w)
        if self._settings_open:
            width = max(width, self.settings.winfo_reqwidth())
        height = self._chrome_height()
        height += self._line_height(MIN_TIME_PT, "bold") + DATE_GAP
        height += self._line_height(MIN_DATE_PT, "normal")
        return width, height

    def _apply_minsize(self):
        """Publish the floor, and grow the window if it is already below that floor."""
        width, height = self._minimum_size()
        self._min_w = width
        self._min_h = height
        self.root.minsize(width, height)
        current_w = self.root.winfo_width()
        current_h = self.root.winfo_height()
        if current_w > 1 and (current_w < width or current_h < height):
            self.root.geometry(
                f"{max(current_w, width)}x{max(current_h, height)}"
                f"+{self.root.winfo_x()}+{self.root.winfo_y()}"
            )
            self._sync_glass()

    def _pick_sizes(self, body_w, body_h):
        """Largest time and date that fit. The date never drops a character."""
        text_w = body_w - self._label_extra_w
        if text_w < 1 or body_h < 1:
            return MIN_TIME_PT, MIN_DATE_PT
        lo, hi = MIN_TIME_PT, 240
        best_time, best_date = MIN_TIME_PT, MIN_DATE_PT
        while lo <= hi:
            mid = (lo + hi) // 2
            date_cap = max(MIN_DATE_PT, int(mid * DATE_RATIO))
            date_size = self._max_point("normal", self._date_sample, text_w, MIN_DATE_PT, date_cap)
            time_fits = self._typeface(mid, "bold").measure(self._time_sample) <= text_w
            date_fits = self._typeface(date_size, "normal").measure(self._date_sample) <= text_w
            needed = self._line_height(mid, "bold") + DATE_GAP + self._line_height(date_size, "normal")
            if time_fits and date_fits and needed <= body_h:
                best_time, best_date = mid, date_size
                lo = mid + 1
            else:
                hi = mid - 1
        return best_time, best_date

    def _fit_fonts(self, width, height):
        """Pick time and date sizes that fit the current window.

        The face keeps the full time and the longest date the locale can
        produce. Extra room grows the time, and the date follows at about a
        third of that size.
        """
        if width < 20 or height < 20:
            return
        body_h = height - self._chrome_height()
        body_w = width - FACE_PAD * 2
        time_size, date_size = self._pick_sizes(body_w, body_h)
        # Skip the widget update when the size is unchanged. Configure fires
        # again after a font change, and this stops that loop.
        if time_size == self._time_size and date_size == self._date_size:
            return
        self._time_size = time_size
        self._date_size = date_size
        self.time_lbl.config(font=("Segoe UI", time_size, "bold"))
        self.date_lbl.config(font=("Segoe UI", date_size))

    def _set_glass(self, value):
        """Glass scale callback. ``value`` arrives as a string."""
        self._apply_glass(int(float(value)))

    def _set_font(self, value):
        """Font scale callback. ``value`` arrives as a string."""
        self._apply_font(int(float(value)))

    def _apply_glass(self, percent):
        """Fade only the plate. 0 is solid. 100 is visually clear.

        Alpha never reaches 0. A layered window at 0 stops receiving clicks,
        and the plate would no longer move or resize.
        """
        if percent >= 100:
            alpha = GLASS_ALPHA_FLOOR
        else:
            alpha = max(GLASS_ALPHA_FLOOR, 1.0 - (percent / 100.0))
        self.glass.attributes("-alpha", alpha)
        self._no_activate(self.glass)

    def _rgb(self, hex_color):
        """Turn ``#rrggbb`` into a 0–255 triple."""
        color = hex_color.lstrip("#")
        return tuple(int(color[i:i + 2], 16) for i in (0, 2, 4))

    def _shade(self, hex_color, brightness):
        """Blend a digit color toward the color key.

        100 returns the original color. 0 returns BG, which the color key
        removes, so the glyph disappears without fading the rest of the window.
        """
        level = max(0, min(100, int(brightness))) / 100.0
        if level >= 1:
            return hex_color
        src = self._rgb(hex_color)
        base = self._rgb(BG)
        mixed = tuple(int(round(base[i] + (src[i] - base[i]) * level)) for i in range(3))
        return "#{:02x}{:02x}{:02x}".format(*mixed)

    def _apply_font(self, percent):
        """Set time and date brightness. Controls keep their own colors."""
        self.time_lbl.configure(fg=self._shade(FG, percent))
        self.date_lbl.configure(fg=self._shade(MUTED, percent))

    def _tick(self):
        """Refresh the labels four times a second and schedule the next pass."""
        now = datetime.now()
        self.time_lbl.config(text=now.strftime("%H:%M:%S"))
        self.date_lbl.config(text=now.strftime("%A, %d %B %Y"))
        self._sync_glass()
        self.root.after(200, self._tick)

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    ClockApp().run()
