<#
.SYNOPSIS
  Quick "does it boot?" check: run a merged flash image in Espressif QEMU for a
  few seconds and save UART0 output next to the image (<image>.uart.log).
  The full boot investigation is spike step 2. This only proves that the
  step 1 images are bootable.

.EXAMPLE
  .\boot-smoke.ps1 -Image build-a-esp32-os\sketch_multi.ino.merged.bin -Chip esp32
#>
param(
  [Parameter(Mandatory)][string]$Image,
  [ValidateSet('esp32', 'esp32s3')][string]$Chip = 'esp32',
  [int]$Seconds = 8,
  # Extra QEMU arguments, e.g. '-icount','shift=auto'.
  [string[]]$QemuExtra = @(),
  # QEMU isn't on PATH on this machine (see tools-installed.txt item 6).
  [string]$QemuBin = 'C:\Espressif\tools\tools\qemu-xtensa\esp_develop_9.2.2_20260417\qemu\bin'
)
$ErrorActionPreference = 'Stop'
$img = Get-Item (Resolve-Path $Image)
$log = "$($img.BaseName).uart.log"
$qemu = Join-Path $QemuBin 'qemu-system-xtensa.exe'

# Relative file names + WorkingDirectory avoid quoting drive-letter paths for QEMU.
$qemuArgs = @('-machine', $Chip, '-display', 'none', '-monitor', 'none',
  '-serial', "file:$log", '-drive', "file=$($img.Name),if=mtd,format=raw") + $QemuExtra
$proc = Start-Process -FilePath $qemu -ArgumentList $qemuArgs -WorkingDirectory $img.DirectoryName -PassThru -NoNewWindow
$exitedEarly = $proc.WaitForExit($Seconds * 1000)
if (-not $exitedEarly) { Stop-Process -Id $proc.Id -Force }

$logPath = Join-Path $img.DirectoryName $log
Write-Host "QEMU $(if ($exitedEarly) { "EXITED EARLY (code $($proc.ExitCode))" } else { "ran ${Seconds}s, stopped" }); UART log: $logPath"
if (Test-Path $logPath) { Get-Content $logPath -Tail 25 }
