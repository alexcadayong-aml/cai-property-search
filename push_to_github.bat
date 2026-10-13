@echo off
title Push Property Search to GitHub
cd /d "C:\Users\mcjld\OneDrive\Desktop\Antigravity"
echo =======================================================
echo Pushing Property Search App to GitHub...
echo If prompted, please click "Sign in with your browser".
echo =======================================================
echo.
git push -u origin main
echo.
if %ERRORLEVEL% EQU 0 (
    echo =======================================================
    echo SUCCESS! Your code and database have been pushed to GitHub!
    echo Now go back to Render and click "Deploy web service".
    echo =======================================================
) else (
    echo.
    echo If sign-in was canceled, run this file again to retry.
)
echo.
pause
