@echo off
REM Double-click this file to run the LeetCode Comparator web UI.
REM It opens its own window and stays running until you close it.
cd /d "%~dp0"
echo Starting LeetCode Profile Comparator...
echo Your browser should open automatically at http://localhost:8010
echo (If not, open that address yourself.)
echo Close this window (or press Ctrl+C) to stop the server.
echo.
python server.py
echo.
echo Server stopped. Press any key to close this window.
pause >nul
