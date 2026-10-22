@echo off
setlocal
cd /d "%~dp0"
echo.
echo =====================================================
echo   BINANCE FUTURES RESEARCH DESK - PAPER TESTING ONLY
echo =====================================================
echo.
echo Checking and installing Python packages...
py -m pip install -r requirements.txt
if errorlevel 1 goto error
echo.
echo Starting the dashboard in your browser...
py -m streamlit run app.py
if errorlevel 1 goto error
goto end
:error
echo.
echo Setup or launch failed. Please check README.md.
pause
:end
endlocal
