"""Tkinter control window."""

from __future__ import annotations

import os
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, font as tkfont, messagebox, ttk

from .app import ReceiverApp, Settings, Status
from .files import Transfer
from .player import list_output_devices

REFRESH_MS = 250
GREEN, AMBER, GREY, RED = "#1a7f37", "#9a6700", "#6e7781", "#cf222e"
BRAND_BLUE = "#2563EB"


def _fmt_ms(value: float | None, digits: int = 0) -> str:
    return "–" if value is None else f"{value:.{digits}f} ms"


def _fmt_size(n: float) -> str:
    for unit in ("B", "KB", "MB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def _open_path(path: Path) -> None:
    if sys.platform == "win32":
        os.startfile(path)
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)])


def _transfer_status(t: Transfer) -> str:
    if t.status == "transferring":
        return f"{t.progress * 100:.0f} %"
    if t.status == "queued":
        return "Waiting for phone" if t.direction == "out" else "Queued"
    if t.status == "done":
        return "Received" if t.direction == "in" else "Sent"
    return "Failed"


class ReceiverWindow:
    def __init__(self, root: tk.Tk, app: ReceiverApp, settings: Settings):
        self.root = root
        self.app = app
        self.settings = settings
        root.title("Airwire Receiver")
        root.minsize(760, 0)
        root.resizable(False, False)
        style = ttk.Style(root)
        if sys.platform == "darwin" and "aqua" in style.theme_names():
            style.theme_use("aqua")
            style.configure("Big.TLabel", font=("Helvetica Neue", 13, "bold"))
            style.configure("Value.TLabel", font=("Helvetica Neue", 11))
            style.configure("Key.TLabel", foreground=GREY, font=("Helvetica Neue", 10))
            style.configure("Brand.TLabel", font=("Helvetica Neue", 16, "bold"), foreground=BRAND_BLUE)
            style.configure("Code.TLabel", font=("Menlo", 22, "bold"), foreground=BRAND_BLUE)
        elif sys.platform != "win32":
            # Linux and others: Segoe UI and Consolas aren't there, and the default Tk
            # theme looks dated. Use clam with the desktop's own fonts.
            if "clam" in style.theme_names():
                style.theme_use("clam")
            sans = tkfont.nametofont("TkDefaultFont").actual("family")
            mono = tkfont.nametofont("TkFixedFont").actual("family")
            style.configure("Big.TLabel", font=(sans, 13, "bold"))
            style.configure("Value.TLabel", font=(sans, 10))
            style.configure("Key.TLabel", foreground=GREY)
            style.configure("Brand.TLabel", font=(sans, 16, "bold"), foreground=BRAND_BLUE)
            style.configure("Code.TLabel", font=(mono, 22, "bold"), foreground=BRAND_BLUE)
        else:
            if "vista" in style.theme_names():
                style.theme_use("vista")
            style.configure("Big.TLabel", font=("Segoe UI Semibold", 13))
            style.configure("Value.TLabel", font=("Segoe UI", 10))
            style.configure("Key.TLabel", foreground=GREY)
            style.configure("Brand.TLabel", font=("Segoe UI Semibold", 16), foreground=BRAND_BLUE)
            style.configure("Code.TLabel", font=("Consolas", 22, "bold"), foreground=BRAND_BLUE)

        body = ttk.Frame(root, padding=14)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)

        brand = ttk.Frame(body)
        brand.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        ttk.Label(brand, text="Airwire", style="Brand.TLabel").pack(anchor="w")
        ttk.Label(brand, text="The audio cable, minus the cable.", style="Key.TLabel").pack(anchor="w")

        # Server -----------------------------------------------------------------
        server = ttk.LabelFrame(body, text="Server", padding=(10, 6))
        server.grid(row=1, column=0, sticky="ew")
        self.listen_var = tk.StringVar()
        self.ips_var = tk.StringVar()
        ttk.Label(server, textvariable=self.listen_var, style="Value.TLabel").pack(anchor="w")
        ttk.Label(server, textvariable=self.ips_var, style="Key.TLabel").pack(anchor="w")

        # Connection -------------------------------------------------------------
        conn = ttk.LabelFrame(body, text="Connection", padding=(10, 6))
        conn.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        self.state_label = ttk.Label(conn, style="Big.TLabel")
        self.state_label.pack(anchor="w")
        self.sender_var = tk.StringVar()
        self.source_var = tk.StringVar()
        ttk.Label(conn, textvariable=self.sender_var, style="Value.TLabel").pack(anchor="w")
        ttk.Label(conn, textvariable=self.source_var, style="Key.TLabel").pack(anchor="w")

        # Stream health ----------------------------------------------------------
        health = ttk.LabelFrame(body, text="Stream", padding=(10, 6))
        health.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        health.columnconfigure(1, weight=1)
        self.metrics: dict[str, tk.StringVar] = {}
        for row, key in enumerate(["Est. latency", "Buffer", "Jitter", "Packet loss", "RTT",
                                   "Recovered / concealed", "Bitrate in"]):
            ttk.Label(health, text=key, style="Key.TLabel").grid(row=row, column=0, sticky="w")
            var = tk.StringVar(value="–")
            ttk.Label(health, textvariable=var, style="Value.TLabel").grid(
                row=row, column=1, sticky="e", padx=(16, 0))
            self.metrics[key] = var

        # Output -----------------------------------------------------------------
        out = ttk.LabelFrame(body, text="Output", padding=(10, 6))
        out.grid(row=4, column=0, sticky="ew", pady=(10, 0))
        out.columnconfigure(1, weight=1)
        ttk.Label(out, text="Device", style="Key.TLabel").grid(row=0, column=0, sticky="w")
        self.devices = list_output_devices()
        self.device_var = tk.StringVar()
        combo = ttk.Combobox(out, textvariable=self.device_var, state="readonly", width=36,
                             values=[d.name for d in self.devices])
        combo.grid(row=0, column=1, sticky="ew", padx=(10, 0))
        combo.bind("<<ComboboxSelected>>", self._on_device)
        self.device_var.set(app.output_device_name())

        ttk.Label(out, text="Volume", style="Key.TLabel").grid(row=1, column=0, sticky="w",
                                                              pady=(8, 0))
        vol_row = ttk.Frame(out)
        vol_row.grid(row=1, column=1, sticky="ew", padx=(10, 0), pady=(8, 0))
        vol_row.columnconfigure(0, weight=1)
        self.volume_var = tk.DoubleVar(value=settings.volume)
        self.volume_text = tk.StringVar()
        scale = ttk.Scale(vol_row, from_=0, to=100, variable=self.volume_var,
                          command=self._on_volume)
        scale.grid(row=0, column=0, sticky="ew")
        scale.bind("<ButtonRelease-1>", lambda _e: self.settings.save())
        ttk.Label(vol_row, textvariable=self.volume_text, width=5, anchor="e").grid(
            row=0, column=1, padx=(8, 0))
        self.output_note = ttk.Label(out, style="Key.TLabel")
        self.output_note.grid(row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))
        self._on_volume(settings.volume)

        # PC audio -> phone --------------------------------------------------------
        listen = ttk.LabelFrame(body, text="Play this PC on your phone", padding=(10, 6))
        listen.grid(row=5, column=0, sticky="ew", pady=(10, 0))
        listen.columnconfigure(0, weight=1)
        self.allow_listen_var = tk.BooleanVar(value=settings.allow_listen)
        ttk.Checkbutton(listen, text="Let paired phones play what this PC is playing",
                        variable=self.allow_listen_var, command=self._on_allow_listen).grid(
            row=0, column=0, columnspan=2, sticky="w")
        self.listeners_label = ttk.Label(listen, style="Value.TLabel", wraplength=360,
                                         justify="left")
        self.listeners_label.grid(row=1, column=0, sticky="w", pady=(4, 0))
        self.stop_listen_btn = ttk.Button(listen, text="Stop", command=self.app.broadcast.stop_all)
        self.stop_listen_btn.grid(row=1, column=1, sticky="e", padx=(8, 0), pady=(4, 0))

        # Files (right column) -----------------------------------------------------
        body.columnconfigure(1, weight=1)
        self._build_files_panel(body)

        root.protocol("WM_DELETE_WINDOW", self.close)
        if sys.platform == "darwin":
            root.bind("<Command-q>", lambda _e: self.close())
            root.bind("<Command-w>", lambda _e: self.close())
        self._refresh()

    def _build_files_panel(self, body: ttk.Frame) -> None:
        panel = ttk.LabelFrame(body, text="Files", padding=(10, 6))
        panel.grid(row=1, column=1, rowspan=5, sticky="nsew", padx=(12, 0))
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(4, weight=1)
        self._transfers: dict[str, Transfer] = {}
        if self.app.files is None:
            ttk.Label(panel, text="File transfer is disabled (--no-files).",
                      style="Key.TLabel").grid(row=0, column=0, sticky="w")
            return

        ttk.Label(panel, text="Pairing code", style="Key.TLabel").grid(row=0, column=0, sticky="w")
        code_row = ttk.Frame(panel)
        code_row.grid(row=1, column=0, sticky="ew")
        code_row.columnconfigure(0, weight=1)
        self.code_var = tk.StringVar()
        ttk.Label(code_row, textvariable=self.code_var, style="Code.TLabel").grid(
            row=0, column=0, sticky="w")
        ttk.Button(code_row, text="Reset pairing", command=self._reset_pairing).grid(
            row=0, column=1, sticky="e")
        self.paired_var = tk.StringVar()
        ttk.Label(panel, textvariable=self.paired_var, style="Key.TLabel", wraplength=330,
                  justify="left").grid(row=2, column=0, sticky="w", pady=(0, 8))

        buttons = ttk.Frame(panel)
        buttons.grid(row=3, column=0, sticky="ew", pady=(0, 6))
        ttk.Button(buttons, text="Send files to phone…", command=self._send_files).pack(side="left")
        ttk.Button(buttons, text="Open received folder", command=self._open_folder).pack(
            side="left", padx=(6, 0))

        tree = ttk.Treeview(panel, columns=("dir", "size", "status"), height=9,
                            selectmode="browse")
        tree.heading("#0", text="File", anchor="w")
        tree.heading("dir", text="")
        tree.heading("size", text="Size", anchor="e")
        tree.heading("status", text="Status", anchor="w")
        tree.column("#0", width=170, stretch=True)
        tree.column("dir", width=28, anchor="center", stretch=False)
        tree.column("size", width=70, anchor="e", stretch=False)
        tree.column("status", width=110, anchor="w", stretch=False)
        tree.grid(row=4, column=0, sticky="nsew")
        tree.bind("<Double-1>", self._open_selected)
        self.files_tree = tree

        ttk.Label(panel, text=f"Received files are saved to {self.app.files.files_dir}",
                  style="Key.TLabel", wraplength=330, justify="left").grid(
            row=5, column=0, sticky="w", pady=(6, 0))

    def _reset_pairing(self) -> None:
        if messagebox.askyesno("Reset pairing", "Paired phones will need the new code to "
                                                "transfer files or play this PC's audio. "
                                                "Reset now?", parent=self.root):
            self.app.files.pairing.reset()

    def _send_files(self) -> None:
        paths = filedialog.askopenfilenames(parent=self.root, title="Send files to phone")
        if not paths:
            return
        self.app.files.send_to_phone(list(paths))
        if not self.app.files.pairing.devices:
            messagebox.showinfo("Pair your phone", "The files are waiting here. On the phone, "
                                "open Airwire, go to Files and enter the pairing code; they "
                                "download automatically.", parent=self.root)

    def _open_folder(self) -> None:
        folder = self.app.files.files_dir
        folder.mkdir(parents=True, exist_ok=True)
        _open_path(folder)

    def _open_selected(self, _event=None) -> None:
        sel = self.files_tree.selection()
        t = self._transfers.get(sel[0]) if sel else None
        if t and t.status == "done" and t.path and Path(t.path).exists():
            _open_path(Path(t.path))

    def _render_files(self) -> None:
        files = self.app.files
        if files is None:
            return
        code = files.pairing.code
        self.code_var.set(f"{code[:3]} {code[3:]}")
        names = files.pairing.device_names()
        self.paired_var.set(("Paired: " + ", ".join(names)) if names else
                            "No phone paired yet. In the Airwire app open Files and enter "
                            "this code.")
        tree = self.files_tree
        current = files.transfers()
        self._transfers = {t.id: t for t in current}
        for t in current:
            values = ("↓" if t.direction == "in" else "↑", _fmt_size(t.size),
                      _transfer_status(t))
            if tree.exists(t.id):
                tree.item(t.id, text=t.name, values=values)
            else:
                tree.insert("", 0, iid=t.id, text=t.name, values=values)
        for iid in tree.get_children():
            if iid not in self._transfers:
                tree.delete(iid)

    def _on_device(self, _event=None) -> None:
        name = self.device_var.get()
        device = next((d for d in self.devices if d.name == name), None)
        if device is None:
            return
        self.app.set_device(device.index)
        self.settings.device_name = name
        self.settings.save()

    def _on_volume(self, value) -> None:
        pct = round(float(value))
        self.app.player.volume = pct / 100
        self.settings.volume = pct
        self.volume_text.set(f"{pct} %")

    def _on_allow_listen(self) -> None:
        self.app.set_allow_listen(self.allow_listen_var.get())

    def _refresh(self) -> None:
        self._render(self.app.status())
        self._render_files()
        self.root.after(REFRESH_MS, self._refresh)

    def _render(self, s: Status) -> None:
        self.listen_var.set(f"Listening on {s.listening}")
        host_prefix = "This Mac: " if sys.platform == "darwin" else "This PC: "
        self.ips_var.set(host_prefix + (", ".join(s.local_ips) or "no LAN address found"))
        if s.connected and s.playing:
            self.state_label.configure(text="● Streaming", foreground=GREEN)
        elif s.connected and s.silent_s >= 1:
            self.state_label.configure(
                text=f"● Connected – no audio for {s.silent_s:.0f} s (Wi-Fi busy?)",
                foreground=AMBER)
        elif s.connected:
            self.state_label.configure(text="● Connected – buffering", foreground=AMBER)
        else:
            self.state_label.configure(text="○ Disconnected – waiting for sender",
                                       foreground=GREY)
        if s.connected:
            name = s.sender_name or "Unknown device"
            self.sender_var.set(f"{name}  ·  {s.sender_addr}")
            parts = [s.source, f"{s.bitrate_kbps} kbps" if s.bitrate_kbps else "",
                     {1: "mono", 2: "stereo"}.get(s.channels, "")]
            self.source_var.set("  ·  ".join(p for p in parts if p))
        else:
            self.sender_var.set("Open the Android app and tap START STREAMING.")
            self.source_var.set("")

        m = self.metrics
        m["Est. latency"].set(_fmt_ms(s.est_latency_ms))
        m["Buffer"].set(f"{s.buffer_ms:.0f} ms (target {s.target_ms:.0f})" if s.connected else "–")
        m["Jitter"].set(_fmt_ms(s.jitter_ms, 1) if s.connected else "–")
        m["Packet loss"].set(f"{s.loss_pct:.1f} %" if s.connected else "–")
        m["RTT"].set(_fmt_ms(s.rtt_ms, 1))
        m["Recovered / concealed"].set(f"{s.recovered} / {s.concealed}")
        m["Bitrate in"].set(f"{s.kbps:.0f} kbps" if s.connected else "–")

        if s.output_error:
            self.output_note.configure(text=f"Output error: {s.output_error}", foreground=RED)
        else:
            self.output_note.configure(text=f"Output latency {s.output_latency_ms:.0f} ms",
                                       foreground=GREY)

        if s.listeners:
            lines = [f"▶ {l.device}  ·  {l.bitrate_kbps} kbps  ·  loss "
                     f"{l.loss_permille / 10:.1f} %  ·  buffer {l.buffer_ms} ms"
                     for l in s.listeners]
            self.listeners_label.configure(text="\n".join(lines), foreground=GREEN)
            self.stop_listen_btn.state(["!disabled"])
        else:
            if s.capture_error:
                text, color = f"Can't capture this PC's audio: {s.capture_error}", RED
            elif s.allow_listen:
                text, color = "No phone is listening. In the app choose PC → Phone.", GREY
            else:
                text, color = "Off.", GREY
            self.listeners_label.configure(text=text, foreground=color)
            self.stop_listen_btn.state(["disabled"])

    def close(self) -> None:
        self.settings.save()
        self.app.stop()
        self.root.destroy()


def run(app: ReceiverApp, settings: Settings) -> None:
    root = tk.Tk()
    ReceiverWindow(root, app, settings)
    root.mainloop()
