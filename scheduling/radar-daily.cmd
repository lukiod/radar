@echo off
REM radar: build today's outreach queue and send it.
REM
REM The Windows half of scheduling/radar-daily.service. There is no systemd
REM here, so without this the schedule simply does not exist and the machine
REM looks idle rather than broken.
REM
REM The repository is found from this file's own location, so moving the checkout
REM does not silently point the run at nothing.

setlocal

set "HERE=%~dp0"
set "RADAR=%HERE%.."
set "LOG=%HERE%..\..\internal-docs\comms\outreach-state\daily.log"

REM The cap is the base rate for one mailbox, and it is set here for the same
REM reason the unit sets it: two numbers in force depending on how the run was
REM launched is a difference nobody finds by reading either one.
set "RADAR_DAILY_CAP=100"

REM The py launcher ships with the python.org installer; python is the fallback.
set "PY=py -3"
where py >nul 2>&1 || set "PY=python"

if not exist "%HERE%..\..\internal-docs\comms\outreach-state" mkdir "%HERE%..\..\internal-docs\comms\outreach-state" 2>nul

cd /d "%RADAR%" || exit /b 1

echo. >> "%LOG%"
echo ==== run started %DATE% %TIME% ==== >> "%LOG%"
REM Unquoted on purpose: "py -3" in quotes is one program named py -3, not the
REM launcher with an argument. Neither form has a space in it.
%PY% tools\daily.py --cap 100 --pace 45 >> "%LOG%" 2>&1

REM The exit code is passed on so Task Scheduler's history shows a failure as a
REM failure. A run that dies at its first import has to be visible somewhere.
exit /b %ERRORLEVEL%
