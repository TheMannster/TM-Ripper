@echo off
REM ============================================================
REM  Build the full Windows installer for Social Video Downloader
REM  1) builds the app .exe with PyInstaller
REM  2) compiles the Setup.exe with Inno Setup
REM  Output: installer_output\SocialVideoDownloader-Setup.exe
REM ============================================================
cd /d "%~dp0"

echo [1/4] Installing dependencies...
py -m pip install --user -r requirements.txt
py -m pip install --user pyinstaller

echo [2/4] Building app icon...
py make_icon.py

echo [3/4] Building app executable...
py -m PyInstaller --noconfirm --onefile --windowed ^
  --name "Social Video Downloader" ^
  --icon "assets\icon.ico" ^
  --add-data "assets;assets" ^
  --collect-all tkinterdnd2 ^
  --collect-all yt_dlp ^
  app.py

echo [4/4] Compiling installer with Inno Setup...
set "ISCC=C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=ISCC.exe"
"%ISCC%" installer.iss

echo.
if exist "installer_output\SocialVideoDownloader-Setup.exe" (
    echo SUCCESS - installer is at:  installer_output\SocialVideoDownloader-Setup.exe
) else (
    echo Installer build failed. Check the messages above.
)
echo.
pause
