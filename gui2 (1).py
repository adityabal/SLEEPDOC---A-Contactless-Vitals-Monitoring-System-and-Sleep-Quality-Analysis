# GUI for Sleep Doc — full dashboard
import customtkinter as ctk
import tkinter as tk
from tkinter import messagebox, filedialog
from PIL import Image, ImageTk, ImageDraw, ImageFont
import os, subprocess, signal, sys
import json
import requests
from urllib.parse import quote
from datetime import datetime, timedelta
import threading
import time
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import numpy as np
import csv
import glob
import socket
import subprocess

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")

# ── Deep Sleep Color Palette ──────────────────────────────────────────────────
C_BG          = "#09091e"
C_SIDEBAR     = "#10102b"
C_HEADER      = "#0e0d26"
C_CARD        = "#16153a"
C_CARD2       = "#1e1d47"
C_BORDER      = "#2e2b6e"
C_ACCENT      = "#a78bfa"
C_ACCENT2     = "#7c3aed"
C_ACCENT_HOV  = "#6d28d9"
C_HR          = "#f472b6"
C_BR          = "#38bdf8"
C_TEMP        = "#fb923c"
C_HUM         = "#34d399"
C_PRESS       = "#a78bfa"
C_TEXT        = "#e2e8f0"
C_MUTED       = "#6b7280"
C_NAV         = "#1e1d47"
C_NAV_HOV     = "#2e2b6e"
C_SUCCESS     = "#4ade80"
C_WARN        = "#fbbf24"
C_DANGER      = "#f87171"

# ── App constants ─────────────────────────────────────────────────────────────
FLASK_PORT       = 5000
QR_PATH          = "/home/raspberry/Desktop/SLEEPDOC/qr_code.png"


vitals_process = None
monitoring = False


def get_local_ip():
    """Get the Pi's current WiFi IP. Reads wlan0 directly first — works with
    no internet/gateway, e.g. when the Pi is its own standalone access point."""
    try:
        out = subprocess.check_output(
            ["ip", "-4", "-o", "addr", "show", "wlan0"],
            stderr=subprocess.DEVNULL
        ).decode()
        return out.split("inet ")[1].split("/")[0]
    except Exception:
        pass
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip


def _touch_scroll_bind_tree(canvas, widget):
    """Make finger-drag scrolling work on a CTkScrollableFrame.

    CTkScrollableFrame only reacts to mouse-wheel events and dragging the
    thin scrollbar — a touchscreen swipe generates neither, it just looks
    like a click-drag on whatever widget is under the finger. This recurses
    through `widget` and every descendant and binds press/drag handlers that
    scroll `canvas` instead, so swiping anywhere on the page works. Nested
    CTkScrollableFrames (e.g. a scrollable list inside a scrollable page) get
    their own independent binding to their own canvas rather than being
    bound to the outer one.
    """
    state = {"y": None}

    def on_press(event):
        state["y"] = event.y_root

    def on_drag(event):
        if state["y"] is None:
            state["y"] = event.y_root
            return
        dy = state["y"] - event.y_root
        if abs(dy) >= 6:
            canvas.yview_scroll(int(dy / 6), "units")
            state["y"] = event.y_root

    def bind_one(w):
        if isinstance(w, ctk.CTkScrollableFrame):
            _touch_scroll_bind_tree(w._parent_canvas, w)
            return
        try:
            w.bind("<ButtonPress-1>", on_press, add="+")
            w.bind("<B1-Motion>", on_drag, add="+")
        except Exception:
            pass
        for child in w.winfo_children():
            bind_one(child)

    try:
        widget.bind("<ButtonPress-1>", on_press, add="+")
        widget.bind("<B1-Motion>", on_drag, add="+")
    except Exception:
        pass
    for child in widget.winfo_children():
        bind_one(child)


