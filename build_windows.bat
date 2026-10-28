@echo off
setlocal

echo ========================================
echo  CameraApp - Build for Windows
echo ========================================
echo.

where python >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Python no encontrado. Instala Python 3.9+ desde https://www.python.org/
    echo         Asegurate de marcar "Add Python to PATH"
    pause
    exit /b 1
)

echo [INFO] Instalando dependencias...
python -m pip install --upgrade pip
python -m pip install -r requirements-build.txt

echo.
echo [INFO] Limpiando builds anteriores...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo.
echo [INFO] Generando ejecutable con PyInstaller...
pyinstaller vstarcam.spec --clean

echo.
echo ========================================
echo  BUILD COMPLETADO
echo ========================================
echo.
echo  El ejecutable esta en: dist\vstarcam\vstarcam.exe
echo.
echo  Para ejecutarlo: run_windows.bat
echo  o directamente: dist\vstarcam\vstarcam.exe
echo.
pause
