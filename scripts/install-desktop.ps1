# Installs the PiGuard desktop app on Windows for the current user.
#
#   powershell -ExecutionPolicy Bypass -File .\scripts\install-desktop.ps1
#   powershell -ExecutionPolicy Bypass -File .\scripts\install-desktop.ps1 -Installer .\PiGuard-1.1.0-win-x64.exe
#   powershell -ExecutionPolicy Bypass -File .\scripts\install-desktop.ps1 -Url https://.../PiGuard-1.1.0-win-x64.exe
#
# With no argument it downloads the newest apps-* release from GitHub. After install the app
# updates itself from your Pi. Windows may warn because the file is not code-signed: More info, Run anyway.
param(
    [string]$Installer = "",
    [string]$Url = "",
    [string]$Repo = "lytexdev/pi-guard"
)
$manifestUrl = ""

$ErrorActionPreference = "Stop"
$temp = Join-Path $env:TEMP ("pi-guard-desktop-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $temp | Out-Null
try {
    if (-not $Installer -and -not $Url) {
        Write-Host "Looking up the newest apps release…"
        $releases = Invoke-RestMethod -Uri "https://api.github.com/repos/$Repo/releases"
        foreach ($release in $releases) {
            if (-not $release.tag_name.StartsWith("apps-")) { continue }
            foreach ($asset in $release.assets) {
                if ($asset.name -like "PiGuard-*-win-x64.exe") {
                    $Url = $asset.browser_download_url
                    $name = $asset.name
                }
                if ($asset.name -eq "desktop-win-x64.json") { $manifestUrl = $asset.browser_download_url }
            }
            if ($Url) { break }
        }
        if (-not $Url) { throw "no PiGuard Windows installer in apps-* releases" }
        $Installer = Join-Path $temp $name
        Write-Host "Downloading $name …"
        Invoke-WebRequest -Uri $Url -OutFile $Installer
        # Windows PowerShell has no Ed25519, so only the checksum from the signed manifest is
        # compared here; the installed app checks the full signature of every later update.
        if (-not $manifestUrl) { throw "the release has no manifest for win-x64: not installing" }
        $manifest = Invoke-RestMethod -Uri $manifestUrl
        $sum = (Get-FileHash -Algorithm SHA256 -LiteralPath $Installer).Hash.ToLower()
        if ($sum -ne $manifest.sha256) { throw "the download does not match its manifest: not installing" }
    } elseif ($Url) {
        $name = [IO.Path]::GetFileName(($Url -split "\?")[0])
        $Installer = Join-Path $temp $name
        Write-Host "Downloading $name …"
        Invoke-WebRequest -Uri $Url -OutFile $Installer
    }

    if (-not (Test-Path -LiteralPath $Installer)) { throw "installer not found: $Installer" }
    Write-Host "Starting installer…"
    # NSIS: per-user, no silent unless the person asks; /S would skip the wizard.
    Start-Process -FilePath $Installer -Wait
    Write-Host "Done. Start PiGuard from the Start menu. Later versions update from your Pi."
}
finally {
    Remove-Item -Recurse -Force $temp -ErrorAction SilentlyContinue
}
