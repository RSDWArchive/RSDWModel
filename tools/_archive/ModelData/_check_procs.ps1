Get-CimInstance Win32_Process -Filter "Name='blender.exe'" |
    Select-Object ProcessId, ParentProcessId, CreationDate,
        @{Name='CPU_s'; Expression={(Get-Process -Id $_.ProcessId -ErrorAction SilentlyContinue).CPU}} |
    Format-Table -AutoSize

Write-Host '---'

Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -match 'BuildGLB' } |
    Select-Object ProcessId, ParentProcessId, CreationDate, CommandLine |
    Format-Table -Wrap
