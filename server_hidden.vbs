' Windowless launcher for the JH auction server (port 4011).
'
' 2026-10-01 (owner: "CMD 창을 띄우지 말라고 아예 백그라운드를 하면 되잖아"):
'   The old line ran `cmd /c run_server_4011.bat /hidden`. wscript has no console and
'   Run(..., 0, False) asks for a HIDDEN window, but this PC's default console host is
'   Windows Terminal, and Terminal ignores that hidden flag -> a black window appeared.
'   Now we skip cmd entirely and run PYTHONW, which never allocates a console at all,
'   so no terminal setting can make a window appear. server_launch.py does what the bat
'   did (env vars, stale-listener cleanup, log rotation, log redirect, death record).
'   run_server_4011.bat is kept untouched as a manual fallback.
Dim fso, folder, sh, pyw
Set fso = CreateObject("Scripting.FileSystemObject")
folder = fso.GetParentFolderName(WScript.ScriptFullName)
pyw = "C:\Users\red85\AppData\Local\Python\pythoncore-3.14-64\pythonw.exe"
Set sh = CreateObject("WScript.Shell")
If fso.FileExists(pyw) Then
    sh.Run """" & pyw & """ """ & folder & "\server_launch.py""", 0, False
Else
    ' fallback: old path (a window may appear, but the server still starts)
    sh.Run "cmd /c """ & folder & "\run_server_4011.bat"" /hidden", 0, False
End If
