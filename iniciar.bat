@echo off
title C-4 en tiempo real
cd /d "%~dp0"
where py >/dev/null 2>/dev/null && (py -3 iniciar.py %* & goto fin)
where python >/dev/null 2>/dev/null && (python iniciar.py %* & goto fin)
echo No encuentro Python. Descargalo de https://www.python.org/downloads/ (marca "Add Python to PATH").
:fin
pause
