# Social Video Downloader

A simple desktop app (GUI) to download videos from **TikTok**, **Instagram Reels**,
**Facebook Reels**, and **YouTube Shorts** by pasting a link.

Built with Python + `tkinter` (GUI) and [`yt-dlp`](https://github.com/yt-dlp/yt-dlp) (downloader).

## Features

- Paste **or drag-and-drop** a link and download with one click
- Auto-detects the platform (TikTok / Instagram / Facebook / YouTube)
- **Preview** the title, uploader, duration, resolution, and thumbnail before downloading
- Choose quality: Best, 1080p, 720p, or Audio-only (MP3)
- Pick your save folder
- Live progress with **speed, ETA, and file size**
- **Stop** button to cancel an in-progress download
- **Open file** / **Show in folder** buttons when a download finishes
- **Tools > Update downloader** keeps yt-dlp current so sites don't break
- Non-blocking UI (downloads run in a background thread)
- **Two looks** you can switch anytime under **Settings > Preferences**:
  - *Legacy* - clean modern flat UI
  - *Retro* - Windows 95 style UI
  - Your choice is remembered in `settings.json`

## Setup

1. Install [Python 3.9+](https://www.python.org/downloads/) (you have 3.14).
2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. (Recommended) Install **ffmpeg** so high-quality streams can be merged and
   MP3 conversion works. It's already installed on this machine. If you ever move
   the app, get it from https://ffmpeg.org/download.html and make sure `ffmpeg`
   is on your PATH.

## Run

Double-click **`run.bat`**, or from a terminal:

```bash
python app.py
```

## How to use

1. Copy a video link from TikTok, Instagram, Facebook, or YouTube.
2. Click **Paste**, paste into the box, or **drag the link** onto the window.
3. (Optional) Click **Preview** to see the title + thumbnail first.
4. Choose a quality and save folder (optional).
5. Click **Download**. Use **Stop** to cancel. When finished, use **Open file**
   or **Show in folder**. Files are saved to your **Downloads** folder by default.

## Build a Windows installer (recommended for sharing)

To create a proper `Setup.exe` that installs the app (Start Menu + optional
desktop shortcut, an entry in Add/Remove Programs, and a bundled ffmpeg so it
works with zero setup):

1. Install [Inno Setup](https://jrsoftware.org/isdl.php) once (`winget install JRSoftware.InnoSetup`).
2. Double-click **`build_installer.bat`**.
3. The finished installer appears at **`installer_output\SocialVideoDownloader-Setup.exe`**.

Give that single `Setup.exe` to anyone — they run it, click through the wizard,
and get a fully working app (ffmpeg included, no Python needed). It installs
per-user by default, so no admin prompt is required. Preferences are stored in
`%APPDATA%\SocialVideoDownloader`.

## Build just a portable .exe (no installer)

To create a single portable `.exe` instead:

1. Double-click **`build_exe.bat`**.
2. The finished app appears at **`dist\Social Video Downloader.exe`**.

The app auto-detects a bundled `ffmpeg`/`ffprobe` sitting next to it (or in a
`vendor\ffmpeg` folder), and otherwise falls back to any `ffmpeg` on the PATH.

## Notes

- Only download content you have the right to save. Respect each platform's
  Terms of Service and copyright.
- Some private/age-restricted content may require you to be logged in and is not
  supported out of the box.
- If a specific site stops working, update the downloader: `pip install -U yt-dlp`.
