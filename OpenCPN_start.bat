@echo off
chcp 65001 >nul
rem Запуск вместе с OpenCPN: сначала MarineVHF (приём АИС, окно свёрнуто), затем OpenCPN.
rem Положите этот файл рядом с MarineVHF.exe. Если OpenCPN установлен в другое место — впишите путь в OPENCPN ниже.
setlocal
cd /d "%~dp0"
set "OPENCPN="
if exist "%ProgramFiles%\OpenCPN\opencpn.exe" set "OPENCPN=%ProgramFiles%\OpenCPN\opencpn.exe"
if exist "%ProgramFiles(x86)%\OpenCPN\opencpn.exe" set "OPENCPN=%ProgramFiles(x86)%\OpenCPN\opencpn.exe"
if exist "%LOCALAPPDATA%\Programs\OpenCPN\opencpn.exe" set "OPENCPN=%LOCALAPPDATA%\Programs\OpenCPN\opencpn.exe"
start "" "%~dp0MarineVHF.exe" --ais --minimized
timeout /t 2 /nobreak >nul
if defined OPENCPN (
  start "" "%OPENCPN%"
) else (
  echo OpenCPN не найден. Откройте файл в блокноте и впишите путь в строку set "OPENCPN=...".
  pause
)
