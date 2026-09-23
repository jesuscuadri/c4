@echo off
title C-4 en tiempo real (con iPhone)
cd /d "%~dp0"
where py >nul 2>nul && (py -3 c4_tiempo_real.py --movil %* & goto fin)
where python >nul 2>nul && (python c4_tiempo_real.py --movil %* & goto fin)
echo No encuentro Python. Descargalo de https://www.python.org/downloads/ (marca "Add Python to PATH").
:fin
pause
