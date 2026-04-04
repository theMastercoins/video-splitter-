@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"
if exist "video.mkv" (
  python split_video.py video.mkv
) else (
  python split_video.py video.mp4
)
pause
