@echo off
chcp 65001 >nul
rem Сборка portable-версии MarineVHF (папка с exe). Запускать из папки проекта.
rem Нужен Python 3.8 (64-бит) — это последний Python, работающий в Windows 7:  https://www.python.org/downloads/release/python-3810/
rem Перед запуском положите в папку dlls\ файлы librtlsdr.dll, libusb-1.0.dll (и libwinpthread-1.dll, если есть) из x64-сборки rtl-sdr.
setlocal
cd /d "%~dp0"
if not exist dlls\librtlsdr.dll (
  echo Нет dlls\librtlsdr.dll - положите туда DLL из x64-сборки rtl-sdr ^(см. README^)
  exit /b 1
)
py -3.8 --version >nul 2>&1 || (
  echo Не найден Python 3.8. Установите Python 3.8.10 x64 ^(галочка "py launcher"^) и запустите снова.
  exit /b 1
)
if not exist .venv-build (
  py -3.8 -m venv .venv-build || exit /b 1
)
call .venv-build\Scripts\activate.bat
python -m pip install --upgrade "pip<25" || exit /b 1
pip install -r requirements-build.txt || exit /b 1
python selftest.py || (echo Самопроверка не прошла & exit /b 1)
pyinstaller --noconfirm --clean --windowed --name MarineVHF --icon app.ico ^
  --collect-all sounddevice --hidden-import lameenc ^
  --exclude-module matplotlib --exclude-module pandas --exclude-module IPython --exclude-module pytest ^
  --exclude-module scipy --exclude-module PIL ^
  app.py || exit /b 1
copy /y dlls\*.dll dist\MarineVHF\ >nul
copy /y README.md dist\MarineVHF\ >nul
copy /y OpenCPN_start.bat dist\MarineVHF\ >nul
copy /y "OpenCPN-подключение.txt" dist\MarineVHF\ >nul
echo.
echo Готово: dist\MarineVHF\MarineVHF.exe  ^(папку можно копировать целиком, например на флешку^)
