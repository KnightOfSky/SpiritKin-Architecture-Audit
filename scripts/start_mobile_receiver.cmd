@echo off
setlocal
cd /d "%~dp0.."
python -u scripts\mobile_link_receiver.py --host 0.0.0.0 --port 8791
