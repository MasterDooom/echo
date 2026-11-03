from __future__ import annotations

import ctypes
import math
import queue
import tkinter as tk
from ctypes import wintypes


class _RECT(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


def _work_area() -> tuple[int, int]:
    try:
        rect = _RECT()
        ok = ctypes.windll.user32.SystemParametersInfoW(
            0x0030, 0, ctypes.byref(rect), 0
        )
        if ok:
            return int(rect.right), int(rect.bottom)
    except Exception:
        pass
    try:
        user32 = ctypes.windll.user32
        return int(user32.GetSystemMetrics(0)), int(user32.GetSystemMetrics(1))
    except Exception:
        return 1920, 1080


class EchoOverlay:
    """Small animated orb with an expanded status and clarification panel."""

    COLORS = {
        "loading": "#FBBF24",
        "listening": "#34D399",
        "awake": "#67E8F9",
        "working": "#60A5FA",
        "privacy": "#64748B",
        "paused": "#64748B",
        "error": "#F87171",
    }

    def __init__(self, events: queue.Queue, on_toggle, on_quit) -> None:
        self.events = events
        self.on_toggle = on_toggle
        self.on_quit = on_quit
        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-toolwindow", True)
        self.transparent = "#010203"
        try:
            self.root.configure(bg=self.transparent)
            self.root.attributes("-transparentcolor", self.transparent)
        except tk.TclError:
            self.root.attributes("-alpha", 0.94)
        self.canvas = tk.Canvas(
            self.root, bg=self.transparent, highlightthickness=0, bd=0
        )
        self.canvas.pack(fill="both", expand=True)
        self.status = "loading"
        self.message = "Starting up"
        self.expanded = False
        self.phase = 0.0
        self.width, self.height = 58, 58
        self._place()
        self.root.bind("<Button-1>", self._click)
        self.root.bind("<Button-3>", self._menu)
        self.root.bind("<Escape>", lambda _event: self._collapse())
        self.root.protocol("WM_DELETE_WINDOW", self._quit)
        self.root.after(100, self._tick)
        self.root.after(100, self._drain_events)
        self._draw()

    def _place(self) -> None:
        right, bottom = _work_area()
        x = right - self.width - 12
        y = bottom - self.height - 10
        self.root.geometry(f"{self.width}x{self.height}+{x}+{y}")

    def _draw(self) -> None:
        self.canvas.delete("all")
        color = self.COLORS.get(self.status, self.COLORS["listening"])
        if not self.expanded:
            self.canvas.create_oval(
                5, 5, 53, 53, fill="#0B1220", outline="#243247", width=1
            )
            pulse = (
                3.0 * (0.5 + 0.5 * math.sin(self.phase))
                if self.status == "listening"
                else 0
            )
            self.canvas.create_oval(
                17 - pulse, 17 - pulse, 41 + pulse, 41 + pulse,
                fill="#132D36", outline=""
            )
            self.canvas.create_oval(20, 20, 38, 38, fill=color, outline="")
            return

        self.canvas.create_rectangle(
            25, 4, self.width - 25, self.height - 4,
            fill="#101827", outline="#263449"
        )
        self.canvas.create_oval(
            4, 4, 46, self.height - 4, fill="#101827", outline="#263449"
        )
        self.canvas.create_oval(
            self.width - 46, 4, self.width - 4, self.height - 4,
            fill="#101827", outline="#263449"
        )
        self.canvas.create_oval(17, 24, 33, 40, fill=color, outline="")
        self.canvas.create_text(
            55, 23, anchor="w", text="ECHO",
            fill="#E5EEF8", font=("Segoe UI", 9, "bold")
        )
        labels = {
            "loading": "STARTING",
            "listening": "LISTENING",
            "awake": "HEARD YOU",
            "working": "WORKING",
            "privacy": "MICROPHONE OFF",
            "paused": "MICROPHONE OFF",
            "error": "CHECK SETUP",
        }
        label = labels.get(self.status, "LISTENING")
        self.canvas.create_text(
            55, 41, anchor="w", text=label,
            fill="#91A4BA", font=("Segoe UI", 8)
        )
        if self.message:
            short_message = self.message[:140] + ("…" if len(self.message) > 140 else "")
            self.canvas.create_text(
                55, 52, anchor="nw", text=short_message,
                width=self.width - 125,
                fill="#C0CDDC", font=("Segoe UI", 7)
            )
        enabled = self.status not in {"privacy", "paused", "error"}
        self.canvas.create_text(
            self.width - 34, self.height // 2,
            text="ON" if enabled else "OFF",
            fill=color, font=("Segoe UI", 8, "bold")
        )

    def _click(self, event) -> None:
        if self.expanded and event.x >= self.width - 58:
            self.on_toggle()
        elif self.expanded:
            self._collapse()
        else:
            self.expanded = True
            self.width, self.height = 330, 86
            self._place()
        self._draw()

    def _collapse(self) -> None:
        self.expanded = False
        self.width, self.height = 58, 58
        self._place()
        self._draw()

    def _menu(self, _event) -> None:
        menu = tk.Menu(self.root, tearoff=False)
        if self.status in {"privacy", "paused"}:
            menu.add_command(label="Enable microphone", command=self.on_toggle)
        else:
            menu.add_command(
                label="Privacy mode · release microphone",
                command=self.on_toggle,
            )
        menu.add_separator()
        menu.add_command(label="Quit Echo", command=self._quit)
        menu.tk_popup(self.root.winfo_pointerx(), self.root.winfo_pointery())

    def _drain_events(self) -> None:
        changed = False
        while True:
            try:
                kind, value = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "status":
                self.status = value
                changed = True
            elif kind == "message":
                self.message = value
                if self.expanded:
                    changed = True
        if changed:
            self._draw()
        self.root.after(100, self._drain_events)

    def _tick(self) -> None:
        self.phase += 0.22
        if not self.expanded and self.status == "listening":
            self._draw()
        self.root.after(100, self._tick)

    def _quit(self) -> None:
        self.on_quit()
        self.root.quit()

    def run(self) -> None:
        self.root.mainloop()
