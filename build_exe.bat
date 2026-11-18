@echo off
rem Rebuild WBOX (onedir) and deploy into %OUTDIR% (default: %ROOT%app)
rem Requires C:\Python38 + PyInstaller 5.13.2; src\tools\upx.exe is optional
rem
rem NOTE: only program files are replaced; %OUTDIR%\config.json / guard.log are kept.
rem
rem v2.3.0 revision: a locked %OUTDIR%\WBOX.exe aborts the build (see :busy).
rem Pointing OUTDIR at a live install is fine as long as no guard is running.
rem
rem OUTDIR lets you build into a scratch directory, e.g.
rem   set OUTDIR=D:\06-Temp\wbox-s7\app && build_exe.bat
rem so a smoke test never overwrites the deployed app\ that the guard runs from.
rem Keep this file ASCII-only: cmd.exe would misinterpret UTF-8 comments.
setlocal
rem v2.3.0 final: reproducible build. Both values are pinned, before any
rem PyInstaller call:
rem   PYTHONHASHSEED=0     - without it the hash seed is random per process and
rem                          the bytecode of ftplib/hashlib/zipfile/netrc (string
rem                          constant interning order) changes between builds.
rem   SOURCE_DATE_EPOCH    - PyInstaller 5.13.2 writes this into the PE
rem                          TimeDateStamp; otherwise it stamps the build time.
rem 1000000000 = 2001-09-09T01:46:40Z. Never change these two numbers: every
rem historical size/md5 comparison (and the installer's payload) stops matching.
set PYTHONHASHSEED=0
set SOURCE_DATE_EPOCH=1000000000
if "%PY%"=="" set PY=C:\Python38\python.exe
set ROOT=%~dp0
if "%OUTDIR%"=="" set OUTDIR=%ROOT%app
cd /d "%ROOT%src"
"%PY%" -m PyInstaller --noconfirm --clean --distpath "%ROOT%dist" --workpath "%ROOT%_build" --upx-dir "%ROOT%src\tools" abg.spec
if errorlevel 1 goto :fail
if not exist "%OUTDIR%" mkdir "%OUTDIR%"
rem v2.3.0 revision (QA P1-3): the default OUTDIR is %ROOT%app, which is where the
rem production guard runs from. A mapped exe cannot be deleted or replaced, but
rem *.dll/*.pyd/tcl/tk in the same folder can - so "just rebuild it" used to
rem leave a half-new, half-old install while still printing BUILD OK. Probe the
rem target exe for exclusive write and HARD-FAIL (never skip silently).
"%PY%" -c "import sys;sys.path.insert(0,r'%ROOT%installer');import shared_runtime;sys.exit(shared_runtime.outdir_busy(r'%OUTDIR%'))"
if errorlevel 1 goto :busy
rem xcopy is incremental: files removed from the spec would linger in %OUTDIR% and
rem get shipped in payload.zip. Purge previous build artifacts first; never
rem touch user data such as config.json / guard.log / caches.
del /Q "%OUTDIR%\*.dll" >nul 2>&1
del /Q "%OUTDIR%\*.pyd" >nul 2>&1
del /Q "%OUTDIR%\base_library.zip" >nul 2>&1
del /Q "%OUTDIR%\WBOX.exe" >nul 2>&1
rem v2.11.0 rename: purge the old-name exe left by pre-rename builds so it
rem cannot linger in OUTDIR and leak into payload.zip.
del /Q "%OUTDIR%\Wingman.exe" >nul 2>&1
for %%D in (tcl tk tcl8) do if exist "%OUTDIR%\%%D" rmdir /S /Q "%OUTDIR%\%%D" >nul 2>&1
xcopy "%ROOT%dist\WBOX\*" "%OUTDIR%\" /E /Y /Q >nul
if errorlevel 1 goto :fail
if not exist "%OUTDIR%\WBOX.exe" goto :fail
rem The build never produces config.json: generate the factory template (kernel
rem defaults + builtin plugin defaults). Never copy app\config.json here: that is
rem the live production config and would ship user machine paths in every payload.
"%PY%" "%ROOT%installer\factory_config.py" --out "%OUTDIR%\config.json"
if errorlevel 1 goto :fail
rem v2.3.0: compile docs\PLUGIN-SPEC.md into a single self-contained HTML and lay
rem the guide (HTML + Markdown + plugin template) into %OUTDIR%\docs\, so
rem build_setup.py picks it up for payload.zip. Kept as one python call because
rem this .bat must stay ASCII-only and the guide's filename is Chinese.
"%PY%" "%ROOT%installer\build_plugin_doc.py" --deploy "%OUTDIR%"
if errorlevel 1 goto :fail
if not exist "%OUTDIR%\docs" goto :fail
rem v2.3.0: a fresh OUTDIR also needs the two panel/tray icons. PyInstaller does
rem not produce them (they are design assets), so a scratch build used to end up
rem without them and the installer payload silently lost 7 KB of icons.
if not exist "%OUTDIR%\icon-light.ico" copy /Y "%ROOT%design\icon-light.ico" "%OUTDIR%\icon-light.ico" >nul 2>&1
if not exist "%OUTDIR%\icon-dark.ico"  copy /Y "%ROOT%design\icon-dark.ico"  "%OUTDIR%\icon-dark.ico"  >nul 2>&1
if not exist "%OUTDIR%\icon-light.ico" goto :fail
rem v2.1.1: PyInstaller writes base_library.zip as STORED (1.03 MB) for the
rem distribution path; the deployed on-disk copy is recompressed to ~468 KB.
rem build_setup.py re-stores it again when building payload.zip.
"%PY%" -c "import sys;sys.path.insert(0,r'%ROOT%installer');import shared_runtime;shared_runtime.compact_base_library(r'%OUTDIR%\base_library.zip')"
if errorlevel 1 goto :fail

rem v2.11.3: the product ships ZERO plugins. Plugins are a separate repo
rem (github.com/qinchangsheng123/wbox-plugins) and are installed from the panel
rem afterwards. Nothing is copied here, and nothing is deleted either: %OUTDIR%
rem may be the live install dir, where plugins\ holds the user's own plugins.
rem installer\build_setup.py excludes plugins\ from payload.zip and fails if any
rem plugin entry sneaks in, so "not bundled" is guaranteed at the packaging step
rem instead of by destroying user data at the build step.

rmdir /S /Q "%ROOT%dist" 2>nul
rmdir /S /Q "%ROOT%_build" 2>nul
echo BUILD OK  -^>  %OUTDIR%\WBOX.exe
echo Plugins     -^>  not bundled (install from the panel; see README)
echo Restart guard: powershell -NoProfile -Command "Stop-ScheduledTask -TaskName WBOX; Start-ScheduledTask -TaskName WBOX"
goto :eof
:busy
echo BUILD FAILED -^> %OUTDIR% is in use by a running WBOX guard.
echo   The deployed exe is locked, so the dll/pyd/tcl swap would produce a
echo   half-new, half-old install. Stop the task first, or build elsewhere:
echo     powershell -NoProfile -Command "Stop-ScheduledTask -TaskName WBOX"
echo     set OUTDIR=D:\06-Temp\wbox-build\app ^&^& build_exe.bat
exit /b 1
:fail
echo BUILD FAILED
exit /b 1
