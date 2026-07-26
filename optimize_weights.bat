@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo.
echo ========================================
echo   Weight Optimization - Sector Leaderboard
echo ========================================
echo.
echo Params: 30000 iterations, 8 workers, target=day5_pct, all-market + per-regime
echo.

python optimize_weights.py --input data/scores_v2.csv --target day5_pct --iterations 30000 --workers 8

echo.
echo ========================================
echo   Done! Results saved to data/weights_result_*.csv
echo ========================================
pause
