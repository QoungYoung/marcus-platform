@echo off
echo ============================================
echo   Marcus DB SSH Tunnel
echo   本地 5432 ^<-^> 远程服务器 5432
echo   关闭此窗口即断开连接
echo ============================================
ssh -L 5432:localhost:5432 root@81.70.44.68 -N
pause
