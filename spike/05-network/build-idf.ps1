<#
.SYNOPSIS
  Spike step 5: build a plain ESP-IDF project for QEMU's emulated Ethernet
  (open_eth) and produce a 4 MB merged flash image QEMU can boot.

  Output: build-<project>-<chip>[-<tag>]/flash_merged.bin next to this script (git-ignored).
  Run it through the heavy-job lock (parallel/RULES.md rule 3).

.EXAMPLE
  & D:\Coding\Code_Visualizer\Claude_CLI\parallel\run-heavy.ps1 -Who network -What 'idf build http_request esp32' -Job {
      & .\build-idf.ps1 -Project idf_http_request -Chip esp32
  }
#>
param(
  [Parameter(Mandatory)][string]$Project,
  [ValidateSet('esp32', 'esp32s3')][string[]]$Chip = @('esp32'),
  # Extra sdkconfig.defaults files, applied after sdkconfig.qemu_eth.
  [string[]]$ExtraDefaults = @(),
  [string]$BuildTag = '',
  [switch]$Clean,
  # Spike-only assumption: EIM's default install location.
  [string]$IdfProfile = 'C:\Espressif\tools\Microsoft.v5.5.5.PowerShell_profile.ps1'
)
$ErrorActionPreference = 'Stop'
$here = $PSScriptRoot
$proj = (Resolve-Path (Join-Path $here $Project)).Path
$projName = Split-Path $proj -Leaf
if (-not $env:IDF_PATH) { . $IdfProfile | Out-Null }

$results = foreach ($c in $Chip) {
  $name = "build-$($projName -replace '^idf_', '')-$c" + $(if ($BuildTag) { "-$BuildTag" } else { '' })
  $out = Join-Path $here $name
  if ($Clean -and (Test-Path $out)) { Remove-Item -Recurse -Force $out }
  Write-Host "`n===== $name =====" -ForegroundColor Cyan
  $sw = [Diagnostics.Stopwatch]::StartNew()
  $defaults = (@((Join-Path $here 'sdkconfig.qemu_eth')) + ($ExtraDefaults | ForEach-Object { (Resolve-Path $_).Path })) -join ';'
  & idf.py -C $proj -B $out "-DIDF_TARGET=$c" "-DSDKCONFIG=$out\sdkconfig" "-DSDKCONFIG_DEFAULTS=$defaults" build
  $ok = ($LASTEXITCODE -eq 0)
  if ($ok) {
    Push-Location $out
    try {
      & python -m esptool --chip $c merge_bin --fill-flash-size 4MB -o flash_merged.bin '@flash_args'
      $ok = ($LASTEXITCODE -eq 0)
    }
    finally { Pop-Location }
  }
  $sw.Stop()
  [pscustomobject]@{ Build = $name; Ok = $ok; Seconds = [math]::Round($sw.Elapsed.TotalSeconds, 1)
    Image = if (Test-Path "$out\flash_merged.bin") { "$name\flash_merged.bin" } else { '' } }
}
$results | Format-Table -AutoSize | Out-String -Width 200 | Write-Host
if ($results.Ok -contains $false) { exit 1 }
