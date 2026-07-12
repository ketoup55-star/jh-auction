' Windowless launcher for the JH auction server (port 4011).
' wscript.exe has no console, and Run(..., 0, False) starts the server bat with a HIDDEN
' window, so the uvicorn console never stays on screen. Passes /hidden so the bat runs the
' server directly instead of relaunching again (prevents an infinite relaunch loop).
' The bat path is derived at runtime from this script's own folder, so the Korean folder
' name is never hardcoded here (this file stays pure ASCII).
Dim fso, folder, sh
Set fso = CreateObject("Scripting.FileSystemObject")
folder = fso.GetParentFolderName(WScript.ScriptFullName)
Set sh = CreateObject("WScript.Shell")
sh.Run "cmd /c """ & folder & "\run_server_4011.bat"" /hidden", 0, False
