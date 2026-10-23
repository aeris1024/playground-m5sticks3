@echo off
setlocal
title M5StickS3 Head Tracker

pushd "%~dp0"
if errorlevel 1 (
    echo Cannot open the project folder.
    pause
    exit /b 1
)

set "HEAD_TRACKER_UV=uv.exe"
where uv.exe >nul 2>&1
if errorlevel 1 (
    if exist "%USERPROFILE%\.local\bin\uv.exe" (
        set "HEAD_TRACKER_UV=%USERPROFILE%\.local\bin\uv.exe"
    ) else (
        echo uv was not found. Install uv, then try again.
        echo https://docs.astral.sh/uv/getting-started/installation/
        pause
        popd
        exit /b 1
    )
)

echo Starting Head Tracker Monitor. The first launch may take a little longer.
echo This window will close when you exit the GUI.
"%HEAD_TRACKER_UV%" run --project "." --frozen head-tracker-monitor
set "HEAD_TRACKER_RESULT=%ERRORLEVEL%"
if not "%HEAD_TRACKER_RESULT%"=="0" (
    echo.
    echo The app did not exit normally. Check the error message above.
    pause
)
popd
exit /b %HEAD_TRACKER_RESULT%
