@echo off
setlocal
cd /d "%~dp0"

set "RELEASE_VERSION=%~1"
if "%RELEASE_VERSION%"=="" set /p RELEASE_VERSION=<VERSION

where ffmpeg >nul 2>nul || (
  echo FFmpeg build tools are missing. Install once with: choco install ffmpeg
  exit /b 1
)
where ffprobe >nul 2>nul || (
  echo ffprobe is missing. Install once with: choco install ffmpeg
  exit /b 1
)

py -3.12 -m venv .build-venv || exit /b 1
.build-venv\Scripts\python.exe -m pip install --upgrade pip || exit /b 1
.build-venv\Scripts\python.exe -m pip install -r requirements-build.txt || exit /b 1
.build-venv\Scripts\python.exe scripts\build_dist.py --version "%RELEASE_VERSION%" || exit /b 1

echo Portable ZIP is ready in: %CD%\release
endlocal
