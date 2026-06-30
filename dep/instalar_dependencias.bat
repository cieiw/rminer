@echo off
setlocal
cd /d "%~dp0.."

echo Instalando dependencias do Rminer...
py -m pip install --upgrade pip
py -m pip install customtkinter playwright instaloader yt-dlp
py -m playwright install chromium

echo.
echo Dependencias instaladas.
pause
