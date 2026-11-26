@echo off
cd /d "%~dp0"
set /p CIA=Companhia (LA, G3 ou AD): 
set /p LOC=Localizador: 
set /p SOB=Sobrenome: 
py robo_reservas.py --aprender %CIA% %LOC% %SOB% || python robo_reservas.py --aprender %CIA% %LOC% %SOB%
pause
