#requires -Version 5.1
<#
.SYNOPSIS
 Read-only discovery of installed CorelDRAW and Photoshop on a local Windows PC.
 Does not launch either editor, install dependencies, open ports, execute macros,
 contact Linux, or change license/registry. Prints JSON for future bridge setup.
.EXAMPLE
 powershell.exe -NoProfile -File .\windows\graphics-bridge\Probe-GraphicsEditors.ps1
#>
[CmdletBinding()]
param()
$ErrorActionPreference = "Stop"

function Get-InstalledApplications {
    $roots = @(
        "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*",
        "HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*",
        "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*"
    )
    $apps = foreach ($root in $roots) {
        Get-ItemProperty -Path $root -ErrorAction SilentlyContinue |
            Where-Object { $_.DisplayName -match "(?i)(CorelDRAW|Photoshop)" } |
            ForEach-Object {
                [pscustomobject]@{
                    name = [string]$_.DisplayName
                    version = [string]$_.DisplayVersion
                    publisher = [string]$_.Publisher
                    install_path = [string]$_.InstallLocation
                }
            }
    }
    @($apps | Sort-Object name, version -Unique)
}

function Find-ProgId([string] $prefix) {
    $registryRoot = [Microsoft.Win32.Registry]::ClassesRoot
    @($registryRoot.GetSubKeyNames() |
        Where-Object { $_ -like ($prefix + "*") } |
        Sort-Object)
}

$apps = @(Get-InstalledApplications)
$corelCom = @(Find-ProgId "CorelDRAW.Application")
$photoshopCom = @(Find-ProgId "Photoshop.Application")
$result = [ordered]@{
    schema = "achewiw.graphics-bridge.windows-probe.v1"
    machine_name = $env:COMPUTERNAME
    os = [Environment]::OSVersion.VersionString
    powershell = $PSVersionTable.PSVersion.ToString()
    apps = $apps
    coreldraw_com_progids = $corelCom
    photoshop_com_progids = $photoshopCom
    notes = @(
        "Read-only discovery; COM registry entries do not prove automation will work.",
        "Photoshop 2026 UXP availability needs separate local interactive validation.",
        "No applications started, no jobs submitted, no network connections."
    )
}
$result | ConvertTo-Json -Depth 6

if ($apps.Count -eq 0 -and $corelCom.Count -eq 0 -and $photoshopCom.Count -eq 0) {
    Write-Warning "Application registry entries not found; this is not proof editors are absent."
}
