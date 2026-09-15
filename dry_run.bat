@echo off
rem 干跑检查：只判定「会不会同步存档」，不写任何文件、不启动任何程序
rem 会保留窗口显示结果（关掉窗口即结束）
cd /d "%~dp0"
python "%~dp0spt_solostart.py" --dry-run
echo.
echo ---- 干跑结束，按任意键关闭 ----
pause >nul