def load_icon(path, size, fallback_text="IMG"):
    try:
        image = Image.open(path).resize(size, Image.LANCZOS)
        return ctk.CTkImage(light_image=image, dark_image=image, size=size)
    except:
        placeholder = Image.new('RGBA', size, (64, 64, 64, 255))
        d = ImageDraw.Draw(placeholder)
        try:
            font = ImageFont.truetype("arial.ttf", size=size[0] // 5)
        except:
            font = None
        d.text((size[0] // 4, size[1] // 4), fallback_text, fill=(255, 255, 255), font=font)
        return ctk.CTkImage(light_image=placeholder, dark_image=placeholder, size=size)


def create_light_icon(color_hex, size=(50, 50)):
    import math
    img = Image.new('RGBA', size, (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    center_x, center_y = size[0]//2, size[1]//2
    d.ellipse([center_x-15, center_y-20, center_x+15, center_y+10],
              fill=color_hex, outline='white', width=2)
    d.rectangle([center_x-8, center_y+8, center_x+8, center_y+15],
                fill='gray', outline='white', width=1)
    for angle in [0, 45, 90, 135, 180, 225, 270, 315]:
        rad = math.radians(angle)
        x1 = center_x + 20 * math.cos(rad)
        y1 = center_y + 20 * math.sin(rad)
        x2 = center_x + 25 * math.cos(rad)
        y2 = center_y + 25 * math.sin(rad)
        d.line([(x1, y1), (x2, y2)], fill='yellow', width=2)
    return ctk.CTkImage(light_image=img, dark_image=img, size=size)


# ── Splash Screen ─────────────────────────────────────────────────────────────
class SplashScreen(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.overrideredirect(True)
        self.geometry("800x480")
        self.configure(fg_color="black")

        logo = load_icon("assets/logo.png", (500, 300))
        ctk.CTkLabel(self, image=logo, text="", fg_color="transparent").place(relx=0.5, rely=0.5, anchor="center")

        screen_width  = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()
        x = (screen_width / 2) - 400
        y = (screen_height / 2) - 240
        self.geometry(f'+{int(x)}+{int(y)}')
        self.after(3000, self.start_main)

    def start_main(self):
        # Cancel all pending after() callbacks before destroying
        for after_id in self.tk.eval('after info').split():
            try:
                self.after_cancel(after_id)
            except Exception:
                pass
        self.destroy()
        app = SleepDocApp()
        app.mainloop()


# ── Main App ──────────────────────────────────────────────────────────────────
class SleepDocApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.local_ip  = "127.0.0.1"   # GUI and backend both run on same Pi — always use localhost
        self.base_url  = f"http://{self.local_ip}:{FLASK_PORT}"
        self.attributes("-fullscreen", True)
        self.config(cursor="none")
        self.bind_all("<Control-Alt-e>", self.secret_exit)
        self.secret_tap_count = 0
        self.last_tap_time    = time.time()
        self.bind("<Button-1>", self.handle_secret_click)

        self.title("Sleep Doc+")
        self.geometry("750x470")
        self.resizable(False, False)
        self.configure(fg_color=C_BG)
        self.protocol("WM_DELETE_WINDOW", self.exit_app)

        self.ctk_font_title    = ctk.CTkFont(family="Segoe UI", size=26, weight="bold")
        self.ctk_font_subtitle = ctk.CTkFont(family="Segoe UI", size=18, weight="bold")
        self.ctk_font_data     = ctk.CTkFont(family="Segoe UI", size=34, weight="bold")
        self.ctk_font_medium   = ctk.CTkFont(family="Segoe UI", size=15, weight="normal")
        self.ctk_font_small    = ctk.CTkFont(family="Segoe UI", size=12, weight="normal")
        self.ctk_font_button   = ctk.CTkFont(family="Segoe UI", size=13, weight="bold")
        self.ctk_font_tiny     = ctk.CTkFont(family="Segoe UI", size=11, weight="normal")
        self.ctk_font_datetime = ctk.CTkFont(family="Segoe UI", size=13, weight="bold")

        self.hr_data = []
        self.br_data = []
        self.time_data = []
        self.hr = self.br = self.temp = self.hum = self.press = "--"
        self.selected_sound  = None
        self.selected_light  = None
        self.selected_effect = "breathing"   # default breathing effect
        self.current_page    = "home"
        self.weather_data   = {"temp": "--", "condition": "--", "humidity": "--"}
        self.gesture_status   = "pause"   # mirrors gv.status from vsd_on_startup
        self._pending_action  = False     # blocks poll during button actions

        self.light_colors = [
            {"name": "Sky Blue", "color": "#A7C7E7", "code": "sky_blue", "icon": create_light_icon("#A7C7E7")},
            {"name": "Amber",    "color": "#FF8C00", "code": "amber",    "icon": create_light_icon("#FF8C00")},
            {"name": "Rose",     "color": "#FFC0CB", "code": "rose",     "icon": create_light_icon("#FFC0CB")},
            {"name": "Lavender", "color": "#E6E6FA", "code": "lavender", "icon": create_light_icon("#E6E6FA")},
            {"name": "Sage",     "color": "#8FBC8F", "code": "sage",     "icon": create_light_icon("#8FBC8F")},
        ]

        self.build_gui()
        self.start_datetime_update()
        self.start_realtime_data_update()
        self._start_backend()
        self._poll_gesture_status()

    # ── GUI skeleton ──────────────────────────────────────────────────────────
    def build_gui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # Header
        self.header_frame = ctk.CTkFrame(self, height=50, fg_color=C_HEADER, corner_radius=0)
        self.header_frame.grid(row=0, column=0, columnspan=3, sticky="ew")
        self.header_frame.grid_propagate(False)
        self.header_frame.grid_columnconfigure(0, weight=0)
        self.header_frame.grid_columnconfigure(1, weight=1)
        self.header_frame.grid_columnconfigure(2, weight=0)

        ctk.CTkLabel(self.header_frame, text="Sleep Doc+",
                     font=self.ctk_font_subtitle, text_color=C_ACCENT).grid(
                     row=0, column=0, padx=20, pady=12, sticky="w")

        self.datetime_label = ctk.CTkLabel(self.header_frame, text="",
                                           font=self.ctk_font_datetime, text_color=C_MUTED)
        self.datetime_label.grid(row=0, column=1, padx=20, pady=12, sticky="e")

        # ── Hamburger menu button in header ──────────────────────────────────────
        self._menu_open = False
        self._menu_popup = None
        self.hamburger_btn = ctk.CTkButton(
            self.header_frame, text="Menu", width=44, height=36,
            font=self.ctk_font_subtitle, fg_color=C_NAV, hover_color=C_NAV_HOV,
            corner_radius=10, command=self._toggle_menu)
        self.hamburger_btn.grid(row=0, column=2, padx=(0, 16), pady=8, sticky="e")

        # Main content area — full width, no sidebar
        self.main = ctk.CTkFrame(self, fg_color="transparent")
        self.main.grid(row=1, column=0, columnspan=3, sticky="nsew")
        self.main.grid_columnconfigure(0, weight=1)
        self.main.grid_rowconfigure(0, weight=1)

        self.container = ctk.CTkFrame(self.main, fg_color="transparent")
        self.container.grid(row=0, column=0, sticky="nsew")
        self.container.grid_rowconfigure(0, weight=1)
        self.container.grid_columnconfigure(0, weight=1)

        self.home_frame         = ctk.CTkFrame(self.container, fg_color="transparent")
        self.light_frame        = ctk.CTkScrollableFrame(self.container, fg_color="transparent")
        self.analysis_frame     = ctk.CTkScrollableFrame(self.container, fg_color="transparent")
        self.data_storage_frame = ctk.CTkScrollableFrame(self.container, fg_color="transparent")
        self.connect_frame      = ctk.CTkFrame(self.container, fg_color="transparent")

        self.build_home(self.home_frame)
        self.build_light(self.light_frame)
        self.build_analysis(self.analysis_frame)
        self.build_data_storage(self.data_storage_frame)
        self.build_connect(self.connect_frame)

        for sf in (self.light_frame, self.analysis_frame, self.data_storage_frame):
            _touch_scroll_bind_tree(sf._parent_canvas, sf)

        self.show_home()

    def _toggle_menu(self):
        if self._menu_open and self._menu_popup:
            self._close_menu()
        else:
            self._open_menu()

    def _open_menu(self):
        if self._menu_popup:
            return
        self._menu_open = True
        popup = ctk.CTkFrame(self, fg_color=C_SIDEBAR, corner_radius=14,
                              border_width=1, border_color=C_BORDER)
        popup.place(x=0, y=self.header_frame.winfo_height(), anchor="nw")
        self._menu_popup = popup
        nav_items = [
            ("Home",               self.show_home),
            ("Ambient Light",      self.show_light),
            ("Real-time Analysis", self.show_analysis),
            ("Data Storage",       self.show_data_storage),
            ("Connect Phone",      self.show_connect),
        ]
        for text, cmd in nav_items:
            btn = ctk.CTkButton(
                popup, text=text, anchor="w", width=200, height=42,
                font=self.ctk_font_button, fg_color=C_NAV, hover_color=C_NAV_HOV,
                text_color=C_TEXT, corner_radius=8,
                command=lambda c=cmd: [self._close_menu(), c()])
            btn.pack(padx=8, pady=4, fill="x")
        # Close when clicking outside
        self.bind("<Button-1>", self._check_close_menu, add="+")

    def _close_menu(self):
        if self._menu_popup:
            self._menu_popup.destroy()
            self._menu_popup = None
        self._menu_open = False
        try:
            self.unbind("<Button-1>")
        except Exception:
            pass
        self.bind("<Button-1>", self.handle_secret_click)

    def _check_close_menu(self, event):
        if self._menu_popup:
            wx = self._menu_popup.winfo_rootx()
            wy = self._menu_popup.winfo_rooty()
            ww = self._menu_popup.winfo_width()
            wh = self._menu_popup.winfo_height()
            if not (wx <= event.x_root <= wx + ww and wy <= event.y_root <= wy + wh):
                self._close_menu()

    # ══════════════════════════════════════════════════════════════════════════
    # ── DateTime update ───────────────────────────────────────────────────────
    def start_datetime_update(self):
        self.update_datetime()

    def update_datetime(self):
        if not getattr(self, '_alive', True):
            return
        now = datetime.now()
        self.datetime_label.configure(
            text=f"{now.strftime('%A, %B %d, %Y')} • {now.strftime('%I:%M:%S %p')}")
        self.after(1000, self.update_datetime)

    # ── Real-time vitals update ───────────────────────────────────────────────
    def start_realtime_data_update(self):
        """Start a single persistent vitals-poll thread that loops forever.
        Previous design spawned a NEW thread every 2 seconds — after 15 minutes
        that is 450 threads created, causing GIL pressure and Flask queue buildup
        that made every request time out. One thread that sleeps between polls
        has zero accumulation overhead."""
        t = threading.Thread(target=self._vitals_loop, daemon=True)
        t.start()

    def _vitals_loop(self):
        """Single long-lived thread. Polls /vitals every 2 seconds forever,
        pushes results to the main thread via self.after(0, ...) — never
        touches Tkinter directly from here."""
        while getattr(self, '_alive', True):
            vitals = None
            try:
                url      = f"http://{self.local_ip}:{FLASK_PORT}/vitals"
                response = requests.get(url, timeout=2)
                if response.status_code == 200:
                    vitals = response.json()
            except Exception:
                pass   # silent — Pi might not be reachable yet
            if getattr(self, '_alive', True):
                self.after(0, lambda v=vitals: self._on_vitals_fetched(v))
            time.sleep(2)

    def _on_vitals_fetched(self, vitals):
        """Runs on the main thread — just applies vitals if we got any."""
        if vitals is not None:
            self._apply_vitals(vitals)

    def _reset_vitals_display(self):
        """Clear all vital displays back to -- after session stops."""
        self.hr = self.br = self.temp = self.hum = self.press = "--"
        self.hr_label.configure(text="HR: --")
        self.br_label.configure(text="BR: --")
        self.temp_label.configure(text="\U0001f321\ufe0f  Temp: --\u00b0C")
        self.hum_label.configure(text="\U0001f4a7  Hum: --%")
        self.press_label.configure(text="\U0001f535  Press: -- hPa")
        self.hr_data.clear()
        self.br_data.clear()
        self._graph_tick = 0
        if hasattr(self, 'home_ax'):
            self.home_hr_line.set_data([], [])
            self.home_br_line.set_data([], [])
            self.home_ax.set_xlim(0, 10)
            self.home_ax.set_ylim(0, 50)
            self.home_canvas.draw_idle()

    def _apply_vitals(self, vitals):
        """Called on the main thread to safely update UI widgets."""
        try:
            # Skip updates when session is not running
            if self.gesture_status != "start":
                return
            hr    = float(vitals.get("heart_rate")     or 0)
            br    = float(vitals.get("breathing_rate") or 0)
            temp  = float(vitals.get("temperature")    or 0)
            hum   = float(vitals.get("humidity")       or 0)
            press = float(vitals.get("pressure")       or 0)

            if hr > 0:
                self.hr = hr
                self.hr_label.configure(text=f"HR: {self.hr:.1f}")
            if br > 0:
                self.br = br
                self.br_label.configure(text=f"BR: {self.br:.1f}")
            if temp > 0:
                self.temp = temp
                self.temp_label.configure(text=f"Temp: {self.temp:.1f}°C")
            if hum > 0:
                self.hum = hum
                self.hum_label.configure(text=f"Hum: {self.hum:.1f}%")
            if press > 0:
                self.press = press
                self.press_label.configure(text=f"Press: {self.press:.1f} hPa")

            if hr > 0:
                self.hr_data.append(hr)
            if br > 0:
                self.br_data.append(br)
            if len(self.hr_data) > 60:
                self.hr_data = self.hr_data[-60:]
            if len(self.br_data) > 60:
                self.br_data = self.br_data[-60:]

            # Rolling average over last 5 readings to smooth the line
            def rolling_avg(data, w=5):
                return [sum(data[max(0,i-w+1):i+1]) / len(data[max(0,i-w+1):i+1])
                        for i in range(len(data))]

            # Redraw graph every 10s (every 5th 2s fetch)
            self._graph_tick = getattr(self, '_graph_tick', 0) + 1
            if self._graph_tick >= 5:
                self._graph_tick = 0
                hr_smooth = rolling_avg(self.hr_data)
                br_smooth = rolling_avg(self.br_data)
                if hasattr(self, 'home_ax') and len(hr_smooth) > 1:
                    self.home_hr_line.set_data(range(len(hr_smooth)), hr_smooth)
                    self.home_br_line.set_data(range(len(br_smooth)), br_smooth)
                    self.home_ax.set_xlim(0, max(len(hr_smooth), 10))
                    all_vals = hr_smooth + br_smooth
                    self.home_ax.set_ylim(max(0, min(all_vals) - 10), max(all_vals) + 15)
                    self.home_canvas.draw_idle()

        except Exception:
            pass

    def update_realtime_data(self):
        pass  # kept for compatibility — replaced by _fetch_vitals_in_thread

    # ── Home page ─────────────────────────────────────────────────────────────
    def build_home(self, frame):
        frame.grid_columnconfigure(0, weight=1)   # left panel
        frame.grid_columnconfigure(1, weight=2)   # right panel (graph)
        frame.grid_rowconfigure(0, weight=1)

        # ── LEFT PANEL: all vitals + AI + gesture status ──────────────────────
        left = ctk.CTkFrame(frame, fg_color="transparent")
        left.grid(row=0, column=0, sticky="nsew", padx=(12, 6), pady=10)
        left.grid_columnconfigure(0, weight=1)
        left.grid_rowconfigure(3, weight=1)   # spacer row absorbs extra space, not the button's row

        # HR / BR
        hrbr_card = ctk.CTkFrame(left, fg_color=C_CARD, corner_radius=16)
        hrbr_card.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        hrbr_card.grid_columnconfigure((0, 1), weight=1)
        self.hr_label = ctk.CTkLabel(hrbr_card, text="HR: --",
                                     font=self.ctk_font_subtitle, text_color=C_HR)
        self.hr_label.grid(row=0, column=0, padx=10, pady=12)
        self.br_label = ctk.CTkLabel(hrbr_card, text="BR: --",
                                     font=self.ctk_font_subtitle, text_color=C_BR)
        self.br_label.grid(row=0, column=1, padx=10, pady=12)

        # Env vitals — stacked vertically so nothing gets cut
        env_card = ctk.CTkFrame(left, fg_color=C_CARD, corner_radius=16)
        env_card.grid(row=1, column=0, sticky="ew", pady=(0, 6))
        env_card.grid_columnconfigure(0, weight=1)
        self.temp_label = ctk.CTkLabel(env_card, text="Temp: --°C",
                                       font=self.ctk_font_small, text_color=C_TEMP)
        self.temp_label.grid(row=0, column=0, pady=(8, 2))
        self.hum_label = ctk.CTkLabel(env_card, text="Hum: --%",
                                      font=self.ctk_font_small, text_color=C_HUM)
        self.hum_label.grid(row=1, column=0, pady=2)
        self.press_label = ctk.CTkLabel(env_card, text="Press: -- hPa",
                                        font=self.ctk_font_small, text_color=C_PRESS)
        self.press_label.grid(row=2, column=0, pady=(2, 8))

        # Gesture status
        gest_card = ctk.CTkFrame(left, fg_color=C_CARD, corner_radius=16)
        gest_card.grid(row=2, column=0, sticky="ew", pady=(0, 6))
        gest_card.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(gest_card, text="Gesture / Button Control",
                     font=self.ctk_font_small, text_color=C_MUTED).grid(row=0, column=0, pady=(8,0))
        self.gesture_status_label = ctk.CTkLabel(
            gest_card,
            text="Session Paused — Swipe LEFT to start",
            font=self.ctk_font_small, text_color=C_WARN,
            wraplength=220)
        self.gesture_status_label.grid(row=1, column=0, pady=(2, 8), padx=8)

        # Spacer
        ctk.CTkFrame(left, fg_color="transparent", height=1).grid(row=3, column=0, sticky="ew")

        # Start/Stop button at bottom of left panel
        self.monitor_btn = ctk.CTkButton(
            left, text="Start Session",
            font=ctk.CTkFont(size=18, weight="bold"), command=self.toggle_monitoring,
            height=60, fg_color=C_ACCENT2, hover_color=C_ACCENT_HOV,
            corner_radius=30, text_color="white")
        self.monitor_btn.grid(row=4, column=0, sticky="sew", pady=(0, 6))

        # ── RIGHT PANEL: live graph ───────────────────────────────────────────
        right = ctk.CTkFrame(frame, fg_color=C_CARD, corner_radius=16)
        right.grid(row=0, column=1, sticky="nsew", padx=(6, 12), pady=10)
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(right, text="Live Vitals Trend",
                     font=self.ctk_font_small, text_color=C_MUTED).grid(
                     row=0, column=0, pady=(10, 0))

        fig, ax = plt.subplots(figsize=(5, 3), facecolor=C_CARD)
        ax.set_facecolor("#0d0c2a")
        self.home_fig = fig
        self.home_ax  = ax
        self.home_hr_line, = ax.plot([], [], color=C_HR, linewidth=2, label="HR")
        self.home_br_line, = ax.plot([], [], color=C_BR, linewidth=2, label="BR")
        ax.legend(loc="upper right", facecolor=C_CARD2, labelcolor=C_TEXT,
                  fontsize=8, framealpha=0.9, edgecolor=C_BORDER)
        ax.set_xlabel("Samples", color=C_MUTED, fontsize=7)
        ax.set_ylabel("BPM",     color=C_MUTED, fontsize=7)
        ax.tick_params(colors=C_MUTED, labelsize=6)
        for spine in ax.spines.values():
            spine.set_edgecolor(C_BORDER)
        ax.set_xlim(0, 30)
        ax.set_ylim(0, 120)
        ax.grid(True, color=C_BORDER, linewidth=0.5, alpha=0.5)
        fig.tight_layout(pad=0.6)
        self.home_canvas = FigureCanvasTkAgg(fig, master=right)
        self.home_canvas.draw()
        self.home_canvas.get_tk_widget().grid(row=1, column=0, sticky="nsew", padx=10, pady=(4, 10))

    # ── Binaural page ─────────────────────────────────────────────────────────
    # ── Light page ────────────────────────────────────────────────────────────
    def build_light(self, frame):
        frame.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(frame, text="Ambient Light Therapy",
                     font=self.ctk_font_title, text_color=C_ACCENT).grid(row=0, column=0, pady=20)

        # ── Color buttons ─────────────────────────────────────────────────────
        light_frame = ctk.CTkFrame(frame, fg_color="transparent")
        light_frame.grid(row=1, column=0, pady=10, padx=40, sticky="ew")
        for col in range(3):
            light_frame.grid_columnconfigure(col, weight=1)

        self.light_buttons = []
        for i, light in enumerate(self.light_colors):
            row = i // 3
            col = i % 3
            btn = ctk.CTkButton(light_frame, text=light['name'], image=light['icon'],
                               compound="top", width=200, height=100,
                               font=self.ctk_font_medium, corner_radius=16,
                               fg_color=C_CARD2, hover_color=C_NAV_HOV,
                               command=lambda l=light: self.select_light(l))
            btn.grid(row=row, column=col, padx=10, pady=10)
            self.light_buttons.append(btn)

        # ── Selected color label ──────────────────────────────────────────────
        self.selected_light_frame = ctk.CTkFrame(frame, fg_color=C_CARD, corner_radius=16)
        self.selected_light_frame.grid(row=2, column=0, pady=10, padx=40, sticky="ew")
        self.selected_light_label = ctk.CTkLabel(
            self.selected_light_frame, text="No light color selected",
            font=self.ctk_font_medium, text_color=C_MUTED)
        self.selected_light_label.pack(pady=15)

        # ── Effect buttons (Static / Breathing) ──────────────────────────────
        effect_frame = ctk.CTkFrame(frame, fg_color=C_CARD, corner_radius=16)
        effect_frame.grid(row=3, column=0, pady=10, padx=40, sticky="ew")
        ctk.CTkLabel(effect_frame, text="Light Effect",
                    font=self.ctk_font_medium).pack(pady=(15, 8))

        effect_btn_frame = ctk.CTkFrame(effect_frame, fg_color="transparent")
        effect_btn_frame.pack(pady=(0, 15))

        self.selected_effect = "breathing"   # default matches VSD default

        self.static_btn = ctk.CTkButton(
            effect_btn_frame, text="Static",
            width=160, height=45, corner_radius=20,
            fg_color=C_CARD2, hover_color=C_NAV_HOV,
            command=lambda: self.select_effect("static"))
        self.static_btn.pack(side="left", padx=10)

        self.breathing_btn = ctk.CTkButton(
            effect_btn_frame, text="Breathing",
            width=160, height=45, corner_radius=20,
            fg_color=C_ACCENT2, hover_color=C_ACCENT_HOV,
            command=lambda: self.select_effect("breathing"))
        self.breathing_btn.pack(side="left", padx=10)

        # ── Brightness slider ─────────────────────────────────────────────────
        brightness_frame = ctk.CTkFrame(frame, fg_color=C_CARD, corner_radius=16)
        brightness_frame.grid(row=4, column=0, pady=10, padx=40, sticky="ew")
        ctk.CTkLabel(brightness_frame, text="Brightness Control",
                    font=self.ctk_font_medium).pack(pady=(15, 5))
        self.brightness_slider = ctk.CTkSlider(
            brightness_frame, from_=0, to=100, number_of_steps=100, width=400,
            command=self.adjust_brightness)
        self.brightness_slider.pack(pady=10)
        self.brightness_slider.set(100)   # default full brightness
        self.brightness_value_label = ctk.CTkLabel(
            brightness_frame, text="100%", font=self.ctk_font_small)
        self.brightness_value_label.pack(pady=(0, 15))

        # ── Turn On / Off buttons ─────────────────────────────────────────────
        control_frame = ctk.CTkFrame(frame, fg_color="transparent")
        control_frame.grid(row=5, column=0, pady=15)
        self.turn_on_light_btn = ctk.CTkButton(
            control_frame, text="Turn On", command=self.turn_on_light,
            width=120, height=45, state="disabled", corner_radius=20)
        self.turn_on_light_btn.pack(side="left", padx=10)
        self.turn_off_light_btn = ctk.CTkButton(
            control_frame, text="Turn Off", command=self.turn_off_light,
            width=120, height=45, state="disabled", corner_radius=20)
        self.turn_off_light_btn.pack(side="left", padx=10)

    # ── Analysis page ─────────────────────────────────────────────────────────
    def build_analysis(self, frame):
        frame.grid_columnconfigure(0, weight=1)

        header = ctk.CTkFrame(frame, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=20, pady=(15, 5))
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(header, text="Sleep Session Analysis",
                     font=self.ctk_font_title, text_color=C_ACCENT).grid(row=0, column=0, sticky="w")
        self.analysis_refresh_btn = ctk.CTkButton(
            header, text="⟳  Load Latest Session", width=200, height=36,
            corner_radius=18, fg_color=C_ACCENT2, hover_color=C_ACCENT_HOV,
            font=self.ctk_font_button, command=self.load_analysis_data)
        self.analysis_refresh_btn.grid(row=0, column=1, sticky="e", padx=(10, 0))

        sel_frame = ctk.CTkFrame(frame, fg_color=C_CARD, corner_radius=14)
        sel_frame.grid(row=1, column=0, padx=20, pady=6, sticky="ew")
        sel_frame.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(sel_frame, text="Session file:",
                     font=self.ctk_font_small, text_color=C_MUTED).grid(row=0, column=0, padx=15, pady=10)
        self.session_var   = tk.StringVar(value="No session loaded")
        self.session_label = ctk.CTkLabel(sel_frame, textvariable=self.session_var,
                                          font=self.ctk_font_small, text_color=C_TEXT)
        self.session_label.grid(row=0, column=1, padx=10, pady=10, sticky="w")
        ctk.CTkButton(sel_frame, text="Browse Folder...", width=130, height=30,
                      corner_radius=15, font=self.ctk_font_button,
                      command=self.browse_session_file).grid(row=0, column=2, padx=15, pady=10)

        stats_frame = ctk.CTkFrame(frame, fg_color=C_CARD, corner_radius=14)
        stats_frame.grid(row=2, column=0, padx=20, pady=6, sticky="ew")
        for i in range(5):
            stats_frame.grid_columnconfigure(i, weight=1)

        stat_defs = [
            ("Avg HR",   "--", C_HR,     "bpm"),
            ("Avg BR",   "--", C_BR,     "bpm"),
            ("Avg Temp", "--", C_TEMP,   "°C"),
            ("Avg Hum",  "--", C_HUM,    "%"),
            ("Duration", "--", C_ACCENT, "min"),
        ]
        self.stat_labels = {}
        for col, (name, val, color, unit) in enumerate(stat_defs):
            card = ctk.CTkFrame(stats_frame, fg_color=C_CARD2, corner_radius=12)
            card.grid(row=0, column=col, padx=8, pady=10, sticky="nsew")
            ctk.CTkLabel(card, text=name, font=self.ctk_font_tiny,  text_color=C_MUTED).pack(pady=(8, 0))
            lbl = ctk.CTkLabel(card, text=val, font=self.ctk_font_subtitle, text_color=color)
            lbl.pack()
            ctk.CTkLabel(card, text=unit, font=self.ctk_font_tiny, text_color=C_MUTED).pack(pady=(0, 8))
            self.stat_labels[name] = lbl

        for row_idx, (title, line_attr1, line_attr2, color1, color2, label1, label2, ylab) in enumerate([
            ("Heart Rate & Breathing Rate", "an_hr_line", "an_br_line",   C_HR,   C_BR,   "Heart Rate",   "Breathing Rate", "BPM"),
            ("Temperature & Humidity",      "an_temp_line","an_hum_line", C_TEMP, C_HUM,  "Temp (°C)",    "Humidity (%)",   ""),
        ], start=3):
            chart_card = ctk.CTkFrame(frame, fg_color=C_CARD, corner_radius=14)
            chart_card.grid(row=row_idx, column=0, padx=20, pady=6, sticky="ew")
            ctk.CTkLabel(chart_card, text=title,
                         font=self.ctk_font_small, text_color=C_MUTED).pack(pady=(10, 0))
            fig, ax = plt.subplots(figsize=(7, 2.0), facecolor=C_CARD)
            ax.set_facecolor("#0d0c2a")
            line1, = ax.plot([], [], color=color1, linewidth=1.8, label=label1)
            line2, = ax.plot([], [], color=color2, linewidth=1.8, label=label2)
            ax.legend(loc="upper right", facecolor=C_CARD2, labelcolor=C_TEXT, fontsize=8, framealpha=0.9)
            ax.set_xlabel("Time (min)", color=C_MUTED, fontsize=7)
            if ylab:
                ax.set_ylabel(ylab, color=C_MUTED, fontsize=7)
            ax.tick_params(colors=C_MUTED, labelsize=6)
            ax.grid(True, color=C_BORDER, linewidth=0.4, alpha=0.5)
            for sp in ax.spines.values():
                sp.set_edgecolor(C_BORDER)
            fig.tight_layout(pad=0.5)
            canvas = FigureCanvasTkAgg(fig, master=chart_card)
            canvas.draw()
            canvas.get_tk_widget().pack(fill="x", padx=10, pady=(0, 10))
            setattr(self, f"an_fig{row_idx-2}",    fig)
            setattr(self, f"an_ax{row_idx-2}",     ax)
            setattr(self, line_attr1, line1)
            setattr(self, line_attr2, line2)
            setattr(self, f"an_canvas{row_idx-2}", canvas)

        insights_card = ctk.CTkFrame(frame, fg_color=C_CARD, corner_radius=14)
        insights_card.grid(row=5, column=0, padx=20, pady=6, sticky="ew")
        ctk.CTkLabel(insights_card, text="Health Insights & Recommendations",
                     font=self.ctk_font_subtitle, text_color=C_ACCENT).pack(pady=(12, 4))
        self.insights_text = ctk.CTkTextbox(insights_card, height=140,
                                            fg_color=C_CARD2, text_color=C_TEXT,
                                            font=self.ctk_font_small, corner_radius=10)
        self.insights_text.pack(fill="x", padx=15, pady=(0, 12))
        self.insights_text.insert("end", "Load a session file to see insights.")
        self.insights_text.configure(state="disabled")

        self.after(300, self.load_analysis_data)

    # ── Data Storage page ─────────────────────────────────────────────────────
    def build_data_storage(self, frame):
        frame.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(frame, text="Data Storage & File Management",
                     font=self.ctk_font_title, text_color=C_ACCENT).grid(row=0, column=0, pady=20)

        storage_frame = ctk.CTkFrame(frame, fg_color=C_CARD, corner_radius=16)
        storage_frame.grid(row=1, column=0, pady=20, padx=40, sticky="ew")
        storage_info = self.get_storage_info()
        storage_text = (
            f"Storage Information:\n\n"
            f"• Total Session Folders: {storage_info['total_files']}\n"
            f"• Storage Used: {storage_info['used_space']} MB\n"
            f"• Available Space: {storage_info['free_space']} MB\n"
            f"• Directory: /home/raspberry/Desktop/SLEEPDOC/Session Reports/\n\n"
            f"Sessions are saved by date → session time when monitoring runs"
        )
        ctk.CTkLabel(storage_frame, text=storage_text,
                    font=self.ctk_font_small, justify="left").pack(pady=10, padx=20, fill="x")

        self.file_listbox_frame = ctk.CTkFrame(frame, fg_color=C_CARD, corner_radius=16)
        self.file_listbox_frame.grid(row=3, column=0, pady=20, padx=40, sticky="ew")
        self.file_listbox_frame.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(self.file_listbox_frame, text="Session Files",
                    font=self.ctk_font_subtitle).grid(row=0, column=0, pady=(15, 5))
        self.files_scroll_frame = ctk.CTkFrame(
            self.file_listbox_frame, fg_color="transparent")
        self.files_scroll_frame.grid(row=1, column=0, pady=10, padx=20, sticky="ew")
        self.files_scroll_frame.grid_columnconfigure(0, weight=1)

        file_buttons_frame = ctk.CTkFrame(frame, fg_color="transparent")
        file_buttons_frame.grid(row=4, column=0, pady=20)
        self.refresh_files_btn = ctk.CTkButton(
            file_buttons_frame, text="Refresh",
            command=self.refresh_file_list, width=150, height=40, corner_radius=20)
        self.refresh_files_btn.pack(side="left", padx=10)
        self.delete_all_btn = ctk.CTkButton(
            file_buttons_frame, text="Delete All Sessions",
            command=self.delete_all_files,
            width=180, height=40, corner_radius=20,
            fg_color="#dc3545", hover_color="#c82333")
        self.delete_all_btn.pack(side="left", padx=10)

        self.file_viewer_frame = ctk.CTkFrame(frame, fg_color=C_CARD, corner_radius=16)
        self.file_viewer_frame.grid(row=5, column=0, pady=20, padx=40, sticky="ew")
        ctk.CTkLabel(self.file_viewer_frame, text="File Viewer",
                    font=self.ctk_font_subtitle).pack(pady=(15, 5))
        self.file_content_text = ctk.CTkTextbox(
            self.file_viewer_frame, height=200, width=800)
        self.file_content_text.pack(pady=10, padx=20, fill="both", expand=True)

        self.refresh_file_list()

    # ── Connect Phone (QR) ───────────────────────────────────────────────────
    def build_connect(self, frame):
        frame.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(frame, text="Scan to Connect",
                     font=self.ctk_font_title, text_color=C_ACCENT).grid(
                     row=0, column=0, pady=(30, 6))
        ctk.CTkLabel(frame,
                     text="Connect your phone to this device's WiFi, then scan\n"
                          "this code to open the Sleep Doc+ dashboard.",
                     font=self.ctk_font_small, text_color=C_MUTED,
                     justify="center").grid(row=1, column=0, pady=(0, 20))

        qr_card = ctk.CTkFrame(frame, fg_color=C_CARD, corner_radius=16)
        qr_card.grid(row=2, column=0, pady=10, padx=40)
        self.qr_image_label = ctk.CTkLabel(
            qr_card, text="", image=load_icon(QR_PATH, (260, 260), fallback_text="No QR yet"))
        self.qr_image_label.pack(padx=24, pady=24)

        self.qr_url_label = ctk.CTkLabel(
            frame, text=f"Or open manually:  {self.base_url}",
            font=self.ctk_font_small, text_color=C_TEXT)
        self.qr_url_label.grid(row=3, column=0, pady=(10, 6))

        ctk.CTkButton(frame, text="Refresh", width=140, height=38,
                      corner_radius=18, command=self.refresh_connect_page
                      ).grid(row=4, column=0, pady=10)

    def refresh_connect_page(self):
        """Re-reads the IP and QR file — useful if the network came up after
        the GUI started, or the QR file was just (re)generated."""
        display_ip = get_local_ip()   # actual wlan0 IP shown to phone user
        self.qr_image_label.configure(image=load_icon(QR_PATH, (260, 260), fallback_text="No QR yet"))
        self.qr_url_label.configure(text=f"Or open manually:  http://{display_ip}:{FLASK_PORT}")

    # ── Navigation ────────────────────────────────────────────────────────────
    def show_home(self):
        self.hide_all_frames()
        self.home_frame.grid(row=0, column=0, sticky="nsew")
        self.home_frame.grid_columnconfigure(0, weight=1)
        self.home_frame.grid_columnconfigure(1, weight=2)
        self.home_frame.grid_rowconfigure(0, weight=1)
        self.current_page = "home"

    def show_light(self):
        self.hide_all_frames()
        self.light_frame.grid(row=0, column=0, sticky="nsew")
        self.current_page = "light"

    def show_analysis(self):
        self.hide_all_frames()
        self.analysis_frame.grid(row=0, column=0, sticky="nsew")
        self.current_page = "analysis"

    def show_data_storage(self):
        self.hide_all_frames()
        self.data_storage_frame.grid(row=0, column=0, sticky="nsew")
        self.current_page = "data_storage"
        self.refresh_file_list()

    def show_connect(self):
        self.hide_all_frames()
        self.connect_frame.grid(row=0, column=0, sticky="nsew")
        self.current_page = "connect"
        self.refresh_connect_page()

    def hide_all_frames(self):
        for frame in [self.home_frame,
                      self.light_frame,
                      self.analysis_frame, self.data_storage_frame,
                      self.connect_frame]:
            frame.grid_remove()
            frame.grid_forget()

    # ── Control helpers ───────────────────────────────────────────────────────
    def send_control_settings(self):
        """Build the payload and fire it in a background thread so the main
        thread (and Tkinter event loop) never blocks on a network call.
        A pending flag prevents stacking up dozens of simultaneous requests
        when the brightness slider is dragged — only the most recent value
        is sent once any in-flight request finishes."""
        brightness_pct = int(self.brightness_slider.get()) if hasattr(self, "brightness_slider") else 100
        brightness_float = round(brightness_pct / 100.0, 2)
        data = {
            "light_on":       self.selected_light is not None,
            "led_mode":       self.selected_light["code"] if self.selected_light else "",
            "led_effect":     getattr(self, "selected_effect", "breathing"),
            "led_brightness": brightness_float,
        }
        # Snapshot latest data so the thread always uses the most recent values
        self._pending_led_data = data
        if not getattr(self, "_led_send_in_flight", False):
            self._led_send_in_flight = True
            threading.Thread(target=self._send_led_in_background, daemon=True).start()

    def _send_led_in_background(self):
        """Runs in background thread. Re-checks _pending_led_data after each
        send so rapid slider drags coalesce into one final request."""
        while True:
            data = getattr(self, "_pending_led_data", None)
            if data is None:
                break
            self._pending_led_data = None   # consume it
            try:
                requests.post(f"http://{self.local_ip}:{FLASK_PORT}/control",
                              json=data, timeout=2)
            except Exception as e:
                print(f"Error sending control settings: {e}")
            # If another value arrived while we were sending, loop and send it
            if getattr(self, "_pending_led_data", None) is None:
                break
        self._led_send_in_flight = False

    # ── Effect selector ───────────────────────────────────────────────────────
    def select_effect(self, effect):
        self.selected_effect = effect
        if effect == "static":
            self.static_btn.configure(fg_color=C_ACCENT2, hover_color=C_ACCENT_HOV)
            self.breathing_btn.configure(fg_color=C_CARD2, hover_color=C_NAV_HOV)
        else:
            self.breathing_btn.configure(fg_color=C_ACCENT2, hover_color=C_ACCENT_HOV)
            self.static_btn.configure(fg_color=C_CARD2, hover_color=C_NAV_HOV)
        self.send_control_settings()

    # ── Light methods ─────────────────────────────────────────────────────────
    def select_light(self, light):
        self.selected_light = light
        self.selected_light_label.configure(
            text=f"Selected: {light['name']} Light", text_color=light['color'])
        self.turn_on_light_btn.configure(state="normal")
        self.turn_off_light_btn.configure(state="normal")
        self.send_control_settings()

    def adjust_brightness(self, value):
        brightness = int(value)
        self.brightness_value_label.configure(text=f"{brightness}%")
        self.send_control_settings()

    def turn_on_light(self):
        if self.selected_light:
            self.send_control_settings()
            messagebox.showinfo("Ambient Light",
                                f"{self.selected_light['name']} light turned on")

    def turn_off_light(self):
        try:
            self.selected_light = None
            self.selected_light_label.configure(text="No light color selected", text_color=C_MUTED)
            self.turn_on_light_btn.configure(state="disabled")
            self.turn_off_light_btn.configure(state="disabled")
            self.send_control_settings()
            messagebox.showinfo("Ambient Light", "Ambient light turned off")
        except Exception as e:
            print(f"Error turning off light: {e}")

    # ── Backend auto-start ────────────────────────────────────────────────────
    def _start_backend(self):
        global vitals_process
        script = "/home/raspberry/Desktop/SLEEPDOC/vsd_on_startup.py"
        if os.path.exists(script):
            try:
                vitals_process = subprocess.Popen(
                    ["sudo", "python3", script])
                print(f"[GUI] Backend started (PID {vitals_process.pid})")
            except Exception as e:
                print(f"[GUI] Could not start backend: {e}")
        else:
            print("[GUI] vsd_on_startup.py not found in SLEEPDOC folder")

    # ── Gesture status poll ───────────────────────────────────────────────────
    def _poll_gesture_status(self):
        """Start a single persistent status-poll thread. Called once at startup.
        Previous design spawned a NEW thread every second — after 15 minutes
        that is 900 threads created. This version uses one thread that loops
        internally, eliminating all thread-spawn overhead."""
        t = threading.Thread(target=self._status_loop, daemon=True)
        t.start()

    def _status_loop(self):
        """Single long-lived thread. Polls /status every second forever,
        pushes results to the main thread via self.after(0, ...)."""
        while getattr(self, '_alive', True):
            new_status = None
            try:
                if not getattr(self, '_pending_action', False):
                    r = requests.get(
                        f"http://{self.local_ip}:{FLASK_PORT}/status", timeout=1)
                    if r.status_code == 200:
                        data = r.json()
                        new_status = data.get("monitoring")
            except Exception:
                pass
            if getattr(self, '_alive', True):
                self.after(0, lambda s=new_status: self._on_status_fetched(s))
            time.sleep(1)

    def _on_status_fetched(self, new_status):
        """Runs on the main thread — applies a status change if any."""
        if new_status is not None and new_status != self.gesture_status:
            self.gesture_status = new_status
            self._sync_monitoring_ui()

    def _sync_monitoring_ui(self):
        """Update button + status label to match the backend gesture state."""
        global monitoring
        if self.gesture_status == "start":
            monitoring = True
            self.monitor_btn.configure(
                text="Stop Session", fg_color="#dc3545", hover_color="#c82333")
            self.gesture_status_label.configure(
                text="Session Running — Swipe RIGHT to stop",
                text_color=C_SUCCESS)
        else:
            monitoring = False
            self.monitor_btn.configure(
                text="Start Session", fg_color=C_ACCENT2, hover_color=C_ACCENT_HOV)
            self.gesture_status_label.configure(
                text="Session Paused — Swipe LEFT to start",
                text_color=C_WARN)
            self._reset_vitals_display()

    # ── Monitoring ────────────────────────────────────────────────────────────
    def toggle_monitoring(self):
        """Start or stop a session via the /control API (same as a gesture swipe)."""
        # Derive intent from gesture_status (source of truth), not the global
        want_start = (self.gesture_status != "start")
        # Disable button and block poll loop immediately
        self.monitor_btn.configure(state="disabled")
        self._pending_action = True
        threading.Thread(
            target=self._do_toggle, args=(want_start,), daemon=True).start()

    def _do_toggle(self, want_start):
        """Runs in background thread — sends control request without blocking UI."""
        brightness_pct   = int(self.brightness_slider.get()) if hasattr(self, "brightness_slider") else 100
        brightness_float = round(brightness_pct / 100.0, 2)
        data = {
            "light_on":       self.selected_light is not None,
            "led_mode":       self.selected_light["code"] if self.selected_light else "",
            "led_effect":     getattr(self, "selected_effect", "breathing"),
            "led_brightness": brightness_float,
            "monitoring":     want_start,   # True = start, False = stop
        }

        confirmed_status = None   # the backend's actual gv.status after the call
        for attempt in range(3):
            try:
                r = requests.post(
                    f"http://{self.local_ip}:{FLASK_PORT}/control",
                    json=data, timeout=3)
                if r.status_code == 200:
                    # Trust what the backend says actually happened, not just
                    # that the HTTP call succeeded — the backend may have
                    # silently skipped the action if it raced with a gesture.
                    body = r.json()
                    confirmed_status = body.get("monitoring")
                    break
                else:
                    print(f"[GUI] Toggle attempt {attempt+1} got status {r.status_code}")
            except Exception as e:
                print(f"[GUI] Toggle attempt {attempt+1} failed: {e}")
                time.sleep(0.5)

        def apply():
            global monitoring
            if confirmed_status is not None:
                # Reflect the backend's real state, whatever it ended up being —
                # if it matches what we asked for, great; if not (e.g. lost a
                # race with a gesture), the UI still shows the true state
                # instead of the state we merely hoped for.
                self.gesture_status = confirmed_status
                monitoring = (confirmed_status == "start")
            else:
                print("[GUI] Could not reach backend — UI unchanged")
            self._sync_monitoring_ui()
            self.monitor_btn.configure(state="normal")
            self._pending_action = False   # re-enable poll loop

        if getattr(self, '_alive', True):
            self.after(0, apply)

    # ── Analysis helpers ──────────────────────────────────────────────────────
    def browse_session_file(self):
        """Let user pick a session folder — loads both radar + env CSVs together."""
        folder = filedialog.askdirectory(
            title="Select Session Folder",
            initialdir=self._data_dir())
        if folder:
            self._parse_and_display(folder)

    def _data_dir(self):
        path = "/home/raspberry/Desktop/SLEEPDOC/Session Reports"
        os.makedirs(path, exist_ok=True)
        return path

    def load_analysis_data(self):
        """Find the most recent session folder and load it."""
        data_dir = self._data_dir()
        session_folders = []
        for day_dir in glob.glob(os.path.join(data_dir, "*")):
            if os.path.isdir(day_dir):
                for sess_dir in glob.glob(os.path.join(day_dir, "*")):
                    if os.path.isdir(sess_dir):
                        session_folders.append(sess_dir)
        if not session_folders:
            self.session_var.set("No session folders found.")
            return
        latest = max(session_folders, key=os.path.getmtime)
        self._parse_and_display(latest)

    def _col(self, row, *names):
        for n in names:
            if n in row:
                try:
                    v = float(row[n])
                    return v if v > 0 else None
                except (ValueError, TypeError):
                    pass
        return None

    def _read_csv_sudo(self, path):
        """Read a CSV file owned by root using sudo cat."""
        try:
            result = subprocess.run(
                ["sudo", "cat", path],
                capture_output=True, text=True, timeout=5)
            if result.returncode == 0 and result.stdout.strip():
                import io
                reader = csv.DictReader(io.StringIO(result.stdout))
                return list(reader)
        except Exception as e:
            print(f"[CSV read error] {path}: {e}")
        return []

    def _parse_and_display(self, session_path):
        """Load combined CSV (preferred) or radar+env separately from a session folder."""
        try:
            all_csvs     = glob.glob(os.path.join(session_path, "*.csv"))
            combined_csv = next((f for f in all_csvs if "combined" in os.path.basename(f).lower()), None)
            radar_csv    = next((f for f in all_csvs if "radar"   in os.path.basename(f).lower()), None)
            env_csv      = next((f for f in all_csvs if "env"     in os.path.basename(f).lower()
                                 or "environment" in os.path.basename(f).lower()), None)

            hrs, brs, temps, hums = [], [], [], []

            if combined_csv:
                # Single combined file — has all columns
                rows = self._read_csv_sudo(combined_csv)
                self.session_var.set(f"{os.path.basename(session_path)} (combined)")
                for i, r in enumerate(rows):
                    t    = float(r.get("time", i)) / 60.0
                    hr   = self._col(r, "heart_rate",    "hr",   "HeartRate",    "HR")
                    br   = self._col(r, "breath_rate",   "br",   "BreathingRate","BR", "breathing_rate")
                    temp = self._col(r, "temperature",   "temp", "Temperature")
                    hum  = self._col(r, "humidity",      "hum",  "Humidity")
                    if hr:   hrs.append((t, hr))
                    if br:   brs.append((t, br))
                    if temp: temps.append((t, temp))
                    if hum:  hums.append((t, hum))
            else:
                # Fallback — read radar + env separately
                self.session_var.set(os.path.basename(session_path))
                radar_rows = self._read_csv_sudo(radar_csv) if radar_csv else []
                env_rows   = self._read_csv_sudo(env_csv)   if env_csv   else []
                for i, r in enumerate(radar_rows):
                    t  = float(r.get("time", i)) / 60.0
                    hr = self._col(r, "heart_rate", "hr", "HeartRate", "HR")
                    br = self._col(r, "breath_rate","br", "BreathingRate","BR","breathing_rate")
                    if hr: hrs.append((t, hr))
                    if br: brs.append((t, br))
                for i, r in enumerate(env_rows):
                    t    = float(r.get("time", i)) / 60.0
                    temp = self._col(r, "temperature", "temp", "Temperature")
                    hum  = self._col(r, "humidity",    "hum",  "Humidity")
                    if temp: temps.append((t, temp))
                    if hum:  hums.append((t, hum))

            if not hrs and not brs and not temps and not hums:
                self.session_var.set("No data found in session.")
                return

            def unzip(lst): return zip(*lst) if lst else ([], [])
            hr_t,  hr_v  = unzip(hrs)
            br_t,  br_v  = unzip(brs)
            tmp_t, tmp_v = unzip(temps)
            hum_t, hum_v = unzip(hums)
            hr_v  = list(hr_v);  hr_t  = list(hr_t)
            br_v  = list(br_v);  br_t  = list(br_t)
            tmp_v = list(tmp_v); tmp_t = list(tmp_t)
            hum_v = list(hum_v); hum_t = list(hum_t)

            # Downsample: bucket into 30-second bins and average each bucket
            def bucket_avg(t_list, v_list, bucket=0.5):
                """Average values into time buckets (bucket size in minutes)."""
                if not t_list:
                    return [], []
                from math import floor
                buckets = {}
                for t, v in zip(t_list, v_list):
                    key = round(floor(t / bucket) * bucket, 2)
                    buckets.setdefault(key, []).append(v)
                keys = sorted(buckets)
                return keys, [sum(buckets[k])/len(buckets[k]) for k in keys]

            hr_t,  hr_v  = bucket_avg(hr_t,  hr_v)
            br_t,  br_v  = bucket_avg(br_t,  br_v)
            tmp_t, tmp_v = bucket_avg(tmp_t, tmp_v)
            hum_t, hum_v = bucket_avg(hum_t, hum_v)

            ax = self.an_ax1
            ax.cla()
            ax.set_facecolor("#0d0c2a")
            if hr_v: ax.plot(hr_t, hr_v, color=C_HR, linewidth=1.8, label="Heart Rate")
            if br_v: ax.plot(br_t, br_v, color=C_BR, linewidth=1.8, label="Breathing Rate")
            if hr_v or br_v:
                ax.legend(loc="upper right", facecolor=C_CARD2, labelcolor=C_TEXT, fontsize=8, framealpha=0.9)
            ax.set_xlabel("Time (min)", color=C_MUTED, fontsize=7)
            ax.set_ylabel("BPM",        color=C_MUTED, fontsize=7)
            ax.tick_params(colors=C_MUTED, labelsize=6)
            ax.grid(True, color=C_BORDER, linewidth=0.4, alpha=0.5)
            for sp in ax.spines.values(): sp.set_edgecolor(C_BORDER)
            self.an_fig1.tight_layout(pad=0.5)
            self.an_canvas1.draw_idle()

            ax2 = self.an_ax2
            ax2.cla()
            ax2.set_facecolor("#0d0c2a")
            if tmp_v: ax2.plot(tmp_t, tmp_v, color=C_TEMP, linewidth=1.8, label="Temp (°C)")
            if hum_v: ax2.plot(hum_t, hum_v, color=C_HUM,  linewidth=1.8, label="Humidity (%)")
            if tmp_v or hum_v:
                ax2.legend(loc="upper right", facecolor=C_CARD2, labelcolor=C_TEXT, fontsize=8, framealpha=0.9)
            ax2.set_xlabel("Time (min)", color=C_MUTED, fontsize=7)
            ax2.tick_params(colors=C_MUTED, labelsize=6)
            ax2.grid(True, color=C_BORDER, linewidth=0.4, alpha=0.5)
            for sp in ax2.spines.values(): sp.set_edgecolor(C_BORDER)
            self.an_fig2.tight_layout(pad=0.5)
            self.an_canvas2.draw_idle()

            def avg(lst): return round(sum(lst)/len(lst), 1) if lst else "--"
            duration = round(max(hr_t + br_t + [0]), 1) if (hr_t or br_t) else "--"
            self.stat_labels["Avg HR"].configure(text=avg(hr_v))
            self.stat_labels["Avg BR"].configure(text=avg(br_v))
            self.stat_labels["Avg Temp"].configure(text=avg(tmp_v))
            self.stat_labels["Avg Hum"].configure(text=avg(hum_v))
            self.stat_labels["Duration"].configure(text=str(duration))

            insights = self._generate_insights(hr_v, br_v, tmp_v, hum_v, duration)
            self.insights_text.configure(state="normal")
            self.insights_text.delete("1.0", "end")
            self.insights_text.insert("end", insights)
            self.insights_text.configure(state="disabled")

        except Exception as e:
            self.session_var.set(f"Error loading file: {e}")

    def _generate_insights(self, hrs, brs, temps, hums, duration):
        lines = []
        if not hrs and not brs:
            return "Not enough data in this session to generate insights."
        if hrs:
            avg_hr = sum(hrs) / len(hrs)
            if avg_hr < 55:
                lines.append(f"Avg Heart Rate: {avg_hr:.1f} bpm — Very low. May indicate deep relaxation or bradycardia.")
            elif avg_hr <= 70:
                lines.append(f"Avg Heart Rate: {avg_hr:.1f} bpm — Excellent resting range.")
            elif avg_hr <= 85:
                lines.append(f"Avg Heart Rate: {avg_hr:.1f} bpm — Slightly elevated. Try relaxing before sleep.")
            else:
                lines.append(f"Avg Heart Rate: {avg_hr:.1f} bpm — High. Consider a calming routine.")
        if brs:
            avg_br = sum(brs) / len(brs)
            if 12 <= avg_br <= 16:
                lines.append(f"Avg Breathing Rate: {avg_br:.1f} bpm — Normal and healthy.")
            elif avg_br < 12:
                lines.append(f"Avg Breathing Rate: {avg_br:.1f} bpm — Below normal.")
            else:
                lines.append(f"Avg Breathing Rate: {avg_br:.1f} bpm — Above normal. Try 4-7-8 breathing.")
        if temps:
            avg_t = sum(temps) / len(temps)
            if 18 <= avg_t <= 22:
                lines.append(f"Room Temp: {avg_t:.1f}°C — Ideal for sleep.")
            elif avg_t > 24:
                lines.append(f"Room Temp: {avg_t:.1f}°C — A bit warm. Cooler rooms improve sleep quality.")
            else:
                lines.append(f"Room Temp: {avg_t:.1f}°C — A bit cool. Consider a light blanket.")
        if hums:
            avg_h = sum(hums) / len(hums)
            if 40 <= avg_h <= 60:
                lines.append(f"Humidity: {avg_h:.1f}% — Comfortable range.")
            elif avg_h > 65:
                lines.append(f"Humidity: {avg_h:.1f}% — High. Consider a dehumidifier.")
            else:
                lines.append(f"Humidity: {avg_h:.1f}% — Low. Dry air may irritate airways.")
        if duration != "--":
            lines.append(f"Session Duration: {duration} min")
            if float(duration) < 20:
                lines.append("Tip: Try monitoring for at least 30 minutes for meaningful analysis.")
        return "\n\n".join(lines) if lines else "No insights available."

    # ── Storage helpers ───────────────────────────────────────────────────────
    def get_storage_info(self):
        try:
            base_dir  = "/home/raspberry/Desktop/SLEEPDOC/Session Reports"
            os.makedirs(base_dir, exist_ok=True)
            csv_files  = glob.glob(os.path.join(base_dir, "**", "*.csv"), recursive=True)
            used_bytes = sum(os.path.getsize(f) for f in csv_files if os.path.exists(f))
            import shutil
            _, _, free = shutil.disk_usage(base_dir)
            return {
                "total_files": len(csv_files),
                "used_space":  f"{used_bytes / (1024*1024):.2f}",
                "free_space":  f"{free / (1024*1024):.0f}",
            }
        except Exception:
            return {"total_files": 0, "used_space": "0.00", "free_space": "Unknown"}

    def refresh_file_list(self):
        try:
            for widget in self.files_scroll_frame.winfo_children():
                widget.destroy()

            base_dir = "/home/raspberry/Desktop/SLEEPDOC/Session Reports"
            os.makedirs(base_dir, exist_ok=True)

            # Collect session folders
            session_folders = []
            for day_dir in sorted(glob.glob(os.path.join(base_dir, "*")), reverse=True):
                if os.path.isdir(day_dir):
                    for sess_dir in sorted(glob.glob(os.path.join(day_dir, "*")), reverse=True):
                        if os.path.isdir(sess_dir):
                            session_folders.append(sess_dir)

            if not session_folders:
                ctk.CTkLabel(self.files_scroll_frame, text="No session folders found",
                             font=self.ctk_font_medium, text_color=C_MUTED).pack(pady=20)
                return

            for sess_path in session_folders:
                day_name  = os.path.basename(os.path.dirname(sess_path))
                sess_name = os.path.basename(sess_path)
                files     = glob.glob(os.path.join(sess_path, "*.csv"))
                num_files = len(files)
                try:
                    size_kb = sum(os.path.getsize(f) for f in files if os.path.isfile(f)) / 1024
                except Exception:
                    size_kb = 0

                row_frame = ctk.CTkFrame(self.files_scroll_frame, fg_color=C_CARD2, corner_radius=10)
                row_frame.pack(fill="x", pady=5, padx=10)
                row_frame.grid_columnconfigure(1, weight=1)

                has_report = os.path.isfile(os.path.join(sess_path, "sleep_report.pdf"))

                ctk.CTkLabel(row_frame, text="[F]", font=self.ctk_font_small,
                             text_color=C_ACCENT).grid(row=0, column=0, padx=10, pady=10)
                ctk.CTkLabel(row_frame,
                             text=f"{day_name}  /  {sess_name}\n{num_files} CSV files  •  {size_kb:.1f} KB"
                                  + ("  •  Report ready" if has_report else ""),
                             font=self.ctk_font_small, justify="left").grid(
                             row=0, column=1, padx=10, pady=10, sticky="w")
                ctk.CTkButton(row_frame, text="View", width=70, height=30,
                              fg_color=C_ACCENT2, hover_color=C_ACCENT_HOV,
                              command=lambda sp=sess_path, sn=sess_name: self.view_session(sp, sn)).grid(
                              row=0, column=2, padx=5, pady=10)
                if has_report:
                    ctk.CTkButton(row_frame, text="Report", width=70, height=30,
                                  fg_color=C_SUCCESS, hover_color=C_ACCENT_HOV,
                                  command=lambda sp=sess_path, sn=sess_name, dn=day_name:
                                      self.show_report_qr(dn, sn)).grid(
                                  row=0, column=3, padx=5, pady=10)
                ctk.CTkButton(row_frame, text="Delete", width=70, height=30,
                              fg_color="#dc3545", hover_color="#c82333",
                              command=lambda sp=sess_path, sn=sess_name: self.delete_session(sp, sn)).grid(
                              row=0, column=4, padx=5, pady=10)

                # files_scroll_frame is now a plain frame (the page itself
                # scrolls), so each row binds straight to the page's own
                # canvas — there is only ever one scroll region on this page.
                _touch_scroll_bind_tree(self.data_storage_frame._parent_canvas, row_frame)
        except Exception as e:
            print(f"Error refreshing file list: {e}")

    def show_report_qr(self, day_name, sess_name):
        """Popup with a QR code pointing at this session's PDF report, so it
        can be scanned and opened on a phone right from the touchscreen."""
        report_url = (f"http://{self.local_ip}:{FLASK_PORT}/reports/"
                      f"{quote(day_name)}/{quote(sess_name)}/sleep_report.pdf")

        popup = ctk.CTkToplevel(self)
        popup.title("Scan to view report")
        popup.geometry("380x480")
        # grab_set() requires the window to already be mapped/viewable —
        # calling it immediately after creation can fail with "window not
        # viewable" if the window manager hasn't realized it yet, and an
        # uncaught TclError here stalls the whole Tkinter event loop (which
        # is also what drives vitals polling and button clicks). Force the
        # geometry/layout to finish first, and don't let a failed grab take
        # down the rest of the app — the popup still works fine without it,
        # just without modal focus-stealing.
        popup.update_idletasks()
        try:
            popup.grab_set()
        except Exception as e:
            print(f"[Report QR] grab_set skipped: {e}")

        ctk.CTkLabel(popup, text="Sleep Report", font=self.ctk_font_subtitle,
                     text_color=C_ACCENT).pack(pady=(16, 4))
        ctk.CTkLabel(popup, text="Scan with your phone's camera",
                     font=self.ctk_font_small, text_color=C_MUTED).pack(pady=(0, 12))

        qr_label = ctk.CTkLabel(popup, text="Loading QR...", font=self.ctk_font_small)
        qr_label.pack(pady=10)

        url_label = ctk.CTkLabel(popup, text=f"Or open manually:\n{report_url}",
                                  font=self.ctk_font_small, text_color=C_TEXT,
                                  wraplength=320, justify="center")
        url_label.pack(pady=(10, 6), padx=15)

        ctk.CTkButton(popup, text="Close", command=popup.destroy,
                      width=120, height=36, corner_radius=18).pack(pady=(10, 16))

        def load_qr():
            try:
                qr_api = (f"http://{self.local_ip}:{FLASK_PORT}/qr"
                          f"?data={quote(report_url, safe='')}")
                resp = requests.get(qr_api, timeout=5)
                if resp.status_code == 200:
                    tmp_path = "/tmp/report_qr_tmp.png"
                    with open(tmp_path, "wb") as f:
                        f.write(resp.content)
                    img = load_icon(tmp_path, (260, 260), fallback_text="QR failed")
                    qr_label.configure(image=img, text="")
                else:
                    qr_label.configure(text="Could not generate QR\n(use the URL below)")
            except Exception as e:
                qr_label.configure(text=f"Could not reach device\n(use the URL below)")

        self.after(100, load_qr)

    def view_session(self, sess_path, sess_name):
        """Show a popup with the contents of all CSVs in the session folder."""
        popup = ctk.CTkToplevel(self)
        popup.title(f"Session: {sess_name}")
        popup.geometry("700x500")
        popup.update_idletasks()
        try:
            popup.grab_set()
        except Exception as e:
            print(f"[View Session] grab_set skipped: {e}")

        ctk.CTkLabel(popup, text=sess_name, font=self.ctk_font_subtitle,
                     text_color=C_ACCENT).pack(pady=(12, 4))

        text_box = ctk.CTkTextbox(popup, fg_color=C_CARD2, text_color=C_TEXT,
                                  font=self.ctk_font_small, corner_radius=10)
        text_box.pack(fill="both", expand=True, padx=15, pady=(0, 10))

        csv_files = sorted(glob.glob(os.path.join(sess_path, "*.csv")))
        if not csv_files:
            text_box.insert("end", "No CSV files found in this session.")
        else:
            for csv_path in csv_files:
                text_box.insert("end", f"=== {os.path.basename(csv_path)} ===\n")
                try:
                    result = subprocess.run(
                        ["sudo", "cat", csv_path],
                        capture_output=True, text=True, timeout=5)
                    lines = result.stdout.split("\n")
                    preview = "\n".join(lines[:50])
                    if len(lines) > 50:
                        preview += f"\n... ({len(lines)} total rows)\n"
                    text_box.insert("end", preview + "\n\n")
                except Exception as e:
                    text_box.insert("end", f"Error reading: {e}\n\n")

        text_box.configure(state="disabled")
        ctk.CTkButton(popup, text="Close", command=popup.destroy,
                      width=120, height=36, corner_radius=18).pack(pady=(0, 12))

    def view_file(self, file_path):
        try:
            # Files are root-owned (backend runs as sudo) — use sudo cat
            result = subprocess.run(
                ["sudo", "cat", file_path],
                capture_output=True, text=True, timeout=5)
            if result.returncode != 0:
                raise PermissionError(result.stderr.strip())
            content = result.stdout
            self.file_content_text.configure(state="normal")
            self.file_content_text.delete("1.0", "end")
            lines = content.split("\n")
            if len(lines) > 100:
                content = "\n".join(lines[:100]) + f"\n\n... (showing first 100 of {len(lines)} lines)"
            self.file_content_text.insert("1.0", content)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to read file:\n{e}")

    def delete_session(self, sess_path, sess_name):
        """Delete an entire session folder (all CSVs, JSON, PDF inside it).
        Uses sudo rm -rf because the folder was created by the root backend."""
        msg = f"Delete session '{sess_name}'?\n\nThis will remove all data files and the report. This cannot be undone!"
        if not messagebox.askyesno("Confirm Delete", msg):
            return
        try:
            result = subprocess.run(
                ["sudo", "rm", "-rf", sess_path],
                capture_output=True, text=True, timeout=10)
            if result.returncode != 0:
                raise PermissionError(result.stderr.strip())

            # Also remove the parent day-folder if it is now empty
            day_folder = os.path.dirname(sess_path)
            try:
                remaining = subprocess.run(
                    ["sudo", "find", day_folder, "-mindepth", "1", "-type", "d"],
                    capture_output=True, text=True).stdout.strip()
                if not remaining:
                    subprocess.run(["sudo", "rm", "-rf", day_folder], timeout=5)
            except Exception:
                pass

            messagebox.showinfo("Deleted", f"Session '{sess_name}' deleted.")
            self.refresh_file_list()
        except Exception as e:
            messagebox.showerror("Error", f"Failed to delete session:\n{e}")

    def delete_file(self, file_path):
        file_name = os.path.basename(file_path)
        if messagebox.askyesno("Confirm Delete", f"Delete '{file_name}'?"):
            try:
                result = subprocess.run(
                    ["sudo", "rm", "-f", file_path],
                    capture_output=True, text=True, timeout=5)
                if result.returncode != 0:
                    raise PermissionError(result.stderr.strip())
                parent = os.path.dirname(file_path)
                try:
                    remaining = subprocess.run(
                        ["sudo", "find", parent, "-type", "f"],
                        capture_output=True, text=True).stdout.strip()
                    if not remaining:
                        subprocess.run(["sudo", "rm", "-rf", parent], timeout=5)
                except Exception:
                    pass
                messagebox.showinfo("Deleted", f"'{file_name}' deleted.")
                self.refresh_file_list()
            except Exception as e:
                messagebox.showerror("Error", f"Failed to delete:\n{e}")

    def delete_all_files(self):
        base_dir = "/home/raspberry/Desktop/SLEEPDOC/Session Reports"
        try:
            result = subprocess.run(
                ["sudo", "find", base_dir, "-type", "f"],
                capture_output=True, text=True, timeout=5)
            all_files = [f for f in result.stdout.strip().split("\n") if f]
        except Exception:
            all_files = []
        if not all_files:
            messagebox.showinfo("Info", "No session files to delete!")
            return
        if messagebox.askyesno("Confirm Delete All",
                               f"Delete ALL {len(all_files)} files in all sessions?\nThis cannot be undone!"):
            try:
                subprocess.run(
                    ["sudo", "find", base_dir, "-mindepth", "1", "-delete"],
                    capture_output=True, text=True, timeout=10)
                messagebox.showinfo("Success", "All session data deleted.")
            except Exception as e:
                messagebox.showerror("Error", f"Failed to delete all:\n{e}")
            self.refresh_file_list()

    # ── Secret exit ───────────────────────────────────────────────────────────
    def handle_secret_click(self, event=None):
        now = time.time()
        if now - self.last_tap_time > 3:
            self.secret_tap_count = 0
        self.last_tap_time    = now
        self.secret_tap_count += 1
        if self.secret_tap_count >= 10:
            self.secret_tap_count = 0
            self.attributes("-fullscreen", False)
            self.config(cursor="arrow")
            if messagebox.askyesno("Unlock", "Do you want to exit Sleep Doc?"):
                self.exit_app()
            else:
                self.attributes("-fullscreen", True)
                self.config(cursor="none")

    def secret_exit(self, event=None):
        self.attributes("-fullscreen", False)
        self.config(cursor="arrow")
        if messagebox.askyesno("Exit", "Do you want to close Sleep Doc?"):
            self.exit_app()

    def exit_app(self):
        global vitals_process
        # 0. Signal all polling loops to stop
        self._alive = False
        # 1. Tell the backend to stop session and turn LEDs off
        try:
            requests.post(
                f"http://{self.local_ip}:{FLASK_PORT}/control",
                json={"light_on": False, "led_mode": "", "led_effect": "breathing",
                      "led_brightness": 1.0, "monitoring": False},
                timeout=2)
        except Exception:
            pass
        time.sleep(0.4)
        # 2. Kill the backend
        if vitals_process and vitals_process.poll() is None:
            try:
                subprocess.run(["sudo", "kill", str(vitals_process.pid)], timeout=3)
            except Exception:
                pass
            try:
                subprocess.run(["sudo", "pkill", "-f", "vsd_on_startup.py"], timeout=3)
            except Exception:
                pass
        # 3. Clean up temp files
        for tf in ["/tmp/stop_vitals", "/tmp/live_vitals.txt", "/tmp/vsd_selection.json"]:
            try:
                if os.path.exists(tf):
                    os.remove(tf)
            except Exception:
                pass
        # 4. Use quit() + destroy() — quit() stops mainloop cleanly on Python 3.9
        #    wrap destroy() in try/except so TclError doesn't crash the process
        try:
            self.quit()
        except Exception:
            pass
        try:
            self.destroy()
        except Exception:
            pass

    # ── Analysis display stub (kept for compatibility) ─────────────────────────
    def update_analysis_display(self):
        pass


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    splash = SplashScreen()
    splash.mainloop()