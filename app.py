"""
TM Ripper  (by TheMannster)
---------------------------
A desktop GUI to download videos from TikTok, Instagram Reels, Facebook Reels,
and YouTube Shorts by pasting (or dropping) a link.

Features:
  * Two switchable looks: Legacy (modern flat) and Retro (Windows 95)
  * Preview title + thumbnail before downloading
  * Cancel/Stop an in-progress download
  * Live progress with speed, ETA, and file size
  * Open the file / show it in its folder when done
  * Download history with convert-to-other-formats (via ffmpeg)
  * Convert any local media file
  * Drag-and-drop a link onto the window
  * One-click "Update downloader" (keeps yt-dlp current)

Powered by yt-dlp. Run with:  pythonw app.py
"""

import io
import os
import re
import sys
import json
import time
import queue
import tempfile
import shutil
import zipfile
import threading
import subprocess
import urllib.request
import tkinter as tk
from tkinter import filedialog

try:
    import winsound  # Windows-only; used for our own notification sounds
except Exception:  # pragma: no cover
    winsound = None

try:
    import yt_dlp
except ImportError:  # pragma: no cover - handled at runtime
    yt_dlp = None

try:  # graceful stop of an in-progress download
    from yt_dlp.utils import DownloadCancelled
except Exception:  # pragma: no cover
    class DownloadCancelled(Exception):
        pass

try:
    from PIL import Image, ImageTk
except ImportError:  # pragma: no cover
    Image = None
    ImageTk = None

try:
    from tkinterdnd2 import TkinterDnD, DND_TEXT, DND_FILES
    _DND_AVAILABLE = True
except Exception:  # pragma: no cover
    TkinterDnD = None
    DND_TEXT = DND_FILES = None
    _DND_AVAILABLE = False

try:
    from pypresence import Presence
except Exception:  # pragma: no cover
    Presence = None


APP_TITLE = "TM Ripper"
APP_VERSION = "1.2.2"
GITHUB_REPO = "TheMannster/TM-Ripper"
RELEASES_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
HISTORY_MAX = 100

# Discord Rich Presence. Create an app at https://discord.com/developers/applications
# then paste its Application (Client) ID here. Upload the logo under
# "Rich Presence > Art Assets" with the key name "tm_logo". Leave "" to disable.
DISCORD_CLIENT_ID = os.environ.get("TMRIPPER_DISCORD_ID", "1522693674503241968")
DISCORD_LARGE_IMAGE = "tm_logo"
DEFAULT_DOWNLOAD_DIR = os.path.join(os.path.expanduser("~"), "Downloads")

if getattr(sys, "frozen", False):
    # Running as a PyInstaller .exe.
    BUNDLE_DIR = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))  # bundled assets
    APP_DIR = os.path.dirname(sys.executable)  # install dir (ffmpeg lives here)
    # Settings go to %APPDATA% since Program Files isn't user-writable.
    CONFIG_DIR = os.path.join(os.environ.get("APPDATA") or APP_DIR, "TMRipper")
else:
    BUNDLE_DIR = os.path.dirname(os.path.abspath(__file__))
    APP_DIR = BUNDLE_DIR
    CONFIG_DIR = BUNDLE_DIR

try:
    os.makedirs(CONFIG_DIR, exist_ok=True)
except OSError:
    CONFIG_DIR = APP_DIR

SETTINGS_PATH = os.path.join(CONFIG_DIR, "settings.json")
HISTORY_PATH = os.path.join(CONFIG_DIR, "history.json")
ICON_ICO = os.path.join(BUNDLE_DIR, "assets", "icon.ico")
ICON_PNG = os.path.join(BUNDLE_DIR, "assets", "icon.png")
SOUND_NOTIFY = os.path.join(BUNDLE_DIR, "assets", "notify.wav")
SOUND_ERROR = os.path.join(BUNDLE_DIR, "assets", "error.wav")


def find_ffmpeg_dir():
    """Locate a bundled ffmpeg so the app works without a system install.

    Checks (in order) next to the exe, a bundled 'ffmpeg' folder, and the
    dev-time 'vendor/ffmpeg' folder. Returns the directory or None (in which
    case yt-dlp falls back to any ffmpeg on the system PATH).
    """
    candidates = [
        APP_DIR,
        os.path.join(APP_DIR, "ffmpeg"),
        BUNDLE_DIR,
        os.path.join(BUNDLE_DIR, "ffmpeg"),
        os.path.join(BUNDLE_DIR, "vendor", "ffmpeg"),
    ]
    exe_name = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
    for d in candidates:
        if os.path.isfile(os.path.join(d, exe_name)):
            return d
    return None


FFMPEG_DIR = find_ffmpeg_dir()


def ffmpeg_executable() -> str | None:
    """Full path to ffmpeg, or 'ffmpeg' if only on PATH, or None if missing."""
    name = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
    if FFMPEG_DIR:
        path = os.path.join(FFMPEG_DIR, name)
        if os.path.isfile(path):
            return path
    found = shutil.which(name)
    return found


# Display label -> (extension, ffmpeg args after -i input, before output)
CONVERT_TARGETS = {
    "MP4 (H.264 video)": ("mp4", ["-c:v", "libx264", "-preset", "fast", "-crf", "23",
                                  "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"]),
    "MKV (remux/copy)": ("mkv", ["-c", "copy"]),
    "WebM (VP9 video)": ("webm", ["-c:v", "libvpx-vp9", "-crf", "32", "-b:v", "0",
                                  "-c:a", "libopus", "-b:a", "128k"]),
    "MOV (H.264 video)": ("mov", ["-c:v", "libx264", "-preset", "fast", "-crf", "23",
                                  "-c:a", "aac", "-b:a", "192k"]),
    "GIF (animated)": ("gif", ["-vf", "fps=12,scale=480:-1:flags=lanczos", "-loop", "0"]),
    "MP3 (audio)": ("mp3", ["-vn", "-c:a", "libmp3lame", "-b:a", "192k"]),
    "M4A (audio)": ("m4a", ["-vn", "-c:a", "aac", "-b:a", "192k"]),
    "WAV (audio)": ("wav", ["-vn", "-c:a", "pcm_s16le"]),
    "FLAC (audio)": ("flac", ["-vn", "-c:a", "flac"]),
    "OGG (audio)": ("ogg", ["-vn", "-c:a", "libvorbis", "-q:a", "5"]),
}

MEDIA_FILETYPES = [
    ("Media files", "*.mp4 *.mkv *.webm *.mov *.avi *.m4v *.mp3 *.m4a *.wav *.flac "
                    "*.ogg *.opus *.gif *.aac *.wma"),
    ("All files", "*.*"),
]

THEME_RETRO = "retro"
THEME_LEGACY = "legacy"

# --- Windows 95 palette -------------------------------------------------
FACE = "#c0c0c0"
SHADOW = "#808080"
DARKEDGE = "#000000"
LIGHT = "#ffffff"
NAVY = "#000080"
BTN_TEXT = "#000000"

WIN95_FONT = ("MS Sans Serif", 8)
WIN95_FONT_BOLD = ("MS Sans Serif", 8, "bold")
WIN95_TITLE_FONT = ("MS Sans Serif", 11, "bold")
WIN95_MONO = ("Courier New", 8)

# --- Modern (legacy) palette : deep dark, easy on the eyes --------------
PRIMARY = "#e11d2a"          # TM crimson
PRIMARY_ACTIVE = "#f5333f"   # brighter crimson (hover/pressed)
PRIMARY_DISABLED = "#5a2226"
PRIMARY_TEXT = "#ffffff"
BG_LEGACY = "#17181c"        # app background (near-black)
SURFACE_LEGACY = "#1f2126"   # cards / log
INPUT_LEGACY = "#26282e"     # input fields
BORDER_LEGACY = "#33363d"    # subtle 1px borders
BTN_LEGACY = "#2b2e35"       # neutral button face
BTN_LEGACY_ACTIVE = "#373b43"
TEXT_LEGACY = "#e9eaec"      # primary light text
SUBTEXT_LEGACY = "#8e96a2"   # muted text
HEADING_LEGACY = "#ffffff"
DISABLED_TEXT_LEGACY = "#5c6069"

# Modern fonts (Segoe UI ships with Windows; graceful fallback elsewhere)
UI_FONT = ("Segoe UI", 10)
UI_FONT_SM = ("Segoe UI", 9)
UI_FONT_BOLD = ("Segoe UI Semibold", 10)
UI_TITLE = ("Segoe UI Semibold", 20)
UI_LABEL = ("Segoe UI Semibold", 9)
UI_MONO = ("Consolas", 9)


def load_settings() -> dict:
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def save_settings(data: dict) -> None:
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
    except OSError:
        pass


