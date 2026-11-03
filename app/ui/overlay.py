"""Small, always-on-top status pill anchored above the Windows taskbar."""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from queue import Empty, SimpleQueue


class StatusOverlay:
    def __init__(self, on_quit: Callable[[], None]) -> None:
        self.root = tk.Tk()
        self.root.title("Echo")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg="#111827")
        self.root.resizable(False, False)
        self._on_quit = on_quit
        self._updates: SimpleQueue[tuple[str, str]] = SimpleQueue()

        width, height = 142, 38
        x = self.root.winfo_screenwidth() - width - 18
        y = self.root.winfo_screenheight() - height - 55
        self.root.geometry(f"{width}x{height}+{x}+{y}")

        self.canvas = tk.Canvas(
            self.root, width=width, height=height, bg="#111827", highlightthickness=0
        )
        self.canvas.pack(fill="both", expand=True)
        self.canvas.create_rectangle(
            1, 1, width - 1, height - 1, fill="#182235", outline="#26344d"
        )
        self.dot = self.canvas.create_oval(
            13, 14, 23, 24, fill="#6b7280", outline=""
        )
        self.label = self.canvas.create_text(
            32, 19, anchor="w", text="Echo · Starting",
            fill="#e5edf8", font=("Segoe UI", 9, "bold")
        )

        self.menu = tk.Menu(self.root, tearoff=False)
        self.menu.add_command(label="Quit Echo", command=self._quit)
        self.canvas.bind("<Button-3>", self._show_menu)
        self.root.bind("<Escape>", lambda _event: self._quit())
        self.root.after(100, self._drain_updates)

    def _show_menu(self, event: tk.Event) -> None:
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()

    def _quit(self) -> None:
        self._on_quit()
        self.root.destroy()

    def set_status(self, text: str, state: str = "idle") -> None:
        # Worker threads enqueue updates; Tk widgets stay on the UI thread.
        self._updates.put((text, state))

    def _drain_updates(self) -> None:
        latest: tuple[str, str] | None = None
        try:
            while True:
                latest = self._updates.get_nowait()
        except Empty:
            pass

        if latest is not None:
            self._apply_status(*latest)
        if self.root.winfo_exists():
            self.root.after(100, self._drain_updates)

    def _apply_status(self, text: str, state: str) -> None:
        colors = {
            "idle": "#34d399",
            "listening": "#38bdf8",
            "working": "#fbbf24",
            "error": "#fb7185",
            "off": "#6b7280",
        }
        color = colors.get(state, colors["idle"])
        self.canvas.itemconfigure(self.dot, fill=color)
        self.canvas.itemconfigure(self.label, text=f"Echo · {text[:18]}")

    def run(self) -> None:
        self.root.mainloop()
