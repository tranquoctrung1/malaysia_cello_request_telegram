@echo off
echo === BUILD EXE WITH PYINSTALLER ===

REM dùng đúng python trong máy
python --version

REM xóa build cũ
rmdir /s /q build
rmdir /s /q dist

REM build exe
pyinstaller ^
 --onefile ^
 --noconsole ^
 --add-data ".env;." ^
 main.py

echo.
echo === BUILD DONE ===
pause
