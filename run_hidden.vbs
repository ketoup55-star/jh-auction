' run_hidden.vbs - run any command with NO console window (Task Scheduler window fix, 2026-09-17)
' Usage (task action): wscript.exe //B //Nologo "<this file>" [/wd:<workdir>] <exe> [args...]
'   - every argument that contains a space is re-quoted, so paths with spaces are safe
'   - /wd:<dir> (optional, first arg) sets the working directory; default = this file's folder
' Why: python.exe / powershell.exe / .bat started directly by Task Scheduler open a console
' window on the interactive desktop (owner saw cmd/powershell/python windows pop up).
' WshShell.Run(cmd, 0, False) starts the process with a hidden window and returns at once.
Option Explicit
Dim sh, fso, folder, cmd, a, i, wd
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
folder = fso.GetParentFolderName(WScript.ScriptFullName)
wd = folder
cmd = ""
For i = 0 To WScript.Arguments.Count - 1
  a = WScript.Arguments(i)
  If i = 0 And LCase(Left(a, 4)) = "/wd:" Then
    wd = Mid(a, 5)
  Else
    If InStr(a, " ") > 0 And Left(a, 1) <> """" Then a = """" & a & """"
    cmd = cmd & a & " "
  End If
Next
If Len(Trim(cmd)) = 0 Then WScript.Quit 2
On Error Resume Next
sh.CurrentDirectory = wd
On Error GoTo 0
sh.Run Trim(cmd), 0, False
