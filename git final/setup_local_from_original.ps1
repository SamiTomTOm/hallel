$ErrorActionPreference = 'Stop'
$Target = Split-Path -Parent $MyInvocation.MyCommand.Path
$Original = Join-Path (Split-Path -Parent $Target) 'hallel_inventory_system'
$SourceDb = Join-Path $Original 'inventory.db'
$SourceEnv = Join-Path $Original '.env'
$TargetDb = Join-Path $Target 'inventory.db'
$TargetEnv = Join-Path $Target '.env'

Write-Host 'Hallel Inventory local setup' -ForegroundColor Green
Write-Host "Target:   $Target"
Write-Host "Original: $Original"

if (!(Test-Path $SourceDb)) { throw "Original database not found: $SourceDb. Keep your original project folder in its current location." }
if (!(Test-Path $SourceEnv)) { throw "Original .env not found: $SourceEnv. No files were changed." }

# Preserve any existing target database before replacing it.
if (Test-Path $TargetDb) {
    $stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
    $backup = Join-Path $Target ("inventory_before_local_setup_$stamp.db")
    Copy-Item $TargetDb $backup
    Write-Host "Existing target database backed up to: $backup" -ForegroundColor Yellow
}

# Copy source data and private local configuration; neither is included in this ZIP.
Copy-Item $SourceDb $TargetDb -Force
Copy-Item $SourceEnv $TargetEnv -Force

# Ensure this local copy uses its own portable SQLite file, not a stale external DATABASE_URL.
$lines = Get-Content $TargetEnv | Where-Object { $_ -notmatch '^\s*DATABASE_URL\s*=' }
$lines += 'DATABASE_URL=sqlite:///./inventory.db'
Set-Content -Path $TargetEnv -Value $lines -Encoding utf8

Write-Host 'Local data and configuration copied successfully.' -ForegroundColor Green
Write-Host 'Next: install dependencies, then start the backend from this folder.'
Write-Host 'Command: python -m uvicorn main:app --reload'
Write-Host 'Do not commit .env, inventory.db, or backups to GitHub.' -ForegroundColor Yellow