def load_history() -> list:
    try:
        with open(HISTORY_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def save_history(entries: list) -> None:
    try:
        with open(HISTORY_PATH, "w", encoding="utf-8") as fh:
            json.dump(entries[:HISTORY_MAX], fh, indent=2)
    except OSError:
        pass


def history_add(entries: list, *, title: str, path: str, url: str = "",
                platform: str = "", quality: str = "") -> list:
    """Prepend a download/convert record and persist. Returns the new list."""
    entry = {
        "id": f"{int(time.time() * 1000)}-{os.path.basename(path or '')}",
        "title": title or os.path.basename(path or "file"),
        "path": path or "",
        "url": url or "",
        "platform": platform or "",
        "quality": quality or "",
        "ts": int(time.time()),
    }
    # Drop older duplicates of the same path so the newest stays on top.
    norm = os.path.normcase(os.path.normpath(path)) if path else ""
    filtered = [
        e for e in entries
        if os.path.normcase(os.path.normpath(e.get("path") or "")) != norm
    ]
    updated = [entry] + filtered
    save_history(updated)
    return updated


def default_convert_output(src: str, ext: str) -> str:
    base, _ = os.path.splitext(src)
    candidate = f"{base}.{ext}"
    if not os.path.exists(candidate):
        return candidate
    n = 2
    while True:
        candidate = f"{base} ({n}).{ext}"
        if not os.path.exists(candidate):
            return candidate
        n += 1


def run_ffmpeg_convert(src: str, dest: str, fmt_label: str,
                       on_stderr=None) -> None:
    """Convert src -> dest using CONVERT_TARGETS[fmt_label]. Raises on failure."""
    if fmt_label not in CONVERT_TARGETS:
        raise ValueError(f"Unknown format: {fmt_label}")
    ff = ffmpeg_executable()
    if not ff:
        raise FileNotFoundError(
            "ffmpeg was not found. Install ffmpeg or use the bundled build.")
    _ext, args = CONVERT_TARGETS[fmt_label]
    cmd = [ff, "-y", "-i", src, *args, dest]
    proc = subprocess.run(
        cmd, capture_output=True, text=True, timeout=3600,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    if on_stderr and proc.stderr:
        on_stderr(proc.stderr[-500:])
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "ffmpeg failed").strip()
        # Keep the useful tail — ffmpeg dumps are long.
        raise RuntimeError(err[-800:] if len(err) > 800 else err)
    if not os.path.isfile(dest) or os.path.getsize(dest) == 0:
        raise RuntimeError("Conversion finished but the output file is missing or empty.")


def parse_version(v: str) -> tuple:
    """Turn 'v1.2.3' / '1.2.3' into a comparable tuple (1, 2, 3)."""
    v = (v or "").strip().lstrip("vV")
    parts = []
    for chunk in v.split("."):
        digits = "".join(ch for ch in chunk if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts) if parts else (0,)


def is_newer_version(remote: str, local: str) -> bool:
    return parse_version(remote) > parse_version(local)


def detect_platform(url: str) -> str:
    u = url.lower()
    if "tiktok.com" in u:
        return "TikTok"
    if "instagram.com" in u:
        return "Instagram"
    if "facebook.com" in u or "fb.watch" in u or "fb.com" in u:
        return "Facebook"
    if "youtube.com" in u or "youtu.be" in u:
        return "YouTube"
    return "Unknown"


def human_size(num) -> str:
    if not num:
        return "?"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if num < 1024:
            return f"{num:.0f} {unit}" if unit == "B" else f"{num:.1f} {unit}"
        num /= 1024
    return f"{num:.1f} PB"


def human_time(secs) -> str:
    if secs is None:
        return "?"
    secs = int(secs)
    m, s = divmod(secs, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def first_url(text: str) -> str:
    match = re.search(r"https?://[^\s'\"<>]+", text or "")
    return match.group(0) if match else (text or "").strip()


def set_dark_titlebar(window, dark: bool):
    """Toggle the native Windows title bar between dark and light."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        window.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        value = ctypes.c_int(1 if dark else 0)
        for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (new, then legacy)
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, attr, ctypes.byref(value), ctypes.sizeof(value)
            ) == 0:
                break
    except Exception:
        pass


# ----------------------------------------------------------- Win95 helpers
def make_button(parent, text, command, bold=False, width=None):
    return tk.Button(
        parent, text=text, command=command,
        font=WIN95_FONT_BOLD if bold else WIN95_FONT,
        bg=FACE, fg=BTN_TEXT, activebackground=FACE, activeforeground=BTN_TEXT,
        relief="raised", bd=2, padx=8, pady=2, width=width, cursor="arrow",
        highlightthickness=1, highlightbackground=DARKEDGE,
    )


def make_entry(parent, textvariable):
    return tk.Entry(
        parent, textvariable=textvariable, font=WIN95_FONT, bg=LIGHT, fg="#000000",
        relief="sunken", bd=2, highlightthickness=0, insertbackground="#000000",
    )


def make_group(parent, text):
    return tk.LabelFrame(
        parent, text=text, font=WIN95_FONT, bg=FACE, fg="#000000",
        relief="groove", bd=2, padx=8, pady=6,
    )


class Win95Progress(tk.Canvas):
    """Classic segmented blue progress bar (little navy blocks)."""

    def __init__(self, master, **kw):
        super().__init__(
            master, height=22, bg=LIGHT, bd=2, relief="sunken", highlightthickness=0, **kw
        )
        self._value = 0
        self.bind("<Configure>", lambda e: self._redraw())

    def set(self, value):
        self._value = max(0, min(100, value))
        self._redraw()

    def _redraw(self):
        self.delete("all")
        w = self.winfo_width()
        h = self.winfo_height()
        if w <= 1:
            return
        block_w, gap = 10, 2
        filled = (self._value / 100.0) * (w - 4)
        x = 3
        while x + block_w <= 3 + filled:
            self.create_rectangle(x, 3, x + block_w, h - 3, fill=NAVY, outline=NAVY)
            x += block_w + gap


# ---------------------------------------------------------- Modern helpers
def mk_button(parent, text, command, kind="ghost", **kw):
    """Flat, hover-aware button for the modern theme.

    kind: "accent" (crimson call-to-action) or "ghost" (neutral surface).
    """
    if kind == "accent":
        base, hover, fg = PRIMARY, PRIMARY_ACTIVE, PRIMARY_TEXT
        font = ("Segoe UI Semibold", 11)
    else:
        base, hover, fg = BTN_LEGACY, BTN_LEGACY_ACTIVE, TEXT_LEGACY
        font = UI_FONT
    btn = tk.Button(
        parent, text=text, command=command, font=font,
        bg=base, fg=fg, activebackground=hover, activeforeground=fg,
        relief="flat", bd=0, cursor="hand2", padx=14, pady=8,
        highlightthickness=0, disabledforeground=DISABLED_TEXT_LEGACY, **kw,
    )
    btn._base_bg = base
    btn._hover_bg = hover

    def on_enter(_):
        if str(btn["state"]) != "disabled":
            btn.config(bg=hover)

    def on_leave(_):
        if str(btn["state"]) != "disabled":
            btn.config(bg=btn._base_bg)

    btn.bind("<Enter>", on_enter)
    btn.bind("<Leave>", on_leave)
    return btn


def mk_entry(parent, textvariable, font=("Segoe UI", 11)):
    """Flat entry wrapped in a 1px frame that glows crimson on focus.

    Returns (wrapper, entry). Pack/grid the wrapper; use the entry for refs.
    """
    wrap = tk.Frame(parent, bg=BORDER_LEGACY, bd=0, highlightthickness=0)
    inner = tk.Frame(wrap, bg=INPUT_LEGACY)
    inner.pack(fill="both", expand=True, padx=1, pady=1)
    entry = tk.Entry(
        inner, textvariable=textvariable, font=font, bg=INPUT_LEGACY, fg=TEXT_LEGACY,
        insertbackground=TEXT_LEGACY, relief="flat", bd=0, highlightthickness=0,
    )
    entry.pack(fill="both", expand=True, padx=10, pady=8)
    entry.bind("<FocusIn>", lambda _: wrap.config(bg=PRIMARY))
    entry.bind("<FocusOut>", lambda _: wrap.config(bg=BORDER_LEGACY))
    return wrap, entry


def mk_card(parent):
    return tk.Frame(parent, bg=SURFACE_LEGACY, bd=0,
                    highlightbackground=BORDER_LEGACY, highlightthickness=1)


def show_dark_menu(root, x, y, entries):
    """A borderless dark popup menu (no white native-menu border).

    entries: list of ("sep",) | ("cmd", label, callback) |
             ("check", label, callback, is_checked)
    """
    win = tk.Toplevel(root)
    win.overrideredirect(True)
    try:
        win.attributes("-topmost", True)
    except tk.TclError:
        pass
    border = tk.Frame(win, bg=BORDER_LEGACY)
    border.pack(fill="both", expand=True)
    body = tk.Frame(border, bg=SURFACE_LEGACY)
    body.pack(fill="both", expand=True, padx=1, pady=1)

    def close(_=None):
        if win.winfo_exists():
            win.destroy()

    has_check = any(e[0] == "check" for e in entries)
    for e in entries:
        if e[0] == "sep":
            tk.Frame(body, bg=BORDER_LEGACY, height=1).pack(fill="x", padx=8, pady=4)
            continue
        label, cb = e[1], e[2]
        if e[0] == "check":
            label = ("\u2713   " if len(e) > 3 and e[3] else "     ") + label
        elif has_check:
            label = "     " + label
        row = tk.Label(body, text=label, bg=SURFACE_LEGACY, fg=TEXT_LEGACY, font=UI_FONT,
                       anchor="w", padx=16, pady=7, cursor="hand2")
        row.pack(fill="x")
        row.bind("<Enter>", lambda _e, w=row: w.config(bg=PRIMARY, fg=PRIMARY_TEXT))
        row.bind("<Leave>", lambda _e, w=row: w.config(bg=SURFACE_LEGACY, fg=TEXT_LEGACY))
        row.bind("<Button-1>", lambda _e, c=cb: (close(), c()))

    win.update_idletasks()
    sw = win.winfo_screenwidth()
    sh = win.winfo_screenheight()
    x = min(x, sw - win.winfo_reqwidth() - 4)
    y = min(y, sh - win.winfo_reqheight() - 4)
    win.geometry(f"+{max(x, 0)}+{max(y, 0)}")
    win.bind("<Escape>", close)
    win.focus_force()
    win.after(60, lambda: win.winfo_exists() and win.bind("<FocusOut>", close))
    return win


def mk_dropdown(parent, var, values, width=20):
    """Flat modern dropdown: styled like an input with a chevron + dark menu."""
    wrap = tk.Frame(parent, bg=BORDER_LEGACY, bd=0, highlightthickness=0)
    inner = tk.Frame(wrap, bg=INPUT_LEGACY, cursor="hand2")
    inner.pack(fill="both", expand=True, padx=1, pady=1)
    value = tk.Label(inner, textvariable=var, bg=INPUT_LEGACY, fg=TEXT_LEGACY, font=UI_FONT,
                     anchor="w", width=width, cursor="hand2")
    value.pack(side="left", fill="x", expand=True, padx=(10, 4), pady=6)
    chevron = tk.Label(inner, text="\u25be", bg=INPUT_LEGACY, fg=SUBTEXT_LEGACY,
                       font=("Segoe UI", 9), cursor="hand2")
    chevron.pack(side="right", padx=(0, 10))

    def popup(_e=None):
        entries = [("check", v, lambda vv=v: var.set(vv), v == var.get()) for v in values]
        show_dark_menu(wrap.winfo_toplevel(),
                       wrap.winfo_rootx(), wrap.winfo_rooty() + wrap.winfo_height() + 2, entries)

    def on_enter(_):
        wrap.config(bg=SUBTEXT_LEGACY)
        chevron.config(fg=TEXT_LEGACY)

    def on_leave(_):
        wrap.config(bg=BORDER_LEGACY)
        chevron.config(fg=SUBTEXT_LEGACY)

    for w in (inner, value, chevron):
        w.bind("<Button-1>", popup)
        w.bind("<Enter>", on_enter)
        w.bind("<Leave>", on_leave)
    return wrap


def mk_caption(parent, text, bg):
    return tk.Label(parent, text=text.upper(), bg=bg, fg=SUBTEXT_LEGACY,
                    font=UI_LABEL, anchor="w")


class ModernBar(tk.Canvas):
    """Slim, rounded, single-fill progress bar for the modern theme."""

    def __init__(self, master, **kw):
        super().__init__(master, height=8, bg=INPUT_LEGACY, bd=0,
                         highlightthickness=0, **kw)
        self._value = 0
        self.bind("<Configure>", lambda e: self._redraw())

    def set(self, value):
        self._value = max(0, min(100, value))
        self._redraw()

    def _round_rect(self, x1, y1, x2, y2, r, color):
        if x2 - x1 < 2 * r:
            r = (x2 - x1) / 2
        self.create_oval(x1, y1, x1 + 2 * r, y2, fill=color, outline=color)
        self.create_oval(x2 - 2 * r, y1, x2, y2, fill=color, outline=color)
        self.create_rectangle(x1 + r, y1, x2 - r, y2, fill=color, outline=color)

    def _redraw(self):
        self.delete("all")
        w = self.winfo_width()
        h = self.winfo_height()
        if w <= 1:
            return
        r = h / 2
        self._round_rect(0, 0, w, h, r, INPUT_LEGACY)
        fill_w = (self._value / 100.0) * w
        if fill_w > 1:
            self._round_rect(0, 0, max(fill_w, h), h, r, PRIMARY)


class DiscordRP:
    """Thin wrapper around pypresence so TM Ripper shows as a Discord activity.

    Safe no-op if pypresence is missing, no client ID is set, or Discord isn't
    running. All calls should be made from the main thread except connect().
    """

    def __init__(self, client_id: str):
        self.client_id = client_id
        self.rpc = None
        self.connected = False
        self.start_ts = int(time.time())

    def connect(self):
        if not Presence or not self.client_id:
            return
        try:
            self.rpc = Presence(self.client_id)
            self.rpc.connect()
            self.connected = True
            self.set_idle()
        except Exception:
            self.connected = False
            self.rpc = None

    def _safe_update(self, **kwargs):
        if not self.connected or not self.rpc:
            return
        try:
            self.rpc.update(start=self.start_ts, large_image=DISCORD_LARGE_IMAGE,
                            large_text=f"{APP_TITLE} v{APP_VERSION}", **kwargs)
        except Exception:
            self.connected = False

    def set_idle(self):
        self._safe_update(state="Idle", details="Ready to rip")

    def set_downloading(self, platform: str):
        self._safe_update(state=f"Ripping a {platform} video", details="Downloading")

    def set_preview(self):
        self._safe_update(state="Previewing a video", details="Browsing")

    def close(self):
        try:
            if self.rpc:
                self.rpc.close()
        except Exception:
            pass
        self.connected = False


class DownloaderApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(APP_TITLE)
        # High-DPI: scale fonts (points) via Tk and the window (pixels) by the factor.
        dpi = _system_dpi()
        self.scale = max(1.0, dpi / 96.0)
        try:
            self.root.tk.call("tk", "scaling", dpi / 72.0)
        except tk.TclError:
            pass
        self.root.geometry(f"{int(520 * self.scale)}x{int(900 * self.scale)}")
        self.root.minsize(int(460 * self.scale), int(560 * self.scale))
        self._legacy_logo_ref = None
        self._toasts = []
        self._apply_window_icon()

        self.msg_queue: "queue.Queue[tuple]" = queue.Queue()
        self.is_downloading = False
        self.is_busy = False  # preview or update running
        self.cancel_event = threading.Event()
        self.last_file: str | None = None
        self._thumb_img = None  # keep a reference so Tk doesn't GC it

        settings = load_settings()
        self.theme = settings.get("theme", THEME_RETRO)
        self.theme_var = tk.StringVar(value=self.theme)

        saved_folder = settings.get("folder", DEFAULT_DOWNLOAD_DIR)
        # If a shared settings.json points somewhere that doesn't exist on this
        # PC (e.g. another person's user folder), fall back to this user's Downloads.
        if not os.path.isdir(os.path.dirname(saved_folder) or saved_folder):
            saved_folder = DEFAULT_DOWNLOAD_DIR

        self.url_var = tk.StringVar()
        self.folder_var = tk.StringVar(value=saved_folder)
        self.quality_var = tk.StringVar(value=settings.get("quality", "Best video + audio"))
        self.status_var = tk.StringVar(value="Ready.")
        self.sound_var = tk.BooleanVar(value=bool(settings.get("sound", True)))
        self.url_var.trace_add("write", self._on_url_change)
        # Persist folder/quality/sound the moment they change so settings never get lost.
        self.folder_var.trace_add("write", lambda *_: self._persist())
        self.quality_var.trace_add("write", lambda *_: self._persist())
        self.sound_var.trace_add("write", lambda *_: self._persist())

        self.log_history: list[tuple[str, bool]] = []
        self.download_history: list = load_history()
        self._history_win = None
        self._update_checked = False

        # Discord Rich Presence (shows the app as a game activity).
        self.discord = DiscordRP(DISCORD_CLIENT_ID)
        threading.Thread(target=self.discord.connect, daemon=True).start()

        self._build_all()
        self._poll_queue()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        # Auto-check GitHub for a newer release shortly after launch (installed builds only).
        if getattr(sys, "frozen", False):
            self.root.after(1500, lambda: self._check_app_updates(manual=False))

    def _on_close(self):
        self._persist()
        self.discord.close()
        self.root.destroy()

    # --------------------------------------------------------------- Icon
    def _apply_window_icon(self):
        try:
            if os.path.exists(ICON_ICO):
                self.root.iconbitmap(ICON_ICO)
        except Exception:
            pass
        try:
            if ImageTk and os.path.exists(ICON_PNG):
                self._win_icon = ImageTk.PhotoImage(Image.open(ICON_PNG))
                self.root.iconphoto(True, self._win_icon)
        except Exception:
            pass

    # -------------------------------------------------------- Theme control
    def _persist(self):
        save_settings({
            "theme": self.theme,
            "folder": self.folder_var.get(),
            "quality": self.quality_var.get(),
            "sound": bool(self.sound_var.get()),
        })

    def _apply_theme(self, theme: str):
        if theme not in (THEME_RETRO, THEME_LEGACY) or theme == self.theme:
            self.theme_var.set(self.theme)
            return
        self.theme = theme
        self.theme_var.set(theme)
        self._persist()
        self._rebuild()

    def _rebuild(self):
        for child in self.root.winfo_children():
            child.destroy()
        self.root.config(menu=tk.Menu(self.root))
        self._build_all()

    def _build_all(self):
        if self.theme == THEME_RETRO:
            self._build_menu()
            self.root.configure(bg=FACE)
            self._build_retro_ui()
        else:
            # Hide the native (light) menu bar; we draw our own dark one instead.
            self.root.config(menu=tk.Menu(self.root))
            self.root.configure(bg=BG_LEGACY)
            self._build_legacy_ui()
        set_dark_titlebar(self.root, self.theme == THEME_LEGACY)
        self._restore_log()
        self._on_url_change()
        self._register_dnd()
        self._update_action_buttons()

    # ------------------------------------------------------------ Menu bar
    def _menu_spec(self):
        return [
            ("File", [
                ("command", "Open save folder", self._open_folder),
                ("command", "Download history\u2026", self._open_history),
                ("command", "Convert file\u2026", lambda: self._open_convert_dialog()),
                ("sep",),
                ("command", "Exit", self._on_close),
            ]),
            ("Settings", [
                ("command", "Preferences\u2026", self._open_settings),
                ("sep",),
                ("radio", "Legacy (Modern) look", THEME_LEGACY),
                ("radio", "Retro (Windows 95) look", THEME_RETRO),
            ]),
            ("Tools", [
                ("command", "Check for updates\u2026", lambda: self._check_app_updates(manual=True)),
                ("command", "Update video engine (yt-dlp)", self._update_downloader),
            ]),
            ("Help", [
                ("command", "About\u2026", self._about),
            ]),
        ]

    def _populate_menu(self, menu, items):
        for it in items:
            if it[0] == "sep":
                menu.add_separator()
            elif it[0] == "command":
                menu.add_command(label=it[1], command=it[2])
            elif it[0] == "radio":
                menu.add_radiobutton(label=it[1], variable=self.theme_var, value=it[2],
                                     command=lambda v=it[2]: self._apply_theme(v))

    def _build_menu(self):
        menubar = tk.Menu(self.root, tearoff=0)
        for title, items in self._menu_spec():
            sub = tk.Menu(menubar, tearoff=0)
            self._populate_menu(sub, items)
            menubar.add_cascade(label=title, menu=sub)
        self.root.config(menu=menubar)

    def _spec_to_entries(self, items):
        entries = []
        for it in items:
            if it[0] == "sep":
                entries.append(("sep",))
            elif it[0] == "command":
                entries.append(("cmd", it[1], it[2]))
            elif it[0] == "radio":
                entries.append(("check", it[1],
                                (lambda v=it[2]: self._apply_theme(v)), it[2] == self.theme))
        return entries

    def _build_menubar_legacy(self, parent):
        """Custom dark menu bar so it matches the modern theme."""
        bar = tk.Frame(parent, bg=BG_LEGACY)
        for title, items in self._menu_spec():
            btn = tk.Label(bar, text=title, bg=BG_LEGACY, fg=TEXT_LEGACY, font=UI_FONT,
                           padx=12, pady=7, cursor="hand2")
            btn.bind("<Button-1>", lambda _e, it=items, b=btn: show_dark_menu(
                self.root, b.winfo_rootx(), b.winfo_rooty() + b.winfo_height(),
                self._spec_to_entries(it)))
            btn.bind("<Enter>", lambda _e, b=btn: b.config(bg=BTN_LEGACY))
            btn.bind("<Leave>", lambda _e, b=btn: b.config(bg=BG_LEGACY))
            btn.pack(side="left")
        return bar

    def _about(self):
        self._alert(
            "About " + APP_TITLE,
            APP_TITLE + " by TheMannster\n\nDownloads TikTok, Instagram Reels, Facebook Reels, "
            "and YouTube Shorts.\nConvert downloads (or any media file) to other formats.\n\n"
            "Powered by yt-dlp + ffmpeg.",
            kind="info",
        )

    # -------------------------------------------------- Notifications
    def _dialog_palette(self):
        if self.theme == THEME_RETRO:
            return {"bg": FACE, "surface": LIGHT, "fg": "#000000", "sub": SHADOW,
                    "border": SHADOW, "font": WIN95_FONT, "title": WIN95_FONT_BOLD, "dark": False}
        return {"bg": BG_LEGACY, "surface": SURFACE_LEGACY, "fg": TEXT_LEGACY,
                "sub": SUBTEXT_LEGACY, "border": BORDER_LEGACY, "font": UI_FONT,
                "title": ("Segoe UI Semibold", 11), "dark": True}

    _KIND_COLORS = {"info": "#3b82f6", "success": "#22c55e",
                    "warning": "#f59e0b", "error": "#ef4444"}

    def _play_sound(self, kind):
        if not self.sound_var.get() or winsound is None:
            return
        path = SOUND_ERROR if kind in ("error", "warning") else SOUND_NOTIFY
        if not os.path.exists(path):
            return
        try:
            winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
        except Exception:
            pass

    def _notify(self, message, kind="info", duration=4000):
        """Own toast pop-up (no Windows system sound)."""
        self._play_sound(kind)
        pal = self._dialog_palette()
        accent = self._KIND_COLORS.get(kind, self._KIND_COLORS["info"])

        toast = tk.Toplevel(self.root)
        toast.overrideredirect(True)
        toast.attributes("-topmost", True)
        try:
            toast.attributes("-alpha", 0.0)
        except tk.TclError:
            pass
        border = tk.Frame(toast, bg=pal["border"])
        border.pack(fill="both", expand=True)
        body = tk.Frame(border, bg=pal["surface"])
        body.pack(fill="both", expand=True, padx=1, pady=1)
        tk.Frame(body, bg=accent, width=4).pack(side="left", fill="y")
        inner = tk.Frame(body, bg=pal["surface"])
        inner.pack(side="left", fill="both", expand=True, padx=14, pady=12)
        tk.Label(inner, text=message, bg=pal["surface"], fg=pal["fg"], font=pal["font"],
                 justify="left", anchor="w", wraplength=320).pack(anchor="w")

        toast.bind("<Button-1>", lambda _e: self._dismiss_toast(toast))
        for child in (border, body, inner):
            child.bind("<Button-1>", lambda _e: self._dismiss_toast(toast))

        self._toasts.append(toast)
        self._reflow_toasts()
        self._fade_toast(toast, 0.0, up=True)
        toast.after(duration, lambda: self._dismiss_toast(toast))

    def _fade_toast(self, toast, alpha, up=True):
        if not toast.winfo_exists():
            return
        alpha = alpha + 0.12 if up else alpha - 0.14
        alpha = max(0.0, min(0.96, alpha))
        try:
            toast.attributes("-alpha", alpha)
        except tk.TclError:
            return
        if up and alpha < 0.96:
            toast.after(16, lambda: self._fade_toast(toast, alpha, up=True))
        elif not up and alpha > 0.0:
            toast.after(16, lambda: self._fade_toast(toast, alpha, up=False))
        elif not up:
            if toast in self._toasts:
                self._toasts.remove(toast)
            toast.destroy()
            self._reflow_toasts()

    def _dismiss_toast(self, toast):
        if toast.winfo_exists():
            self._fade_toast(toast, 0.96, up=False)

    def _reflow_toasts(self):
        self.root.update_idletasks()
        try:
            rx = self.root.winfo_rootx()
            ry = self.root.winfo_rooty()
            rw = self.root.winfo_width()
            rh = self.root.winfo_height()
        except tk.TclError:
            return
        y = ry + rh - 16
        for toast in reversed([t for t in self._toasts if t.winfo_exists()]):
            toast.update_idletasks()
            w = toast.winfo_reqwidth()
            h = toast.winfo_reqheight()
            x = rx + rw - w - 16
            y -= h
            toast.geometry(f"+{max(x, rx + 8)}+{max(y, ry + 8)}")
            y -= 8

    def _modal(self, title, message, buttons, kind="info"):
        """Themed modal dialog. buttons: list of (label, value, accent).
        Returns the chosen value (or None if closed)."""
        self._play_sound(kind)
        pal = self._dialog_palette()
        win = tk.Toplevel(self.root)
        win.title(title)
        win.transient(self.root)
        win.resizable(False, False)
        win.configure(bg=pal["bg"])
        result = {"v": None}

        pad = tk.Frame(win, bg=pal["bg"])
        pad.pack(fill="both", expand=True, padx=20, pady=18)
        head = tk.Frame(pad, bg=pal["bg"])
        head.pack(fill="x", anchor="w")
        tk.Frame(head, bg=self._KIND_COLORS.get(kind, "#3b82f6"), width=4, height=20).pack(
            side="left", fill="y", padx=(0, 10))
        tk.Label(head, text=title, bg=pal["bg"], fg=pal["fg"], font=pal["title"],
                 anchor="w").pack(side="left")
        tk.Label(pad, text=message, bg=pal["bg"], fg=pal["sub"], font=pal["font"],
                 justify="left", anchor="w", wraplength=360).pack(anchor="w", pady=(12, 16))

        row = tk.Frame(pad, bg=pal["bg"])
        row.pack(fill="x")

        def choose(val):
            result["v"] = val
            win.destroy()

        retro = self.theme == THEME_RETRO
        for label, value, accent in reversed(buttons):
            if retro:
                b = make_button(row, label, lambda v=value: choose(v), width=9)
            else:
                b = mk_button(row, label, lambda v=value: choose(v),
                              kind="accent" if accent else "ghost")
            b.pack(side="right", padx=(6, 0))

        win.protocol("WM_DELETE_WINDOW", lambda: choose(None))
        win.update_idletasks()
        set_dark_titlebar(win, pal["dark"])
        w, h = win.winfo_reqwidth(), win.winfo_reqheight()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - w) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - h) // 3
        win.geometry(f"{w}x{h}+{max(x, 0)}+{max(y, 0)}")
        win.grab_set()
        win.wait_window()
        return result["v"]

    def _alert(self, title, message, kind="info"):
        self._modal(title, message, [("OK", True, True)], kind=kind)

    def _confirm(self, title, message, kind="info"):
        return bool(self._modal(
            title, message, [("Cancel", False, False), ("Yes", True, True)], kind=kind))

    # ---------------------------------------------------- Settings dialog
    def _open_settings(self):
        win = tk.Toplevel(self.root)
        win.title("Settings")
        win.transient(self.root)
        win.resizable(False, False)
        win.grab_set()

        retro = self.theme == THEME_RETRO
        bg = FACE if retro else BG_LEGACY
        fg = "#000000" if retro else TEXT_LEGACY
        subfg = "#333333" if retro else SUBTEXT_LEGACY
        win.configure(bg=bg)
        choice = tk.StringVar(value=self.theme)

        pad = tk.Frame(win, bg=bg)
        pad.pack(padx=18, pady=16, fill="both")
        header_font = WIN95_FONT_BOLD if retro else ("Segoe UI Semibold", 12)
        body_font = WIN95_FONT if retro else UI_FONT

        tk.Label(pad, text="Appearance", bg=bg, fg=fg, font=header_font).pack(anchor="w")
        tk.Label(pad, text="Choose how the app looks:", bg=bg, fg=subfg, font=body_font).pack(
            anchor="w", pady=(2, 12)
        )
        for label, value in (
            ("Legacy  -  clean modern flat UI", THEME_LEGACY),
            ("Retro  -  Windows 95 style", THEME_RETRO),
        ):
            tk.Radiobutton(
                pad, text=label, variable=choice, value=value, bg=bg, fg=fg,
                activebackground=bg, activeforeground=fg,
                selectcolor=LIGHT if retro else INPUT_LEGACY,
                font=body_font, anchor="w",
            ).pack(anchor="w", pady=2)

        tk.Label(pad, text="Notifications", bg=bg, fg=fg, font=header_font).pack(
            anchor="w", pady=(16, 0))
        tk.Checkbutton(
            pad, text="Play notification sounds", variable=self.sound_var, bg=bg, fg=fg,
            activebackground=bg, activeforeground=fg,
            selectcolor=LIGHT if retro else INPUT_LEGACY, font=body_font, anchor="w",
        ).pack(anchor="w", pady=(4, 0))

        btn_row = tk.Frame(pad, bg=bg)
        btn_row.pack(fill="x", pady=(18, 0))

        def apply_and_close():
            self._apply_theme(choice.get())
            win.destroy()

        if retro:
            make_button(btn_row, "OK", apply_and_close, width=8).pack(side="right", padx=(6, 0))
            make_button(btn_row, "Cancel", win.destroy, width=8).pack(side="right")
        else:
            mk_button(btn_row, "OK", apply_and_close, kind="accent").pack(side="right", padx=(6, 0))
            mk_button(btn_row, "Cancel", win.destroy).pack(side="right")

        win.update_idletasks()
        set_dark_titlebar(win, not retro)
        x = self.root.winfo_rootx() + (self.root.winfo_width() - win.winfo_width()) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - win.winfo_height()) // 3
        win.geometry(f"+{max(x, 0)}+{max(y, 0)}")

    # ------------------------------------------------ History / Convert
    def _record_history(self, title: str, path: str, url: str = "", quality: str = ""):
        if not path:
            return
        self.download_history = history_add(
            self.download_history,
            title=title,
            path=path,
            url=url,
            platform=detect_platform(url) if url else "",
            quality=quality,
        )
        if self._history_win and self._history_win.winfo_exists():
            self._refresh_history_list()

    def _history_selected(self):
        if not hasattr(self, "_history_list"):
            return None
        sel = self._history_list.curselection()
        if not sel:
            return None
        idx = sel[0]
        if 0 <= idx < len(self.download_history):
            return self.download_history[idx]
        return None

    def _refresh_history_list(self):
        if not hasattr(self, "_history_list"):
            return
        self._history_list.delete(0, "end")
        for entry in self.download_history:
            path = entry.get("path") or ""
            exists = os.path.isfile(path)
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(entry.get("ts") or 0))
            mark = "" if exists else " [missing]"
            platform = entry.get("platform") or ""
            prefix = f"[{platform}] " if platform else ""
            title = entry.get("title") or os.path.basename(path) or "(untitled)"
            self._history_list.insert("end", f"{when}  {prefix}{title}{mark}")

    def _open_history(self):
        if self._history_win and self._history_win.winfo_exists():
            self._history_win.lift()
            self._history_win.focus_force()
            self._refresh_history_list()
            return

        retro = self.theme == THEME_RETRO
        bg = FACE if retro else BG_LEGACY
        fg = "#000000" if retro else TEXT_LEGACY
        subfg = "#333333" if retro else SUBTEXT_LEGACY
        surface = LIGHT if retro else SURFACE_LEGACY
        header_font = WIN95_FONT_BOLD if retro else ("Segoe UI Semibold", 12)
        body_font = WIN95_FONT if retro else UI_FONT

        win = tk.Toplevel(self.root)
        self._history_win = win
        win.title("Download history")
        win.transient(self.root)
        win.configure(bg=bg)
        win.minsize(520, 360)

        pad = tk.Frame(win, bg=bg)
        pad.pack(fill="both", expand=True, padx=16, pady=14)
        tk.Label(pad, text="Download history", bg=bg, fg=fg, font=header_font).pack(anchor="w")
        tk.Label(
            pad,
            text="Open past downloads or convert them to another format.",
            bg=bg, fg=subfg, font=body_font,
        ).pack(anchor="w", pady=(2, 10))

        list_wrap = tk.Frame(pad, bg=bg)
        list_wrap.pack(fill="both", expand=True)
        scroll = tk.Scrollbar(list_wrap)
        scroll.pack(side="right", fill="y")
        self._history_list = tk.Listbox(
            list_wrap, font=body_font, bg=surface, fg=fg,
            selectbackground=NAVY if retro else PRIMARY,
            selectforeground="#ffffff",
            relief="sunken" if retro else "flat",
            bd=2 if retro else 0, highlightthickness=1 if not retro else 0,
            highlightbackground=BORDER_LEGACY, activestyle="none",
            yscrollcommand=scroll.set,
        )
        self._history_list.pack(side="left", fill="both", expand=True)
        scroll.config(command=self._history_list.yview)
        self._history_list.bind("<Double-Button-1>", lambda _e: self._history_open_file())

        btn_row = tk.Frame(pad, bg=bg)
        btn_row.pack(fill="x", pady=(12, 0))

        actions = [
            ("Open", self._history_open_file),
            ("Show", self._history_show_file),
            ("Convert", self._history_convert),
            ("Remove", self._history_remove),
            ("Clear all", self._history_clear),
            ("Convert any\u2026", lambda: self._open_convert_dialog()),
        ]
        for label, cmd in actions:
            if retro:
                b = make_button(btn_row, label, cmd, width=11)
            else:
                kind = "accent" if label == "Convert" else "ghost"
                b = mk_button(btn_row, label, cmd, kind=kind)
            b.pack(side="left", padx=(0, 6))
        if retro:
            make_button(btn_row, "Close", win.destroy, width=8).pack(side="right")
        else:
            mk_button(btn_row, "Close", win.destroy).pack(side="right")

        def on_close():
            self._history_win = None
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", on_close)
        self._refresh_history_list()
        win.update_idletasks()
        set_dark_titlebar(win, not retro)
        w, h = max(win.winfo_reqwidth(), 560), max(win.winfo_reqheight(), 400)
        x = self.root.winfo_rootx() + (self.root.winfo_width() - w) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - h) // 3
        win.geometry(f"{w}x{h}+{max(x, 0)}+{max(y, 0)}")

    def _history_open_file(self):
        entry = self._history_selected()
        if not entry:
            self._notify("Select a history item first.", "warning")
            return
        path = entry.get("path") or ""
        if os.path.isfile(path):
            self._os_open(path)
        else:
            self._notify("That file is no longer on disk.", "warning")

    def _history_show_file(self):
        entry = self._history_selected()
        if not entry:
            self._notify("Select a history item first.", "warning")
            return
        path = entry.get("path") or ""
        if os.path.isfile(path):
            if sys.platform == "win32":
                subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
            else:
                self._os_open(os.path.dirname(path))
        elif os.path.isdir(os.path.dirname(path)):
            self._os_open(os.path.dirname(path))
        else:
            self._notify("That file is no longer on disk.", "warning")

    def _history_convert(self):
        entry = self._history_selected()
        if not entry:
            self._notify("Select a history item first.", "warning")
            return
        path = entry.get("path") or ""
        if not os.path.isfile(path):
            self._notify("That file is no longer on disk.", "warning")
            return
        self._open_convert_dialog(path)

    def _history_remove(self):
        entry = self._history_selected()
        if not entry:
            self._notify("Select a history item first.", "warning")
            return
        eid = entry.get("id")
        self.download_history = [e for e in self.download_history if e.get("id") != eid]
        save_history(self.download_history)
        self._refresh_history_list()

    def _history_clear(self):
        if not self.download_history:
            return
        if not self._confirm("Clear history", "Remove all items from download history?\n"
                             "(This does not delete the files.)", kind="warning"):
            return
        self.download_history = []
        save_history(self.download_history)
        self._refresh_history_list()

    def _open_convert_dialog(self, src_path: str | None = None):
        if self.is_downloading or self.is_busy:
            self._notify("Please wait for the current task to finish.", "info")
            return
        if not ffmpeg_executable():
            self._alert(
                APP_TITLE,
                "ffmpeg was not found, so conversion isn't available.\n\n"
                "Place ffmpeg next to the app (or in vendor\\ffmpeg) and try again.",
                kind="error",
            )
            return

        retro = self.theme == THEME_RETRO
        bg = FACE if retro else BG_LEGACY
        fg = "#000000" if retro else TEXT_LEGACY
        subfg = "#333333" if retro else SUBTEXT_LEGACY
        header_font = WIN95_FONT_BOLD if retro else ("Segoe UI Semibold", 12)
        body_font = WIN95_FONT if retro else UI_FONT

        initial = src_path or self.last_file or ""
        if initial and not os.path.isfile(initial):
            initial = ""

        win = tk.Toplevel(self.root)
        win.title("Convert file")
        win.transient(self.root)
        win.resizable(False, False)
        win.configure(bg=bg)
        win.grab_set()

        src_var = tk.StringVar(value=initial)
        fmt_var = tk.StringVar(value="MP3 (audio)")
        out_var = tk.StringVar()

        def sync_output(*_):
            src = src_var.get().strip()
            label = fmt_var.get()
            if src and label in CONVERT_TARGETS:
                ext, _ = CONVERT_TARGETS[label]
                out_var.set(default_convert_output(src, ext))
            elif not src:
                out_var.set("")

        src_var.trace_add("write", sync_output)
        fmt_var.trace_add("write", sync_output)
        sync_output()

        pad = tk.Frame(win, bg=bg)
        pad.pack(fill="both", expand=True, padx=18, pady=16)
        tk.Label(pad, text="Convert file", bg=bg, fg=fg, font=header_font).pack(anchor="w")
        tk.Label(
            pad, text="Pick a video or audio file and a target format.",
            bg=bg, fg=subfg, font=body_font,
        ).pack(anchor="w", pady=(2, 12))

        tk.Label(pad, text="Source", bg=bg, fg=fg, font=body_font).pack(anchor="w")
        src_row = tk.Frame(pad, bg=bg)
        src_row.pack(fill="x", pady=(4, 10))
        if retro:
            make_entry(src_row, src_var).pack(side="left", fill="x", expand=True, ipady=3, padx=(0, 6))
            make_button(src_row, "Browse", lambda: browse_src(), width=8).pack(side="left")
        else:
            wrap, _ = mk_entry(src_row, src_var, font=UI_FONT)
            wrap.pack(side="left", fill="x", expand=True, padx=(0, 8))
            mk_button(src_row, "Browse", lambda: browse_src()).pack(side="left")

        def browse_src():
            path = filedialog.askopenfilename(
                title="Choose a file to convert",
                initialdir=os.path.dirname(src_var.get()) or self.folder_var.get() or DEFAULT_DOWNLOAD_DIR,
                filetypes=MEDIA_FILETYPES,
            )
            if path:
                src_var.set(path)

        tk.Label(pad, text="Format", bg=bg, fg=fg, font=body_font).pack(anchor="w")
        fmt_row = tk.Frame(pad, bg=bg)
        fmt_row.pack(fill="x", pady=(4, 10))
        labels = list(CONVERT_TARGETS.keys())
        if retro:
            menu = tk.OptionMenu(fmt_row, fmt_var, *labels)
            menu.config(font=WIN95_FONT, bg=FACE, fg="#000000", activebackground=FACE,
                        relief="raised", bd=2, highlightthickness=1,
                        highlightbackground=DARKEDGE, width=22, anchor="w")
            menu["menu"].config(bg=FACE, fg="#000000", font=WIN95_FONT)
            menu.pack(anchor="w")
        else:
            mk_dropdown(fmt_row, fmt_var, labels, width=24).pack(anchor="w")

        tk.Label(pad, text="Output", bg=bg, fg=fg, font=body_font).pack(anchor="w")
        out_row = tk.Frame(pad, bg=bg)
        out_row.pack(fill="x", pady=(4, 14))
        if retro:
            make_entry(out_row, out_var).pack(side="left", fill="x", expand=True, ipady=3, padx=(0, 6))
            make_button(out_row, "Browse", lambda: browse_out(), width=8).pack(side="left")
        else:
            owrap, _ = mk_entry(out_row, out_var, font=UI_FONT)
            owrap.pack(side="left", fill="x", expand=True, padx=(0, 8))
            mk_button(out_row, "Browse", lambda: browse_out()).pack(side="left")

        def browse_out():
            label = fmt_var.get()
            ext = CONVERT_TARGETS.get(label, ("mp4", None))[0]
            path = filedialog.asksaveasfilename(
                title="Save converted file as",
                initialfile=os.path.basename(out_var.get() or f"converted.{ext}"),
                initialdir=os.path.dirname(out_var.get() or src_var.get() or "") or DEFAULT_DOWNLOAD_DIR,
                defaultextension=f".{ext}",
                filetypes=[(f".{ext}", f"*.{ext}"), ("All files", "*.*")],
            )
            if path:
                out_var.set(path)

        btn_row = tk.Frame(pad, bg=bg)
        btn_row.pack(fill="x")

        def start():
            src = src_var.get().strip()
            dest = out_var.get().strip()
            label = fmt_var.get()
            if not src or not os.path.isfile(src):
                self._notify("Choose a valid source file.", "warning")
                return
            if not dest:
                self._notify("Choose an output path.", "warning")
                return
            if label not in CONVERT_TARGETS:
                self._notify("Choose a target format.", "warning")
                return
            if os.path.normcase(os.path.normpath(src)) == os.path.normcase(os.path.normpath(dest)):
                self._notify("Output must be a different file than the source.", "warning")
                return
            win.destroy()
            self._start_convert(src, dest, label)

        if retro:
            make_button(btn_row, "Convert", start, bold=True, width=10).pack(side="right", padx=(6, 0))
            make_button(btn_row, "Cancel", win.destroy, width=8).pack(side="right")
        else:
            mk_button(btn_row, "Convert", start, kind="accent").pack(side="right", padx=(6, 0))
            mk_button(btn_row, "Cancel", win.destroy).pack(side="right")

        win.update_idletasks()
        set_dark_titlebar(win, not retro)
        w, h = win.winfo_reqwidth(), win.winfo_reqheight()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - w) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - h) // 3
        win.geometry(f"+{max(x, 0)}+{max(y, 0)}")

    def _start_convert(self, src: str, dest: str, fmt_label: str):
        if self.is_downloading or self.is_busy:
            self._notify("Please wait for the current task to finish.", "info")
            return
        os.makedirs(os.path.dirname(os.path.abspath(dest)) or ".", exist_ok=True)
        self.is_busy = True
        self._update_action_buttons()
        self.progress.set(0)
        self.status_var.set("Converting...")
        self._log(f"Converting to {fmt_label}: {os.path.basename(src)}")
        threading.Thread(
            target=self._convert_worker, args=(src, dest, fmt_label), daemon=True
        ).start()

    def _convert_worker(self, src: str, dest: str, fmt_label: str):
        try:
            self.msg_queue.put(("progress", 15, "Converting with ffmpeg..."))
            run_ffmpeg_convert(src, dest, fmt_label)
            title = os.path.basename(dest)
            self.msg_queue.put(("convert_done", title, dest, fmt_label))
        except Exception as exc:  # noqa: BLE001
            self.msg_queue.put(("convert_error", str(exc)))

    # ------------------------------------------------------- Retro UI build
    def _build_retro_ui(self):
        outer = tk.Frame(self.root, bg=FACE, bd=0)
        outer.pack(fill="both", expand=True, padx=6, pady=6)

        banner = tk.Frame(outer, bg=NAVY, bd=0)
        banner.pack(fill="x")
        tk.Label(banner, text="  TM Ripper", bg=NAVY, fg="white",
                 font=WIN95_TITLE_FONT, anchor="w").pack(side="left", fill="x", expand=True, ipady=3)
        tk.Label(banner, text="TikTok  Instagram  Facebook  YouTube  ", bg=NAVY, fg="#c0c0c0",
                 font=WIN95_FONT, anchor="e").pack(side="right", ipady=3)

        link_group = make_group(outer, " Video link ")
        link_group.pack(fill="x", pady=(8, 6))
        entry_row = tk.Frame(link_group, bg=FACE)
        entry_row.pack(fill="x")
        self.url_entry = make_entry(entry_row, self.url_var)
        self.url_entry.pack(side="left", fill="x", expand=True, ipady=3, padx=(0, 6))
        make_button(entry_row, "Paste", self._paste, width=7).pack(side="left", padx=(0, 4))
        make_button(entry_row, "Clear", self._clear, width=7).pack(side="left")
        row2 = tk.Frame(link_group, bg=FACE)
        row2.pack(fill="x", pady=(6, 0))
        self.platform_label = tk.Label(row2, text="Platform: (none)", bg=FACE, fg="#000000",
                                       font=WIN95_FONT, anchor="w")
        self.platform_label.pack(side="left")
        self.preview_btn = make_button(row2, "Preview", self._start_preview, width=8)
        self.preview_btn.pack(side="right")

        # Preview panel
        prev_group = make_group(outer, " Preview ")
        prev_group.pack(fill="x", pady=6)
        prev_inner = tk.Frame(prev_group, bg=FACE)
        prev_inner.pack(fill="x")
        self.thumb_label = tk.Label(prev_inner, bg=FACE, text="(no preview)", fg=SHADOW,
                                    font=WIN95_FONT, width=30, height=6)
        self.thumb_label.pack(side="left", padx=(0, 8))
        self.meta_label = tk.Label(prev_inner, bg=FACE, fg="#000000", font=WIN95_FONT,
                                   justify="left", anchor="nw", wraplength=340)
        self.meta_label.pack(side="left", fill="both", expand=True)

        save_group = make_group(outer, " Save to folder ")
        save_group.pack(fill="x", pady=6)
        save_row = tk.Frame(save_group, bg=FACE)
        save_row.pack(fill="x")
        make_entry(save_row, self.folder_var).pack(side="left", fill="x", expand=True, ipady=3, padx=(0, 6))
        make_button(save_row, "Browse", self._browse, width=7).pack(side="left", padx=(0, 4))
        make_button(save_row, "Open", self._open_folder, width=7).pack(side="left")

        opt_group = make_group(outer, " Options ")
        opt_group.pack(fill="x", pady=6)
        opt_row = tk.Frame(opt_group, bg=FACE)
        opt_row.pack(fill="x")
        tk.Label(opt_row, text="Quality:", bg=FACE, fg="#000000", font=WIN95_FONT).pack(side="left")
        menu = tk.OptionMenu(opt_row, self.quality_var, *self._quality_options())
        menu.config(font=WIN95_FONT, bg=FACE, fg="#000000", activebackground=FACE, relief="raised",
                    bd=2, highlightthickness=1, highlightbackground=DARKEDGE, indicatoron=True,
                    width=20, anchor="w")
        menu["menu"].config(bg=FACE, fg="#000000", font=WIN95_FONT)
        menu.pack(side="left", padx=(6, 0))

        btn_row = tk.Frame(outer, bg=FACE)
        btn_row.pack(fill="x", pady=(8, 6))
        self.download_btn = make_button(btn_row, "Download", self._start_download, bold=True)
        self.download_btn.config(pady=6)
        self.download_btn.pack(side="left", fill="x", expand=True, padx=(0, 4))
        self.cancel_btn = make_button(btn_row, "Stop", self._cancel_download, width=8)
        self.cancel_btn.config(pady=6)
        self.cancel_btn.pack(side="left")

        prog_group = make_group(outer, " Progress ")
        prog_group.pack(fill="x", pady=6)
        self.progress = Win95Progress(prog_group)
        self.progress.pack(fill="x")
        done_row = tk.Frame(prog_group, bg=FACE)
        done_row.pack(fill="x", pady=(6, 0))
        self.open_file_btn = make_button(done_row, "Open file", self._open_last_file, width=10)
        self.open_file_btn.pack(side="left", padx=(0, 4))
        self.show_folder_btn = make_button(done_row, "Show in folder", self._show_in_folder, width=13)
        self.show_folder_btn.pack(side="left", padx=(0, 4))
        self.history_btn = make_button(done_row, "History", self._open_history, width=8)
        self.history_btn.pack(side="left", padx=(0, 4))
        self.convert_btn = make_button(done_row, "Convert", lambda: self._open_convert_dialog(), width=8)
        self.convert_btn.pack(side="left")

        log_group = make_group(outer, " Log ")
        log_group.pack(fill="both", expand=True, pady=6)
        log_inner = tk.Frame(log_group, bg=FACE)
        log_inner.pack(fill="both", expand=True)
        self.log = tk.Text(log_inner, height=6, wrap="word", font=WIN95_MONO, bg=LIGHT, fg="#000000",
                           relief="sunken", bd=2, highlightthickness=0)
        self.log.pack(side="left", fill="both", expand=True)
        scroll = tk.Scrollbar(log_inner, command=self.log.yview, relief="raised", bd=1)
        scroll.pack(side="right", fill="y")
        self.log.configure(yscrollcommand=scroll.set, state="disabled")

        statusbar = tk.Frame(self.root, bg=FACE)
        statusbar.pack(side="bottom", fill="x")
        tk.Label(statusbar, text=f"v{APP_VERSION}", bg=FACE, fg="#000000", font=WIN95_FONT,
                 relief="sunken", bd=1, anchor="e", padx=8).pack(side="right")
        tk.Label(statusbar, textvariable=self.status_var, bg=FACE, fg="#000000", font=WIN95_FONT,
                 relief="sunken", bd=1, anchor="w", padx=6).pack(side="left", fill="x", expand=True)

        if yt_dlp is None:
            self._log("yt-dlp is not installed. Run:  pip install -r requirements.txt", error=True)

    # ------------------------------------------------------ Legacy UI build
    def _legacy_logo(self, size):
        if not (Image and ImageTk and os.path.exists(ICON_PNG)):
            return None
        try:
            im = Image.open(ICON_PNG).convert("RGBA")
            im.thumbnail((size, size))
            self._legacy_logo_ref = ImageTk.PhotoImage(im)
            return self._legacy_logo_ref
        except Exception:
            return None

    def _card_body(self, parent, padx=16, pady=14, **pack_kw):
        """A bordered surface card; returns the padded inner frame to fill."""
        card = mk_card(parent)
        card.pack(fill="x", **pack_kw)
        body = tk.Frame(card, bg=SURFACE_LEGACY)
        body.pack(fill="both", expand=True, padx=padx, pady=pady)
        return body

    def _build_legacy_ui(self):
        # --- Custom dark menu bar (top) ---------------------------------
        menubar = self._build_menubar_legacy(self.root)
        menubar.pack(side="top", fill="x")
        tk.Frame(self.root, bg=BORDER_LEGACY, height=1).pack(side="top", fill="x")

        # --- Always-visible bottom bar (status + version) ---------------
        bar_wrap = tk.Frame(self.root, bg=BG_LEGACY)
        bar_wrap.pack(side="bottom", fill="x")
        tk.Frame(bar_wrap, bg=BORDER_LEGACY, height=1).pack(fill="x")
        bar = tk.Frame(bar_wrap, bg=BG_LEGACY)
        bar.pack(fill="x", padx=18, pady=6)
        tk.Label(bar, textvariable=self.status_var, bg=BG_LEGACY, fg=SUBTEXT_LEGACY,
                 font=UI_FONT_SM, anchor="w").pack(side="left")
        tk.Label(bar, text=f"TM Ripper v{APP_VERSION}  \u2022  by TheMannster", bg=BG_LEGACY,
                 fg=SUBTEXT_LEGACY, font=UI_FONT_SM, anchor="e").pack(side="right")

        outer = tk.Frame(self.root, bg=BG_LEGACY)
        outer.pack(side="top", fill="both", expand=True)
        c = tk.Frame(outer, bg=BG_LEGACY)
        c.pack(fill="both", expand=True, padx=24, pady=(18, 8))

        # --- Header (logo + wordmark) -----------------------------------
        header = tk.Frame(c, bg=BG_LEGACY)
        header.pack(fill="x", pady=(0, 16))
        logo = self._legacy_logo(40)
        if logo is not None:
            tk.Label(header, image=logo, bg=BG_LEGACY).pack(side="left", padx=(0, 12))
        titles = tk.Frame(header, bg=BG_LEGACY)
        titles.pack(side="left", anchor="w")
        tk.Label(titles, text="TM Ripper", bg=BG_LEGACY, fg=HEADING_LEGACY,
                 font=UI_TITLE, anchor="w").pack(anchor="w")
        tk.Label(titles,
                 text="TikTok  \u2022  Instagram Reels  \u2022  Facebook Reels  \u2022  YouTube Shorts",
                 bg=BG_LEGACY, fg=SUBTEXT_LEGACY, font=UI_FONT_SM, anchor="w").pack(anchor="w")

        # --- Link card --------------------------------------------------
        link = self._card_body(c, pady=(14, 14))
        mk_caption(link, "Video link", SURFACE_LEGACY).pack(anchor="w", pady=(0, 6))
        entry_row = tk.Frame(link, bg=SURFACE_LEGACY)
        entry_row.pack(fill="x")
        wrap, self.url_entry = mk_entry(entry_row, self.url_var)
        wrap.pack(side="left", fill="x", expand=True, padx=(0, 8))
        mk_button(entry_row, "Paste", self._paste).pack(side="left", padx=(0, 6))
        mk_button(entry_row, "Clear", self._clear).pack(side="left")
        row2 = tk.Frame(link, bg=SURFACE_LEGACY)
        row2.pack(fill="x", pady=(10, 0))
        self.platform_label = tk.Label(row2, text="Platform: \u2014", bg=SURFACE_LEGACY,
                                       fg=SUBTEXT_LEGACY, font=UI_FONT_SM, anchor="w")
        self.platform_label.pack(side="left")
        self.preview_btn = mk_button(row2, "Preview", self._start_preview)
        self.preview_btn.pack(side="right")

        # --- Preview card -----------------------------------------------
        prev = self._card_body(c, pady=(12, 0))
        self.thumb_label = tk.Label(prev, bg=SURFACE_LEGACY, text="(no preview)",
                                    fg=SUBTEXT_LEGACY, font=UI_FONT_SM, width=30, height=5)
        self.thumb_label.pack(side="left", padx=(0, 14))
        self.meta_label = tk.Label(prev, text="", bg=SURFACE_LEGACY, fg=TEXT_LEGACY,
                                   font=UI_FONT_SM, justify="left", anchor="nw", wraplength=340)
        self.meta_label.pack(side="left", fill="both", expand=True)

        # --- Options card (destination + quality) -----------------------
        opts = self._card_body(c, pady=(12, 0))
        mk_caption(opts, "Save to folder", SURFACE_LEGACY).pack(anchor="w", pady=(0, 6))
        out_row = tk.Frame(opts, bg=SURFACE_LEGACY)
        out_row.pack(fill="x")
        fwrap, _ = mk_entry(out_row, self.folder_var, font=UI_FONT)
        fwrap.pack(side="left", fill="x", expand=True, padx=(0, 8))
        mk_button(out_row, "Browse", self._browse).pack(side="left", padx=(0, 6))
        mk_button(out_row, "Open", self._open_folder).pack(side="left")
        qrow = tk.Frame(opts, bg=SURFACE_LEGACY)
        qrow.pack(fill="x", pady=(12, 0))
        mk_caption(qrow, "Quality", SURFACE_LEGACY).pack(side="left", padx=(0, 10))
        mk_dropdown(qrow, self.quality_var, self._quality_options(), width=22).pack(side="left")

        # --- Download / stop --------------------------------------------
        btn_row = tk.Frame(c, bg=BG_LEGACY)
        btn_row.pack(fill="x", pady=(14, 10))
        self.download_btn = mk_button(btn_row, "\u2b07  Download", self._start_download, kind="accent")
        self.download_btn.config(pady=11)
        self.download_btn.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.cancel_btn = mk_button(btn_row, "Stop", self._cancel_download)
        self.cancel_btn.config(pady=11)
        self.cancel_btn.pack(side="left")

        self.progress = ModernBar(c)
        self.progress.pack(fill="x", pady=(0, 12))

        done_row = tk.Frame(c, bg=BG_LEGACY)
        done_row.pack(fill="x", pady=(0, 12))
        self.open_file_btn = mk_button(done_row, "Open file", self._open_last_file)
        self.open_file_btn.pack(side="left", padx=(0, 6))
        self.show_folder_btn = mk_button(done_row, "Show in folder", self._show_in_folder)
        self.show_folder_btn.pack(side="left", padx=(0, 6))
        self.history_btn = mk_button(done_row, "History", self._open_history)
        self.history_btn.pack(side="left", padx=(0, 6))
        self.convert_btn = mk_button(done_row, "Convert", lambda: self._open_convert_dialog())
        self.convert_btn.pack(side="left")

        # --- Log --------------------------------------------------------
        mk_caption(c, "Log", BG_LEGACY).pack(anchor="w", pady=(0, 6))
        log_card = mk_card(c)
        log_card.pack(fill="both", expand=True)
        log_frame = tk.Frame(log_card, bg=SURFACE_LEGACY)
        log_frame.pack(fill="both", expand=True, padx=2, pady=2)
        self.log = tk.Text(log_frame, height=4, wrap="word", font=UI_MONO, relief="flat", bd=0,
                           bg=SURFACE_LEGACY, fg=TEXT_LEGACY, insertbackground=TEXT_LEGACY,
                           padx=10, pady=8, highlightthickness=0)
        self.log.pack(side="left", fill="both", expand=True)
        scroll = tk.Scrollbar(log_frame, command=self.log.yview, relief="flat", bd=0,
                              troughcolor=SURFACE_LEGACY, bg=BTN_LEGACY, activebackground=BTN_LEGACY_ACTIVE)
        scroll.pack(side="right", fill="y")
        self.log.configure(yscrollcommand=scroll.set, state="disabled")

        if yt_dlp is None:
            self._log("yt-dlp is not installed. Run:  pip install -r requirements.txt", error=True)

    # ------------------------------------------------------- Drag and drop
    def _register_dnd(self):
        if not _DND_AVAILABLE:
            return
        try:
            for target in (self.url_entry, self.root):
                target.drop_target_register(DND_TEXT, DND_FILES)
                target.dnd_bind("<<Drop>>", self._on_drop)
        except Exception:
            pass

    def _on_drop(self, event):
        data = event.data or ""
        data = data.strip().strip("{}")
        url = first_url(data)
        if url:
            self.url_var.set(url)
            self.status_var.set("Link dropped.")
        return event.action

    # -------------------------------------------------------------- Helpers
    def _quality_options(self):
        return ["Best video + audio", "1080p max", "720p max", "Audio only (MP3)"]

    def _paste(self):
        try:
            self.url_var.set(self.root.clipboard_get().strip())
        except tk.TclError:
            pass

    def _clear(self):
        self.url_var.set("")
        self._reset_preview()

    def _browse(self):
        folder = filedialog.askdirectory(initialdir=self.folder_var.get() or os.getcwd())
        if folder:
            self.folder_var.set(folder)

    def _open_folder(self):
        folder = self.folder_var.get()
        os.makedirs(folder, exist_ok=True)
        self._os_open(folder)

    def _os_open(self, path):
        try:
            os.startfile(path)  # Windows
        except AttributeError:
            opener = "open" if sys.platform == "darwin" else "xdg-open"
            subprocess.Popen([opener, path])

    def _open_last_file(self):
        if self.last_file and os.path.exists(self.last_file):
            self._os_open(self.last_file)
        else:
            self._notify("No downloaded file to open yet.", "warning")

    def _show_in_folder(self):
        if self.last_file and os.path.exists(self.last_file):
            if sys.platform == "win32":
                subprocess.Popen(["explorer", "/select,", os.path.normpath(self.last_file)])
            else:
                self._os_open(os.path.dirname(self.last_file))
        else:
            self._open_folder()

    def _on_url_change(self, *_):
        if not hasattr(self, "platform_label"):
            return
        url = self.url_var.get().strip()
        none_text = "(none)" if self.theme == THEME_RETRO else "\u2014"
        self.platform_label.config(text=f"Platform: {detect_platform(url) if url else none_text}")

    def _reset_preview(self):
        self._thumb_img = None
        if hasattr(self, "thumb_label"):
            # Restore the small text-sized label; width/height revert to
            # character/line units once the image is removed.
            self.thumb_label.config(image="", text="(no preview)", width=30, height=6)
        if hasattr(self, "meta_label"):
            self.meta_label.config(text="")

    def _restore_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        for text, error in self.log_history:
            self.log.insert("end", ("[!] " if error else "") + text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _log(self, text: str, error: bool = False):
        self.log_history.append((text, error))
        self.log.configure(state="normal")
        self.log.insert("end", ("[!] " if error else "") + text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _set_enabled(self, widget, enabled: bool):
        try:
            widget.config(state="normal" if enabled else "disabled")
        except tk.TclError:
            pass

    def _update_action_buttons(self):
        has_file = bool(self.last_file and os.path.exists(self.last_file))
        self._set_enabled(self.open_file_btn, has_file)
        self._set_enabled(self.show_folder_btn, has_file)
        self._set_enabled(self.cancel_btn, self.is_downloading)
        can_start = not self.is_downloading and not self.is_busy
        self._set_enabled(self.download_btn, can_start)
        self._set_enabled(self.preview_btn, can_start)
        if hasattr(self, "convert_btn"):
            self._set_enabled(self.convert_btn, can_start)
        if hasattr(self, "history_btn"):
            self._set_enabled(self.history_btn, True)

    # -------------------------------------------------------------- Preview
    def _start_preview(self):
        if self.is_downloading or self.is_busy or yt_dlp is None:
            return
        url = self.url_var.get().strip()
        if not url or not re.match(r"^https?://", url):
            self._notify("Please paste a valid video link first.", "warning")
            return
        self.is_busy = True
        self._update_action_buttons()
        self.status_var.set("Fetching preview...")
        self.meta_label.config(text="Loading...")
        self.discord.set_preview()
        threading.Thread(target=self._preview_worker, args=(url,), daemon=True).start()

    def _preview_worker(self, url: str):
        try:
            opts = {"quiet": True, "no_warnings": True, "noplaylist": True, "skip_download": True}
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
            meta = {
                "title": info.get("title", "(untitled)"),
                "uploader": info.get("uploader") or info.get("channel") or info.get("uploader_id") or "?",
                "duration": info.get("duration"),
                "width": info.get("width"),
                "height": info.get("height"),
                "ext": info.get("ext", "?"),
            }
            img = None
            thumb = info.get("thumbnail")
            if thumb and Image is not None:
                try:
                    req = urllib.request.Request(thumb, headers={"User-Agent": "Mozilla/5.0"})
                    data = urllib.request.urlopen(req, timeout=15).read()
                    img = Image.open(io.BytesIO(data))
                    img.load()
                except Exception:
                    img = None
            self.msg_queue.put(("preview", meta, img))
        except Exception as exc:  # noqa: BLE001
            self.msg_queue.put(("preview_error", str(exc)))

    def _show_preview(self, meta, img):
        res = f"{meta['width']}x{meta['height']}" if meta.get("width") else "?"
        text = (
            f"Title: {meta['title']}\n"
            f"By: {meta['uploader']}\n"
            f"Duration: {human_time(meta.get('duration'))}\n"
            f"Resolution: {res}   Format: {meta.get('ext')}"
        )
        self.meta_label.config(text=text)
        if img is not None and ImageTk is not None:
            thumb = img.copy()
            thumb.thumbnail((220, 160))
            self._thumb_img = ImageTk.PhotoImage(thumb)
            self.thumb_label.config(image=self._thumb_img, text="", width=thumb.width, height=thumb.height)
        else:
            self.thumb_label.config(image="", text="(no thumbnail)", width=30, height=6)
        self.status_var.set("Preview ready.")

    # ------------------------------------------------------------ Download
    def _start_download(self):
        if self.is_downloading or self.is_busy:
            return
        if yt_dlp is None:
            self._alert(APP_TITLE, "yt-dlp is not installed.\n\nRun:  pip install -r requirements.txt",
                        kind="error")
            return
        url = self.url_var.get().strip()
        if not url or not re.match(r"^https?://", url):
            self._notify("Please paste a valid video link (starting with http).", "warning")
            return

        folder = self.folder_var.get().strip() or DEFAULT_DOWNLOAD_DIR
        os.makedirs(folder, exist_ok=True)
        self._persist()

        self.is_downloading = True
        self.cancel_event.clear()
        self.last_file = None
        self._update_action_buttons()
        disabled_text = "Downloading..." if self.theme == THEME_RETRO else "Downloading\u2026"
        self.download_btn.config(text=disabled_text)
        self.progress.set(0)
        self.status_var.set("Starting...")
        self._log(f"Downloading from {detect_platform(url)}: {url}")
        self.discord.set_downloading(detect_platform(url))

        threading.Thread(target=self._download_worker, args=(url, folder, self.quality_var.get()),
                         daemon=True).start()

    def _cancel_download(self):
        if self.is_downloading:
            self.cancel_event.set()
            self.status_var.set("Cancelling...")
            self._log("Cancel requested...")

    def _format_for_quality(self, quality: str):
        if quality == "Audio only (MP3)":
            return "bestaudio/best", True
        if quality == "1080p max":
            return "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best", False
        if quality == "720p max":
            return "bestvideo[height<=720]+bestaudio/best[height<=720]/best", False
        return "bestvideo*+bestaudio/best", False

    def _download_worker(self, url: str, folder: str, quality: str):
        def hook(d):
            if self.cancel_event.is_set():
                raise DownloadCancelled()
            if d["status"] == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate")
                downloaded = d.get("downloaded_bytes", 0)
                pct = (downloaded / total * 100) if total else 0
                speed = d.get("speed") or 0
                speed_str = f"{speed / 1_048_576:.1f} MB/s" if speed else "..."
                eta = human_time(d.get("eta"))
                size = human_size(total)
                self.msg_queue.put(
                    ("progress", pct,
                     f"Downloading {pct:.0f}%  |  {speed_str}  |  ETA {eta}  |  {human_size(downloaded)}/{size}")
                )
            elif d["status"] == "finished":
                self.msg_queue.put(("progress", 100, "Processing / merging..."))

        fmt, audio_only = self._format_for_quality(quality)
        ydl_opts = {
            "outtmpl": os.path.join(folder, "%(title).100s [%(id)s].%(ext)s"),
            "format": fmt,
            "progress_hooks": [hook],
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "restrictfilenames": False,
        }
        if FFMPEG_DIR:
            ydl_opts["ffmpeg_location"] = FFMPEG_DIR
        if audio_only:
            ydl_opts["postprocessors"] = [
                {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}
            ]

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                filepath = None
                reqs = info.get("requested_downloads")
                if reqs:
                    filepath = reqs[0].get("filepath") or reqs[0].get("_filename")
                if not filepath:
                    filepath = ydl.prepare_filename(info)
                if audio_only and filepath:
                    filepath = os.path.splitext(filepath)[0] + ".mp3"
                title = info.get("title", "video")
            self.msg_queue.put(("done", title, filepath, url, quality))
        except DownloadCancelled:
            self.msg_queue.put(("cancelled",))
        except Exception as exc:  # noqa: BLE001
            self.msg_queue.put(("error", str(exc)))

    # ------------------------------------------------- App auto-updater
    def _check_app_updates(self, manual=False):
        if self._update_checked and not manual:
            return
        self._update_checked = True
        if manual:
            self.status_var.set("Checking for updates...")
        threading.Thread(target=self._update_check_worker, args=(manual,), daemon=True).start()

    def _update_check_worker(self, manual: bool):
        try:
            req = urllib.request.Request(
                RELEASES_API_URL,
                headers={"User-Agent": "TMRipper", "Accept": "application/vnd.github+json"},
            )
            data = json.load(urllib.request.urlopen(req, timeout=15))
            tag = data.get("tag_name", "")
            portable_url = installer_url = None
            for asset in data.get("assets", []):
                name = asset.get("name", "").lower()
                url = asset.get("browser_download_url")
                if name.endswith(".zip"):
                    portable_url = url  # portable build for the seamless in-app swap
                elif name.endswith(".exe") and ("setup" in name or "install" in name):
                    installer_url = url
            self.msg_queue.put(("update_check", tag, portable_url, installer_url, manual))
        except Exception as exc:  # noqa: BLE001
            self.msg_queue.put(("update_check_err", str(exc), manual))

    def _install_dir_writable(self):
        """True if we can swap the running exe in place (no admin needed)."""
        if not getattr(sys, "frozen", False):
            return False
        try:
            folder = os.path.dirname(sys.executable)
            probe = os.path.join(folder, ".tmr_write_test")
            with open(probe, "w") as fh:
                fh.write("ok")
            os.remove(probe)
            return True
        except OSError:
            return False

    def _handle_update_check(self, tag, portable_url, installer_url, manual):
        if not (tag and is_newer_version(tag, APP_VERSION)):
            self.status_var.set("You're up to date.")
            if manual:
                self._notify("You're on the latest version.", "success")
            return

        if manual:
            self.status_var.set("Update available.")

        can_swap = bool(portable_url) and self._install_dir_writable()
        if not can_swap and not installer_url:
            self._alert(
                APP_TITLE,
                f"A new version ({tag}) is available on GitHub, but no downloadable "
                "build was attached to the release.",
                kind="warning",
            )
            return

        detail = ("It will download in the background, then ask you to reopen the app."
                  if can_swap else
                  "This will download and run the installer.")
        if not self._confirm(
            "Update available",
            f"A new version of {APP_TITLE} is available.\n\n"
            f"You have:  v{APP_VERSION}\nLatest:  {tag}\n\n{detail}\n\nDownload it now?",
        ):
            return

        if can_swap:
            self._start_update_download(portable_url, mode="swap")
        else:
            self._start_update_download(installer_url, mode="installer")

    def _start_update_download(self, url: str, mode: str):
        if self.is_busy:
            return
        self.is_busy = True
        self._update_action_buttons()
        self.status_var.set("Downloading update...")
        self._log(f"Downloading update ({mode}) from {url}")
        threading.Thread(target=self._download_update_worker, args=(url, mode),
                         daemon=True).start()

    def _download_update_worker(self, url: str, mode: str):
        try:
            if mode == "swap":
                dest = os.path.join(tempfile.gettempdir(), "TMRipper-portable.zip")
            else:
                dest = os.path.join(tempfile.gettempdir(), "TMRipper-Setup.exe")
            with urllib.request.urlopen(
                urllib.request.Request(url, headers={"User-Agent": "TMRipper"}), timeout=120
            ) as resp, open(dest, "wb") as fh:
                total = int(resp.headers.get("Content-Length") or 0)
                done = 0
                while True:
                    chunk = resp.read(1024 * 256)
                    if not chunk:
                        break
                    fh.write(chunk)
                    done += len(chunk)
                    pct = (done / total * 100) if total else 0
                    self.msg_queue.put(("progress", pct, f"Downloading update... {pct:.0f}%"))
            self.msg_queue.put(("update_ready", dest, mode))
        except Exception as exc:  # noqa: BLE001
            self.msg_queue.put(("update_err", str(exc)))

    def _apply_exe_update(self, zip_path: str):
        """Extract the portable build and swap it in place; no installer needed."""
        exe = sys.executable
        folder = os.path.dirname(exe)
        new_exe = os.path.join(folder, "TM Ripper.new.exe")
        old = exe + ".old"
        try:
            with zipfile.ZipFile(zip_path) as z:
                inner = next((n for n in z.namelist() if n.lower().endswith(".exe")), None)
                if not inner:
                    raise OSError("No application found inside the update package.")
                with z.open(inner) as src, open(new_exe, "wb") as dst:
                    shutil.copyfileobj(src, dst)
            if os.path.exists(old):
                os.remove(old)
            os.rename(exe, old)          # move the running exe aside (allowed on Windows)
            os.rename(new_exe, exe)      # put the new build in its place
        except OSError as exc:
            for leftover in (new_exe,):
                try:
                    if os.path.exists(leftover):
                        os.remove(leftover)
                except OSError:
                    pass
            self._log(f"Could not apply update in place: {exc}", error=True)
            self.is_busy = False
            self._update_action_buttons()
            self._alert(APP_TITLE, f"Couldn't apply the update automatically:\n\n{exc}",
                        kind="error")
            return
        finally:
            try:
                os.remove(zip_path)
            except OSError:
                pass

        self.is_busy = False
        self._update_action_buttons()
        self.progress.set(100)
        self.status_var.set("Update ready \u2013 restart to finish.")
        self._log("Update downloaded. Restart to finish updating.")
        if self._confirm(
            "Update ready",
            "The update was downloaded and installed.\n\n"
            "Restart TM Ripper now to finish?",
            kind="success",
        ):
            # Must wait until THIS process exits before launching the new exe.
            # Starting it while we're still alive causes PyInstaller DLL load failures.
            self._spawn_after_exit(exe, kill_other_instances=False)
        else:
            self._notify("Update will be applied next time you open TM Ripper.", "info")

    def _ps_quote(self, path: str) -> str:
        """Single-quote a path for PowerShell (-Command)."""
        return "'" + path.replace("'", "''") + "'"

    def _spawn_after_exit(self, target: str, *, args: list[str] | None = None,
                          kill_other_instances: bool = False, settle_ms: int = 1200):
        """Quit this process, then start target once our PID is fully gone.

        Avoids installer mutex loops and PyInstaller "DLL failed to load" errors
        from relaunching while the old process is still tearing down.
        """
        self._persist()
        try:
            self.discord.close()
        except Exception:
            pass

        target = os.path.normpath(target)
        if not os.path.isfile(target):
            self._alert(APP_TITLE, f"Could not find:\n{target}", kind="error")
            self.is_busy = False
            self._update_action_buttons()
            return

        pid = os.getpid()
        arg_list = args or []
        arg_ps = ",".join(self._ps_quote(a) for a in arg_list)
        start_args = f"-ArgumentList @({arg_ps}) " if arg_ps else ""
        kill_ps = (
            "Get-Process -Name 'TM Ripper' -EA SilentlyContinue | "
            "Stop-Process -Force -EA SilentlyContinue; Start-Sleep -m 300; "
            if kill_other_instances else ""
        )
        # Wait for us to die, settle (temp/_MEI cleanup + AV), optional kill, then start.
        ps = (
            f"$p={pid};"
            f"while(Get-Process -Id $p -EA SilentlyContinue){{Start-Sleep -m 200}};"
            f"Start-Sleep -m {int(settle_ms)};"
            f"{kill_ps}"
            f"Start-Process -FilePath {self._ps_quote(target)} {start_args}"
            f"-WorkingDirectory {self._ps_quote(os.path.dirname(target) or '.')}"
        )
        try:
            flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            subprocess.Popen(
                ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps],
                close_fds=True,
                creationflags=flags,
            )
        except Exception as exc:  # noqa: BLE001
            self._alert(APP_TITLE, f"Could not schedule relaunch:\n{exc}", kind="error")
            self.is_busy = False
            self._update_action_buttons()
            return

        release_app_mutex()
        try:
            self.root.destroy()
        except Exception:
            pass
        os._exit(0)

    def _launch_installer_and_quit(self, path: str):
        """Quit first, then start Setup so its AppMutex/file locks aren't stuck on us."""
        self._log("Closing so the installer can update...")
        self._spawn_after_exit(
            path,
            args=["/FORCECLOSEAPPLICATIONS"],
            kill_other_instances=True,
            settle_ms=700,
        )

    # ----------------------------------------------------- Update yt-dlp
    def _update_downloader(self):
        if self.is_downloading or self.is_busy:
            self._notify("Please wait for the current task to finish.", "info")
            return
        self.is_busy = True
        self._update_action_buttons()
        self.status_var.set("Updating yt-dlp...")
        self._log("Updating yt-dlp via pip...")
        threading.Thread(target=self._update_worker, daemon=True).start()

    def _update_worker(self):
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "pip", "install", "--user", "-U", "yt-dlp"],
                capture_output=True, text=True, timeout=180,
            )
            out = (proc.stdout or "") + (proc.stderr or "")
            last = [ln for ln in out.splitlines() if ln.strip()]
            summary = last[-1] if last else "Done."
            ok = proc.returncode == 0
            self.msg_queue.put(("update_done", ok, summary))
        except Exception as exc:  # noqa: BLE001
            self.msg_queue.put(("update_done", False, str(exc)))

    # ----------------------------------------------------------- UI polling
    def _poll_queue(self):
        try:
            while True:
                msg = self.msg_queue.get_nowait()
                kind = msg[0]
                if kind == "progress":
                    _, pct, status = msg
                    self.progress.set(pct)
                    self.status_var.set(status)
                elif kind == "done":
                    # msg: done, title, filepath[, url, quality]
                    title = msg[1]
                    filepath = msg[2]
                    url = msg[3] if len(msg) > 3 else ""
                    quality = msg[4] if len(msg) > 4 else ""
                    self.last_file = filepath
                    self.progress.set(100)
                    self.status_var.set("Done!")
                    self._log(f"Saved: {title}")
                    self._record_history(title, filepath, url=url, quality=quality)
                    self._finish()
                    self._notify(f"Download complete!\n{title}", "success")
                elif kind == "cancelled":
                    self.progress.set(0)
                    self.status_var.set("Cancelled.")
                    self._log("Download cancelled.")
                    self._finish()
                elif kind == "error":
                    _, err = msg
                    self.status_var.set("Error.")
                    self._log(err, error=True)
                    self._finish()
                    self._notify(f"Download failed:\n{err}", "error", duration=6000)
                elif kind == "convert_done":
                    _, title, filepath, fmt_label = msg
                    self.last_file = filepath
                    self.progress.set(100)
                    self.is_busy = False
                    self.status_var.set("Conversion done!")
                    self._log(f"Converted ({fmt_label}): {title}")
                    self._record_history(title, filepath, quality=f"Converted → {fmt_label}")
                    self._update_action_buttons()
                    self.discord.set_idle()
                    self._notify(f"Converted!\n{title}", "success")
                elif kind == "convert_error":
                    _, err = msg
                    self.is_busy = False
                    self.progress.set(0)
                    self.status_var.set("Conversion failed.")
                    self._log("Convert failed: " + err, error=True)
                    self._update_action_buttons()
                    self.discord.set_idle()
                    self._notify(f"Conversion failed:\n{err}", "error", duration=7000)
                elif kind == "preview":
                    _, meta, img = msg
                    self.is_busy = False
                    self._show_preview(meta, img)
                    self._update_action_buttons()
                    self.discord.set_idle()
                elif kind == "preview_error":
                    _, err = msg
                    self.is_busy = False
                    self.meta_label.config(text="")
                    self.status_var.set("Preview failed.")
                    self._log("Preview failed: " + err, error=True)
                    self._update_action_buttons()
                    self.discord.set_idle()
                elif kind == "update_done":
                    _, ok, summary = msg
                    self.is_busy = False
                    self.status_var.set("Update complete." if ok else "Update failed.")
                    self._log(("yt-dlp: " if ok else "Update error: ") + summary, error=not ok)
                    self._update_action_buttons()
                    if ok:
                        self._notify("yt-dlp updated. Restart to use the new version.", "success")
                    else:
                        self._notify("yt-dlp update failed:\n" + summary, "error", duration=6000)
                elif kind == "update_check":
                    _, tag, portable_url, installer_url, manual = msg
                    self._handle_update_check(tag, portable_url, installer_url, manual)
                elif kind == "update_check_err":
                    _, err, manual = msg
                    self._log("Update check failed: " + err, error=True)
                    if manual:
                        self.status_var.set("Update check failed.")
                        self._notify("Couldn't check for updates:\n" + err, "warning")
                elif kind == "update_ready":
                    _, path, mode = msg
                    self.progress.set(100)
                    if mode == "swap":
                        self._apply_exe_update(path)
                    else:
                        self.status_var.set("Update downloaded. Launching installer...")
                        self._launch_installer_and_quit(path)
                elif kind == "update_err":
                    _, err = msg
                    self.is_busy = False
                    self.status_var.set("Update failed.")
                    self._log("Update download failed: " + err, error=True)
                    self._update_action_buttons()
                    self._notify("Update download failed:\n" + err, "error", duration=6000)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_queue)

    def _finish(self):
        self.is_downloading = False
        text = "Download" if self.theme == THEME_RETRO else "\u2b07  Download"
        self.download_btn.config(text=text)
        self._update_action_buttons()
        self.discord.set_idle()


