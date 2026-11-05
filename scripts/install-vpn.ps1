# The VPN service for the desktop app on Windows. Run once in an administrator PowerShell:
#
#   powershell -ExecutionPolicy Bypass -File install-vpn.ps1 -Exe .\pi-guard-vpn.exe
#   powershell -ExecutionPolicy Bypass -File install-vpn.ps1 -Remove
#
# What this changes on this computer:
#   - installs the official WireGuard client if it is missing, after checking its signature
#   - copies pi-guard-vpn.exe to Program Files\PiGuard VPN and adds the service "PiGuardVPN"
#   - only the user who runs this may control the service
param([string]$Exe = "", [switch]$Remove)
$ErrorActionPreference = "Stop"

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { throw "Run this in an administrator PowerShell." }
$target = Join-Path $env:ProgramFiles "PiGuard VPN"
$installed = Join-Path $target "pi-guard-vpn.exe"

if ($Remove) {
    if (Test-Path $installed) { & $installed uninstall }
    Remove-Item -Recurse -Force $target -ErrorAction SilentlyContinue
    Write-Host "removed"
    exit 0
}

if (-not $Exe -or -not (Test-Path $Exe)) { throw "Give the program: -Exe .\pi-guard-vpn.exe" }

$wireguard = Join-Path $env:ProgramFiles "WireGuard\wireguard.exe"
if (-not (Test-Path $wireguard)) {
    $file = Join-Path $env:TEMP "wireguard-installer.exe"
    Invoke-WebRequest "https://download.wireguard.com/windows-client/wireguard-installer.exe" -OutFile $file
    $signature = Get-AuthenticodeSignature $file
    if ($signature.Status -ne "Valid" -or $signature.SignerCertificate.Subject -notmatch "WireGuard LLC") {
        Remove-Item $file
        throw "The WireGuard installer does not carry its signature. Stopped."
    }
    Start-Process $file -Wait
    Remove-Item $file
    if (-not (Test-Path $wireguard)) { throw "WireGuard was not installed." }
}

New-Item -ItemType Directory -Force $target | Out-Null
Copy-Item $Exe $installed -Force
$sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
& $installed install $sid
if ($LASTEXITCODE -ne 0) { throw "The service could not be installed." }
Write-Host "running. Open the VPN page in the desktop app."
