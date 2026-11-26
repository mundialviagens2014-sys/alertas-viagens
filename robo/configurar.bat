@echo off
cd /d "%~dp0"
py robo_reservas.py --configurar || python robo_reservas.py --configurar
pause
