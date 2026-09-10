@echo off
rem Windows：双击此文件启动（pythonw 无控制台窗口）
cd /d "%~dp0.."
start "" .venv\Scripts\pythonw.exe desktop\app.py --config config.yaml
