@echo off
chcp 65001 >nul
echo ============================================================
echo  SUMO 路网生成脚本  /  Intersection Network Generator
echo ============================================================
echo.

:: ── Locate SUMO ──────────────────────────────────────────────────────
if not "%SUMO_HOME%"=="" goto :found_sumo

echo [INFO] SUMO_HOME not set — scanning common install locations...

set "_CANDIDATES=C:\Program Files (x86)\Eclipse\Sumo C:\Program Files\Eclipse\Sumo C:\Sumo D:\Sumo"
for %%P in (%_CANDIDATES%) do (
    if exist "%%P\bin\netconvert.exe" (
        set "SUMO_HOME=%%P"
        goto :found_sumo
    )
)

echo [ERROR] SUMO installation not found.
echo.
echo  Please install SUMO (>= 1.15) from:
echo    https://sumo.dlr.de/docs/Downloads.php
echo.
echo  Then either:
echo    a) Re-run this script (SUMO_HOME will be auto-detected), OR
echo    b) Set the environment variable manually:
echo       setx SUMO_HOME "C:\Program Files (x86)\Eclipse\Sumo"
echo.
pause
exit /b 1

:found_sumo
echo [OK] SUMO_HOME = %SUMO_HOME%
echo.

:: ── Switch to sumo_net directory ──────────────────────────────────────
cd /d "%~dp0"

:: ── Remove stale net file ─────────────────────────────────────────────
if exist intersection.net.xml (
    echo [INFO] Removing previous intersection.net.xml ...
    del /f intersection.net.xml
)

:: ── Run netconvert ────────────────────────────────────────────────────
echo [INFO] Running netconvert ...
echo.

"%SUMO_HOME%\bin\netconvert.exe" ^
    --node-files=intersection.nod.xml ^
    --edge-files=intersection.edg.xml ^
    --connection-files=intersection.con.xml ^
    --output-file=intersection.net.xml ^
    --no-turnarounds ^
    --junctions.corner-detail=5 ^
    --tls.default-type=static ^
    --output.street-names=false ^
    --output.original-names=false ^
    2>&1

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ERROR] netconvert failed — see messages above.
    pause
    exit /b 1
)

:: ── Verify output ─────────────────────────────────────────────────────
if not exist intersection.net.xml (
    echo [ERROR] intersection.net.xml was not created.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo  [OK] intersection.net.xml generated successfully.
echo.
echo  Next step: run  python main.py  from the project root.
echo ============================================================
echo.
pause
