Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
root = fso.GetParentFolderName(fso.GetParentFolderName(WScript.ScriptFullName))
pythonw = root & "\.venv\Scripts\pythonw.exe"
If Not fso.FileExists(pythonw) Then
    MsgBox "Echo virtual environment not found. Run the setup steps in README.md first.", 48, "Echo"
Else
    shell.CurrentDirectory = root
    shell.Run """" & pythonw & """ -m echo", 0, False
End If
