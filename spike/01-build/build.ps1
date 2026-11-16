<#
.SYNOPSIS
  Spike build engine: build an Arduino sketch folder two ways.

  Path A: arduino-cli + Arduino-ESP32 core 3.3.12 (uses precompiled ESP-IDF libs)
  Path B: ESP-IDF v5.5.5 + arduino-esp32 3.3.12 as a managed component (all from source)

  Every build ends with a 4 MB merged flash image that QEMU can boot.
  Output folders are build-<path>-<chip>-<opt>[-<sketch>]/ next to the sketch
  folder's parent (git-ignored). The -<sketch> suffix is left out for the
  step 1 sketch (sketch_multi).

.EXAMPLE
  .\build.ps1                                       # step 1: all 8 builds of sketch_multi
  .\build.ps1 -Path A -Chip esp32 -Opt Og           # just one
  .\build.ps1 -SketchDir ..\02-boot\probe -Opt Og -DebugLevel warn
  .\build.ps1 -Clean                                # delete the output folders first
#>
param(
  [ValidateSet('A', 'B')][string[]]$Path = @('A', 'B'),
  [ValidateSet('esp32', 'esp32s3')][string[]]$Chip = @('esp32', 'esp32s3'),
  [ValidateSet('Os', 'Og')][string[]]$Opt = @('Os', 'Og'),
  [string]$SketchDir = (Join-Path $PSScriptRoot 'sketch_multi'),
  # Path A only: the board menu's "Core Debug Level". Path B sets its level in sdkconfig.defaults.
  [ValidateSet('none', 'error', 'warn', 'info', 'debug', 'verbose')][string]$DebugLevel = 'none',
  # Path B only: extra sdkconfig.defaults files (e.g. one that turns on CONFIG_ETH_USE_OPENETH),
  # applied after the shared ones. Add a -BuildTag so differently-configured builds don't share a folder.
  [string[]]$ExtraDefaults = @(),
  # Appended to the output folder name. (Not -Suffix: PowerShell names are case-insensitive,
  # so it would clash with the internal $suffix variable.)
  [string]$BuildTag = '',
  [switch]$Clean,
  # Path A only. The Arduino board default is QIO, but Espressif QEMU can't set
  # the flash chip's QE bit, so a QIO build asserts in flash init under QEMU.
  # Real boards usually run QIO. See spike/NOTES.md (step 1) for the implication.
  [ValidateSet('qio', 'dio')][string]$FlashMode = 'dio',
  # Path A only: extra Arduino board-menu options, e.g. 'CPUFreq=160'. Combine with -BuildTag.
  [string[]]$BoardOptions = @(),
  # Spike-only assumption: EIM's default install location.
  [string]$IdfProfile = 'C:\Espressif\tools\Microsoft.v5.5.5.PowerShell_profile.ps1'
)
$ErrorActionPreference = 'Stop'
$here = $PSScriptRoot
$sketch = (Resolve-Path $SketchDir).Path
$sketchName = Split-Path $sketch -Leaf
$outRoot = Split-Path $sketch -Parent
$suffix = if ($sketchName -eq 'sketch_multi') { '' } else { "-$sketchName" }
if ($BuildTag) { $suffix += "-$BuildTag" }
$idfProj = Join-Path $here 'idf_arduino'

function Use-Idf {
  if (-not $env:IDF_PATH) { . $IdfProfile | Out-Null }
}

$results = foreach ($p in $Path) {
  foreach ($c in $Chip) {
    foreach ($o in $Opt) {
      $name = 'build-{0}-{1}-{2}{3}' -f $p.ToLower(), $c, $o.ToLower(), $suffix
      $out = Join-Path $outRoot $name
      if ($Clean -and (Test-Path $out)) { Remove-Item -Recurse -Force $out }
      Write-Host "`n===== $name =====" -ForegroundColor Cyan
      $sw = [Diagnostics.Stopwatch]::StartNew()

      if ($p -eq 'A') {
        $cliArgs = @('compile', '--fqbn', "esp32:esp32:$c",
          '--board-options', "FlashMode=$FlashMode", '--board-options', "DebugLevel=$DebugLevel",
          '--build-path', $out)
        foreach ($bo in $BoardOptions) { $cliArgs += @('--board-options', $bo) }
        if ($o -eq 'Og') { $cliArgs += '--optimize-for-debug' }
        & arduino-cli @cliArgs $sketch
        $ok = ($LASTEXITCODE -eq 0)
        $app = Join-Path $out "$sketchName.ino.bin"
        $image = Join-Path $out "$sketchName.ino.merged.bin"
      }
      else {
        Use-Idf
        $defaults = (@('sdkconfig.defaults', "sdkconfig.defaults.$($o.ToLower())") + ($ExtraDefaults | ForEach-Object { (Resolve-Path $_).Path })) -join ';'
        & idf.py -C $idfProj -B $out "-DIDF_TARGET=$c" "-DSDKCONFIG=$out\sdkconfig" "-DSDKCONFIG_DEFAULTS=$defaults" "-DSPIKE_SKETCH_DIR=$sketch" build
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
        Build    = $name
        Ok       = $ok
        Seconds  = [math]::Round($sw.Elapsed.TotalSeconds, 1)
        AppBytes = if (Test-Path $app) { (Get-Item $app).Length } else { $null }
        ImageMB  = if (Test-Path $image) { [math]::Round((Get-Item $image).Length / 1MB, 2) } else { $null }
        Image    = if (Test-Path $image) { $image.Substring($outRoot.Length + 1) } else { '' }
      }
    }
  }
}

Write-Host "`n===== Summary =====" -ForegroundColor Cyan
$results | Format-Table -AutoSize | Out-String -Width 200 | Write-Host
if ($results.Ok -contains $false) { exit 1 }