_APP_MUTEX_HANDLE = None


def _create_app_mutex():
    """Named mutex so the installer's AppMutex can detect a running copy."""
    global _APP_MUTEX_HANDLE
    if sys.platform != "win32":
        return
    try:
        import ctypes

        handle = ctypes.windll.kernel32.CreateMutexW(None, False, "TMRipperRunningMutex")
        if handle:
            _APP_MUTEX_HANDLE = handle
    except Exception:
        pass


def release_app_mutex():
    """Drop the installer mutex early so Setup isn't stuck waiting on us."""
    global _APP_MUTEX_HANDLE
    if sys.platform != "win32" or not _APP_MUTEX_HANDLE:
        return
    try:
        import ctypes

        ctypes.windll.kernel32.CloseHandle(_APP_MUTEX_HANDLE)
    except Exception:
        pass
    _APP_MUTEX_HANDLE = None


def _enable_dpi_awareness():
    """Tell Windows we handle high-DPI so the UI (and file dialogs) stay crisp."""
    if sys.platform != "win32":
        return
    import ctypes

    try:  # Per-Monitor v2 (Win10 1703+): sharpest, handles dialogs well
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return
    except Exception:
        pass
    try:  # System DPI aware (Win8.1+)
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
        return
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def _system_dpi():
    if sys.platform != "win32":
        return 96
    import ctypes

    try:
        hdc = ctypes.windll.user32.GetDC(0)
        dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)  # LOGPIXELSX
        ctypes.windll.user32.ReleaseDC(0, hdc)
        return dpi or 96
    except Exception:
        return 96


def _cleanup_old_update():
    """Delete the previous exe left behind by an in-app update swap."""
    if getattr(sys, "frozen", False):
        old = sys.executable + ".old"
        try:
            if os.path.exists(old):
                os.remove(old)
        except OSError:
            pass


def main():
    _enable_dpi_awareness()
    _create_app_mutex()
    _cleanup_old_update()
    if _DND_AVAILABLE:
        root = TkinterDnD.Tk()
    else:
        root = tk.Tk()
    DownloaderApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
