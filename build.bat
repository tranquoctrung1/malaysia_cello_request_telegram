@echo off
echo === BUILD EXE WITH PYINSTALLER ===

REM dùng đúng python trong máy
python --version

REM xóa build cũ
rmdir /s /q build
rmdir /s /q dist

REM build exe (KHONG bundle .env - token phai o file .env canh exe, khong nam trong binary)
pyinstaller ^
 --onefile ^
 --noconsole ^
 main.py

echo.
echo === BUILD DONE ===
pause
