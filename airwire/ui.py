"""Tkinter control window."""

from __future__ import annotations

import sys
import tkinter as tk
from tkinter import ttk

from .app import ReceiverApp, Settings, Status
from .player import list_output_devices

REFRESH_MS = 250
GREEN, AMBER, GREY, RED = "#1a7f37", "#9a6700", "#6e7781", "#cf222e"
BRAND_BLUE = "#2563EB"


def _fmt_ms(value: float | None, digits: int = 0) -> str:
    return "–" if value is None else f"{value:.{digits}f} ms"


class ReceiverWindow:
    def __init__(self, root: tk.Tk, app: ReceiverApp, settings: Settings):
        self.root = root
        self.app = app
        self.settings = settings
        root.title("Airwire Receiver")
        root.minsize(380, 0)
        root.resizable(False, False)
        style = ttk.Style(root)
        if sys.platform == "darwin" and "aqua" in style.theme_names():
            style.theme_use("aqua")
            style.configure("Big.TLabel", font=("Helvetica Neue", 13, "bold"))
            style.configure("Value.TLabel", font=("Helvetica Neue", 11))
            style.configure("Key.TLabel", foreground=GREY, font=("Helvetica Neue", 10))
            style.configure("Brand.TLabel", font=("Helvetica Neue", 16, "bold"), foreground=BRAND_BLUE)
        else:
            if "vista" in style.theme_names():
                style.theme_use("vista")
            style.configure("Big.TLabel", font=("Segoe UI Semibold", 13))
            style.configure("Value.TLabel", font=("Segoe UI", 10))
            style.configure("Key.TLabel", foreground=GREY)
            style.configure("Brand.TLabel", font=("Segoe UI Semibold", 16), foreground=BRAND_BLUE)

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

        root.protocol("WM_DELETE_WINDOW", self.close)
        if sys.platform == "darwin":
            root.bind("<Command-q>", lambda _e: self.close())
            root.bind("<Command-w>", lambda _e: self.close())
        self._refresh()

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

    def _refresh(self) -> None:
        self._render(self.app.status())
        self.root.after(REFRESH_MS, self._refresh)

    def _render(self, s: Status) -> None:
        self.listen_var.set(f"Listening on {s.listening}")
        host_prefix = "This Mac: " if sys.platform == "darwin" else "This PC: "
        self.ips_var.set(host_prefix + (", ".join(s.local_ips) or "no LAN address found"))
        if s.connected and s.playing:
            self.state_label.configure(text="● Streaming", foreground=GREEN)
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

    def close(self) -> None:
        self.settings.save()
        self.app.stop()
        self.root.destroy()


def run(app: ReceiverApp, settings: Settings) -> None:
    root = tk.Tk()
    ReceiverWindow(root, app, settings)
    root.mainloop()
