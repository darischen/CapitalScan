@echo off
setlocal
set CAPSCAN_ENV=prod
call "%~dp0..\.venv\Scripts\activate.bat"
cscan nightly
endlocal
