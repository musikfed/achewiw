Option Explicit

' ACHEWIW Graphics Bridge / experimental CorelDRAW 2026 macro.
' Run ONLY in a throwaway document explicitly saved with ACHEWIW_SANDBOX
' in its filename. It never saves the document and never overwrites any file.
' Vectorizes a *selected bitmap* using CorelDRAW PowerTRACE technical mode.
' It does not recover semantic editable rectangles, connectors or text.
' The CorelDRAW 2026 version of this macro has NOT yet been run on Windows.

Public Sub AchewiwTraceSelectedBitmap()
    On Error GoTo Failed

    If ActiveDocument Is Nothing Then
        MsgBox "Open a disposable ACHEWIW_SANDBOX document first.", vbExclamation
        Exit Sub
    End If

    If InStr(1, ActiveDocument.FileName, "ACHEWIW_SANDBOX", vbTextCompare) = 0 Then
        MsgBox "Safety stop: save a COPY with ACHEWIW_SANDBOX in its filename.", vbExclamation
        Exit Sub
    End If

    If ActiveSelectionRange.Count <> 1 Then
        MsgBox "Select exactly one bitmap on the sandbox page.", vbExclamation
        Exit Sub
    End If

    Dim imageShape As Shape
    Set imageShape = ActiveSelectionRange(1)

    If imageShape.Type <> cdrBitmapShape Then
        MsgBox "Selected object is not a bitmap.", vbExclamation
        Exit Sub
    End If

    Dim trace As TraceSettings
    Set trace = imageShape.Bitmap.Trace(cdrTraceTechnical)

    Dim vectors As ShapeRange
    Set vectors = trace.Finish()

    MsgBox "PowerTRACE generated " & CStr(vectors.Count) & _
           " vector shapes in the SANDBOX. Review before any SVG export." & vbCrLf & _
           "No file was saved by this macro.", vbInformation
    Exit Sub

Failed:
    MsgBox "CorelDRAW PowerTRACE failed: " & Err.Number & " - " & Err.Description, vbCritical
End Sub
