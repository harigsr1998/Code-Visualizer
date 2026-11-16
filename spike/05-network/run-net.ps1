<#
.SYNOPSIS
  Spike step 5: boot a merged flash image in Espressif QEMU with an emulated
  Ethernet NIC (open_eth) and capture UART0 to a log file.

  App zone (host-side tooling). By default it uses QEMU user-mode networking
  (libslirp NAT) with the given host port forwards.

.PARAMETER HostFwd
  slirp port forwards, e.g. 'tcp:127.0.0.1:18070-:3333'. Bind to 127.0.0.1
  unless you mean to expose the port to the LAN (see NOTES.md, question 4).
.PARAMETER Netdev
  Replaces the whole -nic/-netdev part, e.g.
  '-netdev','stream,id=n0,server=off,addr.type=inet,addr.host=127.0.0.1,addr.port=18079','-net','nic,model=open_eth,netdev=n0'
.PARAMETER Seconds
  0 = return immediately and leave QEMU running (the process object is returned).

.EXAMPLE
  .\run-net.ps1 -Image build-http_request-esp32\flash_merged.bin -Chip esp32 -Seconds 40
#>
param(
  [Parameter(Mandatory)][string]$Image,
  [ValidateSet('esp32', 'esp32s3')][string]$Chip = 'esp32',
  [int]$Seconds = 30,
  [string[]]$HostFwd = @(),
  [string[]]$Netdev = @(),
  [switch]$Icount,
  [string]$Log = '',
  [string]$QemuBin = 'C:\Espressif\tools\tools\qemu-xtensa\esp_develop_9.2.2_20260417\qemu\bin'
)
$ErrorActionPreference = 'Stop'
$img = Get-Item (Resolve-Path $Image)
if (-not $Log) { $Log = "$($img.BaseName).uart.log" }
$qemu = Join-Path $QemuBin 'qemu-system-xtensa.exe'

if ($Netdev.Count -eq 0) {
  $nic = 'user,model=open_eth'
  foreach ($f in $HostFwd) { $nic += ",hostfwd=$f" }
  $Netdev = @('-nic', $nic)
}
$qemuArgs = @('-machine', $Chip, '-display', 'none', '-monitor', 'none',
  '-serial', "file:$Log", '-drive', "file=$($img.Name),if=mtd,format=raw") + $Netdev
if ($Icount) { $qemuArgs += @('-icount', 'shift=auto') }
Write-Host "qemu $($qemuArgs -join ' ')"
$proc = Start-Process -FilePath $qemu -ArgumentList $qemuArgs -WorkingDirectory $img.DirectoryName -PassThru -NoNewWindow
if ($Seconds -eq 0) { return $proc }
$exitedEarly = $proc.WaitForExit($Seconds * 1000)
if (-not $exitedEarly) { Stop-Process -Id $proc.Id -Force }
$logPath = Join-Path $img.DirectoryName $Log
Write-Host "QEMU $(if ($exitedEarly) { "EXITED EARLY (code $($proc.ExitCode))" } else { "ran ${Seconds}s, stopped" }); UART log: $logPath"
