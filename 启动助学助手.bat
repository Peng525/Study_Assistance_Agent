@echo off
chcp 65001 >nul
title AI Study Assistant

"%~dp0backend\venv\Scripts\python.exe" "%~dp0start.py"

pause