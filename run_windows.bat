@echo off
setlocal

if not exist "dist\vstarcam\vstarcam.exe" (
    echo [ERROR] No se encuentra dist\vstarcam\vstarcam.exe
    echo         Ejecuta primero build_windows.bat
    pause
    exit /b 1
)

echo Iniciando CameraApp...
cd dist\vstarcam
start "" vstarcam.exe
