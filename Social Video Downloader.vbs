' Silent launcher for Social Video Downloader.
' Runs the app with pythonw so NO console/CMD window ever appears.
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = scriptDir
' 0 = hidden window, False = don't wait for it to close
shell.Run "pythonw.exe " & Chr(34) & scriptDir & "\app.py" & Chr(34), 0, False
