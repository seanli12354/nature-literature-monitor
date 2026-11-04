@echo off
chcp 65001 >nul
cd /d "C:\Users\xli9\OneDrive - McGill University\Desktop\manu_12_TIM_APEN\literature2"
py -3 download_github_pdfs.py solar_cost
if errorlevel 1 (
  echo.
  echo Sync failed. Verify Python 3, network access, and GitHub public repository.
)
echo.
pause
