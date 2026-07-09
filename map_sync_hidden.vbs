' 경공매 지도 좌표 동기화 배치를 콘솔 창 없이(hidden) 실행.
' 폴더 경로는 이 스크립트 위치에서 유도(한글 경로 하드코딩 안 함 → 이 파일은 순수 ASCII).
Dim fso, folder, sh
Set fso = CreateObject("Scripting.FileSystemObject")
folder = fso.GetParentFolderName(WScript.ScriptFullName)
Set sh = CreateObject("WScript.Shell")
sh.Run "cmd /c """ & folder & "\map_sync.bat""", 0, False
