@echo off
cd /d "%~dp0"
echo Instalando o robo de reservas do MVO...
py -m pip install --upgrade -r requisitos.txt || python -m pip install --upgrade -r requisitos.txt
py -m playwright install chromium || python -m playwright install chromium
echo.
echo Pronto. Agora rode configurar.bat
pause
