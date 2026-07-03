@echo off
REM ============================================================
REM  Build a standalone Windows .exe for Social Video Downloader
REM  Output ends up in the  dist\  folder.
REM ============================================================
cd /d "%~dp0"

echo Installing dependencies...
py -m pip install --user -r requirements.txt
py -m pip install --user pyinstaller

echo Building app icon...
py make_icon.py

echo Building executable (this can take a couple of minutes)...
py -m PyInstaller ^
  --noconfirm ^
  --onefile ^
  --windowed ^
  --name "Social Video Downloader" ^
  --icon "assets\icon.ico" ^
  --add-data "assets;assets" ^
  --collect-all tkinterdnd2 ^
  --collect-all yt_dlp ^
  app.py

echo.
if exist "dist\Social Video Downloader.exe" (
    echo SUCCESS - your app is at:  dist\Social Video Downloader.exe
    echo NOTE: ffmpeg must be on the PATH for HD merging and MP3 conversion.
) else (
    echo Build did not produce an exe. Check the messages above.
)
echo.
pause
