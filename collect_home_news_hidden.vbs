' Run the home-news collector with no console window (hidden).
' Folder derived from this script's own location (no Korean hardcoded -> pure ASCII).
Dim fso, folder, sh
Set fso = CreateObject("Scripting.FileSystemObject")
folder = fso.GetParentFolderName(WScript.ScriptFullName)
Set sh = CreateObject("WScript.Shell")
sh.Run "cmd /c """ & folder & "\collect_home_news.bat""", 0, False
