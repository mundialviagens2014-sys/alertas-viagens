@echo off
cd /d "%~dp0"
title Robo de reservas do MVO
:de_novo
py robo_reservas.py || python robo_reservas.py
echo O robo parou. Reiniciando em 30 segundos (feche esta janela para desligar)...
timeout /t 30
goto de_novo
