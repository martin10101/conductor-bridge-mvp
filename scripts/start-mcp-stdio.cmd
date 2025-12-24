@echo off
setlocal
set LOGFILE=C:\conductor-bridge-mvp\logs\mcp-start.log
if not exist C:\conductor-bridge-mvp\logs mkdir C:\conductor-bridge-mvp\logs >NUL 2>&1
echo %date% %time% starting conductor-bridge stdio>> "%LOGFILE%"
echo CONDUCTOR_BRIDGE_STATE_DIR=%CONDUCTOR_BRIDGE_STATE_DIR%>> "%LOGFILE%"
echo CONDUCTOR_BRIDGE_GEMINI_MODEL=%CONDUCTOR_BRIDGE_GEMINI_MODEL%>> "%LOGFILE%"
echo CONDUCTOR_BRIDGE_STDIO_LOG=%CONDUCTOR_BRIDGE_STDIO_LOG%>> "%LOGFILE%"

"C:\conductor-bridge-mvp\.venv\Scripts\python.exe" -m conductor_bridge.server --stdio 2>> "%LOGFILE%"
