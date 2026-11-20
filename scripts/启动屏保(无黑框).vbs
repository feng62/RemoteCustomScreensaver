Set WshShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
rootDir = fso.GetParentFolderName(scriptDir)
WshShell.CurrentDirectory = rootDir
WshShell.Run "pythonw.exe """ & rootDir & "\src\app.py""", 0, False
