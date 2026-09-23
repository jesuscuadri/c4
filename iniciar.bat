@echo off
title C-4 en tiempo real
cd /d "%~dp0"
where py >/dev/null 2>/dev/null && (py -3 c4_tiempo_real.py %* & goto fin)
where python >/dev/null 2>/dev/null && (python c4_tiempo_real.py %* & goto fin)
echo No encuentro Python. Descargalo de https://www.python.org/downloads/ (marca "Add Python to PATH").
:fin
pause
