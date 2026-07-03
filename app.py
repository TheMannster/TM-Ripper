"""
Social Video Downloader
------------------------
A desktop GUI to download videos from TikTok, Instagram Reels, Facebook Reels,
and YouTube Shorts by pasting (or dropping) a link.

Features:
  * Two switchable looks: Legacy (modern flat) and Retro (Windows 95)
  * Preview title + thumbnail before downloading
  * Cancel/Stop an in-progress download
  * Live progress with speed, ETA, and file size
  * Open the file / show it in its folder when done
  * Drag-and-drop a link onto the window
  * One-click "Update downloader" (keeps yt-dlp current)

Powered by yt-dlp. Run with:  pythonw app.py
"""

import io
import os
import re
import sys
import json
import queue
import threading
import subprocess
import urllib.request
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

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


APP_TITLE = "Social Video Downloader"
DEFAULT_DOWNLOAD_DIR = os.path.join(os.path.expanduser("~"), "Downloads")

if getattr(sys, "frozen", False):
    # Running as a PyInstaller .exe.
    BUNDLE_DIR = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))  # bundled assets
    APP_DIR = os.path.dirname(sys.executable)  # install dir (ffmpeg lives here)
    # Settings go to %APPDATA% since Program Files isn't user-writable.
    CONFIG_DIR = os.path.join(os.environ.get("APPDATA") or APP_DIR, "SocialVideoDownloader")
else:
    BUNDLE_DIR = os.path.dirname(os.path.abspath(__file__))
    APP_DIR = BUNDLE_DIR
    CONFIG_DIR = BUNDLE_DIR

try:
    os.makedirs(CONFIG_DIR, exist_ok=True)
except OSError:
    CONFIG_DIR = APP_DIR

SETTINGS_PATH = os.path.join(CONFIG_DIR, "settings.json")
ICON_ICO = os.path.join(BUNDLE_DIR, "assets", "icon.ico")
ICON_PNG = os.path.join(BUNDLE_DIR, "assets", "icon.png")


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
    for d in candidates:
        if os.path.isfile(os.path.join(d, "ffmpeg.exe")):
            return d
    return None


FFMPEG_DIR = find_ffmpeg_dir()

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

# --- Modern (legacy) palette -------------------------------------------
PRIMARY = "#4f46e5"
PRIMARY_ACTIVE = "#4338ca"
BG_LEGACY = "#f4f4f6"


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


class ModernProgress(ttk.Progressbar):
    def set(self, value):
        self.config(value=value)


class DownloaderApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("680x680")
        self.root.minsize(620, 620)
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
        self.url_var.trace_add("write", self._on_url_change)

        self.log_history: list[tuple[str, bool]] = []

        self._build_all()
        self._poll_queue()

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
        save_settings(
            {"theme": self.theme, "folder": self.folder_var.get(), "quality": self.quality_var.get()}
        )

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
        self._build_menu()
        if self.theme == THEME_RETRO:
            self.root.configure(bg=FACE)
            self._build_retro_ui()
        else:
            self.root.configure(bg=BG_LEGACY)
            self._build_legacy_ui()
        self._restore_log()
        self._on_url_change()
        self._register_dnd()
        self._update_action_buttons()

    # ------------------------------------------------------------ Menu bar
    def _build_menu(self):
        menubar = tk.Menu(self.root, tearoff=0)

        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="Open save folder", command=self._open_folder)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.root.destroy)
        menubar.add_cascade(label="File", menu=file_menu)

        settings_menu = tk.Menu(menubar, tearoff=0)
        settings_menu.add_command(label="Preferences...", command=self._open_settings)
        settings_menu.add_separator()
        settings_menu.add_radiobutton(
            label="Legacy (Modern) look", variable=self.theme_var, value=THEME_LEGACY,
            command=lambda: self._apply_theme(THEME_LEGACY),
        )
        settings_menu.add_radiobutton(
            label="Retro (Windows 95) look", variable=self.theme_var, value=THEME_RETRO,
            command=lambda: self._apply_theme(THEME_RETRO),
        )
        menubar.add_cascade(label="Settings", menu=settings_menu)

        tools_menu = tk.Menu(menubar, tearoff=0)
        tools_menu.add_command(label="Update downloader (yt-dlp)", command=self._update_downloader)
        menubar.add_cascade(label="Tools", menu=tools_menu)

        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="About...", command=self._about)
        menubar.add_cascade(label="Help", menu=help_menu)

        self.root.config(menu=menubar)

    def _about(self):
        messagebox.showinfo(
            "About " + APP_TITLE,
            APP_TITLE + "\n\nDownloads TikTok, Instagram Reels, Facebook Reels,\n"
            "and YouTube Shorts.\n\nPowered by yt-dlp.",
        )

    # ---------------------------------------------------- Settings dialog
    def _open_settings(self):
        win = tk.Toplevel(self.root)
        win.title("Settings")
        win.transient(self.root)
        win.resizable(False, False)
        win.grab_set()

        retro = self.theme == THEME_RETRO
        bg = FACE if retro else "#ffffff"
        win.configure(bg=bg)
        choice = tk.StringVar(value=self.theme)

        pad = tk.Frame(win, bg=bg)
        pad.pack(padx=16, pady=14, fill="both")
        header_font = WIN95_FONT_BOLD if retro else ("Segoe UI", 11, "bold")
        body_font = WIN95_FONT if retro else ("Segoe UI", 10)

        tk.Label(pad, text="Appearance", bg=bg, fg="#000000", font=header_font).pack(anchor="w")
        tk.Label(pad, text="Choose how the app looks:", bg=bg, fg="#333333", font=body_font).pack(
            anchor="w", pady=(2, 10)
        )
        for label, value in (
            ("Legacy  -  clean modern flat UI", THEME_LEGACY),
            ("Retro  -  Windows 95 style", THEME_RETRO),
        ):
            tk.Radiobutton(
                pad, text=label, variable=choice, value=value, bg=bg, fg="#000000",
                activebackground=bg, selectcolor=LIGHT if retro else "#ffffff",
                font=body_font, anchor="w",
            ).pack(anchor="w", pady=1)

        btn_row = tk.Frame(pad, bg=bg)
        btn_row.pack(fill="x", pady=(16, 0))

        def apply_and_close():
            self._apply_theme(choice.get())
            win.destroy()

        if retro:
            make_button(btn_row, "OK", apply_and_close, width=8).pack(side="right", padx=(6, 0))
            make_button(btn_row, "Cancel", win.destroy, width=8).pack(side="right")
        else:
            ttk.Button(btn_row, text="OK", command=apply_and_close).pack(side="right", padx=(6, 0))
            ttk.Button(btn_row, text="Cancel", command=win.destroy).pack(side="right")

        win.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - win.winfo_width()) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - win.winfo_height()) // 3
        win.geometry(f"+{max(x, 0)}+{max(y, 0)}")

    # ------------------------------------------------------- Retro UI build
    def _build_retro_ui(self):
        outer = tk.Frame(self.root, bg=FACE, bd=0)
        outer.pack(fill="both", expand=True, padx=6, pady=6)

        banner = tk.Frame(outer, bg=NAVY, bd=0)
        banner.pack(fill="x")
        tk.Label(banner, text="  Social Video Downloader", bg=NAVY, fg="white",
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
        self.show_folder_btn.pack(side="left")

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

        status = tk.Label(self.root, textvariable=self.status_var, bg=FACE, fg="#000000",
                          font=WIN95_FONT, relief="sunken", bd=1, anchor="w", padx=6)
        status.pack(side="bottom", fill="x")

        if yt_dlp is None:
            self._log("yt-dlp is not installed. Run:  pip install -r requirements.txt", error=True)

    # ------------------------------------------------------ Legacy UI build
    def _build_legacy_ui(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TButton", padding=8, font=("Segoe UI", 10))
        style.configure("Accent.TButton", padding=10, font=("Segoe UI", 11, "bold"),
                        foreground="white", background=PRIMARY)
        style.map("Accent.TButton", background=[("active", PRIMARY_ACTIVE)])
        style.configure("TLabel", font=("Segoe UI", 10), background=BG_LEGACY)
        style.configure("TFrame", background=BG_LEGACY)
        style.configure("Card.TFrame", background="#ffffff")
        style.configure("Header.TLabel", font=("Segoe UI", 16, "bold"), background=BG_LEGACY)
        style.configure("Sub.TLabel", font=("Segoe UI", 9), foreground="#666", background=BG_LEGACY)
        style.configure("Meta.TLabel", font=("Segoe UI", 9), background="#ffffff")

        container = ttk.Frame(self.root, padding=20)
        container.pack(fill="both", expand=True)

        ttk.Label(container, text="Social Video Downloader", style="Header.TLabel").pack(anchor="w")
        ttk.Label(container,
                  text="Download TikTok  \u2022  Instagram Reels  \u2022  Facebook Reels  \u2022  YouTube Shorts",
                  style="Sub.TLabel").pack(anchor="w", pady=(0, 15))

        url_frame = ttk.Frame(container)
        url_frame.pack(fill="x")
        ttk.Label(url_frame, text="Video link").pack(anchor="w")
        entry_row = ttk.Frame(url_frame)
        entry_row.pack(fill="x", pady=(4, 2))
        self.url_entry = ttk.Entry(entry_row, textvariable=self.url_var, font=("Segoe UI", 11))
        self.url_entry.pack(side="left", fill="x", expand=True, ipady=4)
        ttk.Button(entry_row, text="Paste", command=self._paste).pack(side="left", padx=(8, 0))
        ttk.Button(entry_row, text="Clear", command=self._clear).pack(side="left", padx=(6, 0))
        row2 = ttk.Frame(url_frame)
        row2.pack(fill="x", pady=(2, 12))
        self.platform_label = ttk.Label(row2, text="Platform: \u2014", style="Sub.TLabel")
        self.platform_label.pack(side="left")
        self.preview_btn = ttk.Button(row2, text="Preview", command=self._start_preview)
        self.preview_btn.pack(side="right")

        prev_card = ttk.Frame(container, style="Card.TFrame", padding=10)
        prev_card.pack(fill="x", pady=(0, 12))
        self.thumb_label = tk.Label(prev_card, bg="#ffffff", text="(no preview)", fg="#999",
                                    font=("Segoe UI", 9), width=30, height=6)
        self.thumb_label.pack(side="left", padx=(0, 12))
        self.meta_label = ttk.Label(prev_card, text="", style="Meta.TLabel", justify="left",
                                    anchor="nw", wraplength=340)
        self.meta_label.pack(side="left", fill="both", expand=True)

        out_frame = ttk.Frame(container)
        out_frame.pack(fill="x")
        ttk.Label(out_frame, text="Save to folder").pack(anchor="w")
        out_row = ttk.Frame(out_frame)
        out_row.pack(fill="x", pady=(4, 12))
        ttk.Entry(out_row, textvariable=self.folder_var, font=("Segoe UI", 10)).pack(
            side="left", fill="x", expand=True, ipady=3)
        ttk.Button(out_row, text="Browse", command=self._browse).pack(side="left", padx=(8, 0))
        ttk.Button(out_row, text="Open", command=self._open_folder).pack(side="left", padx=(6, 0))

        opt_frame = ttk.Frame(container)
        opt_frame.pack(fill="x", pady=(0, 12))
        ttk.Label(opt_frame, text="Quality").pack(side="left")
        ttk.Combobox(opt_frame, textvariable=self.quality_var, state="readonly", width=22,
                     values=self._quality_options()).pack(side="left", padx=(8, 0))

        btn_row = ttk.Frame(container)
        btn_row.pack(fill="x", pady=(0, 10))
        self.download_btn = ttk.Button(btn_row, text="\u2b07  Download", style="Accent.TButton",
                                       command=self._start_download)
        self.download_btn.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self.cancel_btn = ttk.Button(btn_row, text="Stop", command=self._cancel_download)
        self.cancel_btn.pack(side="left")

        self.progress = ModernProgress(container, mode="determinate", maximum=100)
        self.progress.pack(fill="x")
        ttk.Label(container, textvariable=self.status_var, style="Sub.TLabel").pack(anchor="w", pady=(4, 6))

        done_row = ttk.Frame(container)
        done_row.pack(fill="x", pady=(0, 8))
        self.open_file_btn = ttk.Button(done_row, text="Open file", command=self._open_last_file)
        self.open_file_btn.pack(side="left", padx=(0, 6))
        self.show_folder_btn = ttk.Button(done_row, text="Show in folder", command=self._show_in_folder)
        self.show_folder_btn.pack(side="left")

        ttk.Label(container, text="Log").pack(anchor="w")
        log_frame = ttk.Frame(container)
        log_frame.pack(fill="both", expand=True, pady=(4, 0))
        self.log = tk.Text(log_frame, height=6, wrap="word", font=("Consolas", 9))
        self.log.configure(bg="#1e1e2e", fg="#cdd6f4", insertbackground="#cdd6f4")
        self.log.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(log_frame, command=self.log.yview)
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
            messagebox.showinfo(APP_TITLE, "No downloaded file to open yet.")

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

    # -------------------------------------------------------------- Preview
    def _start_preview(self):
        if self.is_downloading or self.is_busy or yt_dlp is None:
            return
        url = self.url_var.get().strip()
        if not url or not re.match(r"^https?://", url):
            messagebox.showwarning(APP_TITLE, "Please paste a valid video link first.")
            return
        self.is_busy = True
        self._update_action_buttons()
        self.status_var.set("Fetching preview...")
        self.meta_label.config(text="Loading...")
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
            messagebox.showerror(APP_TITLE, "yt-dlp is not installed.\n\nRun:\npip install -r requirements.txt")
            return
        url = self.url_var.get().strip()
        if not url or not re.match(r"^https?://", url):
            messagebox.showwarning(APP_TITLE, "Please paste a valid video link (starting with http).")
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
            self.msg_queue.put(("done", title, filepath))
        except DownloadCancelled:
            self.msg_queue.put(("cancelled",))
        except Exception as exc:  # noqa: BLE001
            self.msg_queue.put(("error", str(exc)))

    # ----------------------------------------------------- Update yt-dlp
    def _update_downloader(self):
        if self.is_downloading or self.is_busy:
            messagebox.showinfo(APP_TITLE, "Please wait for the current task to finish.")
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
                    _, title, filepath = msg
                    self.last_file = filepath
                    self.progress.set(100)
                    self.status_var.set("Done!")
                    self._log(f"Saved: {title}")
                    self._finish()
                    messagebox.showinfo(APP_TITLE, f"Download complete!\n\n{title}")
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
                    messagebox.showerror(APP_TITLE, f"Download failed:\n\n{err}")
                elif kind == "preview":
                    _, meta, img = msg
                    self.is_busy = False
                    self._show_preview(meta, img)
                    self._update_action_buttons()
                elif kind == "preview_error":
                    _, err = msg
                    self.is_busy = False
                    self.meta_label.config(text="")
                    self.status_var.set("Preview failed.")
                    self._log("Preview failed: " + err, error=True)
                    self._update_action_buttons()
                elif kind == "update_done":
                    _, ok, summary = msg
                    self.is_busy = False
                    self.status_var.set("Update complete." if ok else "Update failed.")
                    self._log(("yt-dlp: " if ok else "Update error: ") + summary, error=not ok)
                    self._update_action_buttons()
                    messagebox.showinfo(
                        APP_TITLE,
                        ("yt-dlp updated.\nRestart the app to use the new version."
                         if ok else "Update failed:\n\n" + summary),
                    )
        except queue.Empty:
            pass
        self.root.after(100, self._poll_queue)

    def _finish(self):
        self.is_downloading = False
        text = "Download" if self.theme == THEME_RETRO else "\u2b07  Download"
        self.download_btn.config(text=text)
        self._update_action_buttons()


def main():
    if _DND_AVAILABLE:
        root = TkinterDnD.Tk()
    else:
        root = tk.Tk()
    DownloaderApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
