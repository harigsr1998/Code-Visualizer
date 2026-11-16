<#
.SYNOPSIS
  Spike step 1 (Build): build the same multi-file Arduino sketch two ways.

  Path A: arduino-cli + Arduino-ESP32 core 3.3.12 (uses precompiled ESP-IDF libs)
  Path B: ESP-IDF v5.5.5 + arduino-esp32 3.3.12 as a managed component (all from source)

  Every build ends with a 4 MB merged flash image that QEMU can boot.
  Output folders are build-<path>-<chip>-<opt>/ (git-ignored).

.EXAMPLE
  .\build.ps1                                # all 8 builds
  .\build.ps1 -Path A -Chip esp32 -Opt Og    # just one
  .\build.ps1 -Clean                         # delete the output folders first
#>
param(
  [ValidateSet('A', 'B')][string[]]$Path = @('A', 'B'),
  [ValidateSet('esp32', 'esp32s3')][string[]]$Chip = @('esp32', 'esp32s3'),
  [ValidateSet('Os', 'Og')][string[]]$Opt = @('Os', 'Og'),
  [switch]$Clean,
  # Path A only. The Arduino board default is QIO, but Espressif QEMU can't set
  # the flash chip's QE bit, so a QIO build asserts in flash init under QEMU.
  # Real boards usually run QIO. See spike/NOTES.md (step 1) for the implication.
  [ValidateSet('qio', 'dio')][string]$FlashMode = 'dio',
  # Spike-only assumption: EIM's default install location.
  [string]$IdfProfile = 'C:\Espressif\tools\Microsoft.v5.5.5.PowerShell_profile.ps1'
)
$ErrorActionPreference = 'Stop'
$here = $PSScriptRoot
$sketch = Join-Path $here 'sketch_multi'
$idfProj = Join-Path $here 'idf_arduino'

function Use-Idf {
  if (-not $env:IDF_PATH) { . $IdfProfile | Out-Null }
}

$results = foreach ($p in $Path) {
  foreach ($c in $Chip) {
    foreach ($o in $Opt) {
      $name = 'build-{0}-{1}-{2}' -f $p.ToLower(), $c, $o.ToLower()
      $out = Join-Path $here $name
      if ($Clean -and (Test-Path $out)) { Remove-Item -Recurse -Force $out }
      Write-Host "`n===== $name =====" -ForegroundColor Cyan
      $sw = [Diagnostics.Stopwatch]::StartNew()

      if ($p -eq 'A') {
        $cliArgs = @('compile', '--fqbn', "esp32:esp32:$c", '--board-options', "FlashMode=$FlashMode", '--build-path', $out)
        if ($o -eq 'Og') { $cliArgs += '--optimize-for-debug' }
        & arduino-cli @cliArgs $sketch
        $ok = ($LASTEXITCODE -eq 0)
        $app = Join-Path $out 'sketch_multi.ino.bin'
        $image = Join-Path $out 'sketch_multi.ino.merged.bin'
      }
      else {
        Use-Idf
        $defaults = "sdkconfig.defaults;sdkconfig.defaults.$($o.ToLower())"
        & idf.py -C $idfProj -B $out "-DIDF_TARGET=$c" "-DSDKCONFIG=$out\sdkconfig" "-DSDKCONFIG_DEFAULTS=$defaults" build
        $ok = ($LASTEXITCODE -eq 0)
        $app = Join-Path $out 'spike_idf_arduino.bin'
        $image = Join-Path $out 'flash_merged.bin'
        if ($ok) {
          # flash_args (written by the build) lists every binary and its offset.
          Push-Location $out
          try {
            & python -m esptool --chip $c merge_bin --fill-flash-size 4MB -o flash_merged.bin '@flash_args'
            $ok = ($LASTEXITCODE -eq 0)
          }
          finally { Pop-Location }
        }
      }

      $sw.Stop()
      [pscustomobject]@{
        Build     = $name
        Ok        = $ok
        Seconds   = [math]::Round($sw.Elapsed.TotalSeconds, 1)
        AppBytes  = if (Test-Path $app) { (Get-Item $app).Length } else { $null }
        ImageMB   = if (Test-Path $image) { [math]::Round((Get-Item $image).Length / 1MB, 2) } else { $null }
        Image     = if (Test-Path $image) { $image.Substring($here.Length + 1) } else { '' }
      }
    }
  }
}

Write-Host "`n===== Summary =====" -ForegroundColor Cyan
$results | Format-Table -AutoSize | Out-String -Width 200 | Write-Host
if ($results.Ok -contains $false) { exit 1 }
