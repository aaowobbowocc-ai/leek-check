' run_hidden.vbs — 透明執行 .bat 不彈視窗
' Usage in Scheduled Task:
'   Program:  wscript.exe
'   Arguments: "C:\Users\USER\Desktop\INVEST\scripts\run_hidden.vbs" "C:\path\to\xxx.bat"

If WScript.Arguments.Count < 1 Then
    WScript.Quit 1
End If

Set objShell = CreateObject("Wscript.Shell")
objShell.Run "cmd /c """ & WScript.Arguments(0) & """", 0, True
