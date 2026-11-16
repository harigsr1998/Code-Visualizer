<#
.SYNOPSIS
  Spike step 5: one net_probe session. Starts the host test web server
  (127.0.0.1:18071), boots net_probe in QEMU with host port forwards bound to
  127.0.0.1, waits for the chip's servers, runs host_bench.py, then stops everything.
  App zone. Run it inside the heavy-job lock when -Bench is used for timing numbers.

  Forwards: 18070->3333 echo, 18072->3334 sink, 18073->3335 source.
#>
param(
  [ValidateSet('esp32', 'esp32s3')][string]$Chip = 'esp32',
  [switch]$Icount,
  [string]$BenchArgs = '-n 20 --upload-bytes 262144 --only echo',
  [string]$Tag = 'func',
  [int]$BootTimeoutSec = 60
)
$ErrorActionPreference = 'Stop'
$here = $PSScriptRoot
$img = Join-Path $here "build-net_probe-$Chip\flash_merged.bin"
$log = "probe-$Tag.uart.log"
$logPath = Join-Path $here "build-net_probe-$Chip\$log"
Remove-Item $logPath -ErrorAction SilentlyContinue

$web = Start-Process python -ArgumentList '-I', '-m', 'http.server', '18071', '--bind', '127.0.0.1', '--directory', (Join-Path $here 'host_www') -PassThru -NoNewWindow -RedirectStandardError (Join-Path $here "web-$Tag.log")
$fwd = @('tcp:127.0.0.1:18070-:3333', 'tcp:127.0.0.1:18072-:3334', 'tcp:127.0.0.1:18073-:3335')
$q = & (Join-Path $here 'run-net.ps1') -Image $img -Chip $Chip -Seconds 0 -HostFwd $fwd -Icount:$Icount -Log $log
try {
  $sw = [Diagnostics.Stopwatch]::StartNew()
  while ($sw.Elapsed.TotalSeconds -lt $BootTimeoutSec) {
    if ((Test-Path $logPath) -and (Select-String -Path $logPath -Pattern 'PROBE OUTBOUND DONE' -Quiet)) { break }
    Start-Sleep -Milliseconds 500
  }
  Write-Host "chip ready after $([int]$sw.Elapsed.TotalSeconds) s"
  $cmd = "python -I `"$here\host_bench.py`" $BenchArgs"
  Invoke-Expression $cmd
  Start-Sleep 2
}
finally {
  Stop-Process -Id $q.Id -Force -ErrorAction SilentlyContinue
  Stop-Process -Id $web.Id -Force -ErrorAction SilentlyContinue
}
Write-Host '--- chip UART (PROBE lines)'
Get-Content $logPath | Select-String 'PROBE|Guru|panic|abort' | ForEach-Object { $_.Line }
