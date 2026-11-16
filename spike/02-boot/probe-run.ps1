<#
.SYNOPSIS
  Spike step 2: run the peripheral probe in Espressif QEMU and collect results.

  UART0 is a TCP socket (QEMU waits for us before starting the chip, so no
  output is lost). UART1 goes to a file, so the uart1 test can be checked.
  - A test that crashes the chip: recorded as CRASH. The chip reboots, and we
    send a SKIP list (crashed + already-finished tests) so it carries on.
  - The whole chip goes silent: recorded as HANG, and QEMU is restarted.
  The probe runs on a copy of the image, because it writes NVS and LittleFS
  into the emulated flash.

  Writes results\<build folder>.md (committed) and the full UART log next to
  the image (git-ignored).

.EXAMPLE
  .\probe-run.ps1 -Image build-a-esp32-og-probe\probe.ino.merged.bin -Chip esp32
#>
param(
  [Parameter(Mandatory)][string]$Image,
  [ValidateSet('esp32', 'esp32s3')][string]$Chip = 'esp32',
  [int]$Port = 5555,
  [int]$SilenceSec = 30,   # no output this long during a test = whole-chip hang
  [int]$TotalSec = 420,
  [string]$ResultsDir = (Join-Path $PSScriptRoot 'results'),
  # Tests to leave out from the start (e.g. to check whether one test breaks the next).
  # (Not called -Skip: PowerShell names are case-insensitive, so it would clash with $skip below.)
  [string[]]$SkipTests = @(),
  # Extra QEMU arguments, e.g. '-icount','shift=auto'. Also tags the results file name.
  [string[]]$QemuExtra = @(),
  [string]$Tag = '',
  # QEMU isn't on PATH on this machine (see tools-installed.txt item 6).
  [string]$QemuBin = 'C:\Espressif\tools\tools\qemu-xtensa\esp_develop_9.2.2_20260417\qemu\bin'
)
$ErrorActionPreference = 'Stop'
$src = Get-Item (Resolve-Path $Image)
$dir = $src.DirectoryName
$build = $src.Directory.Name
Copy-Item $src.FullName (Join-Path $dir 'probe-flash.bin') -Force
$qemu = Join-Path $QemuBin 'qemu-system-xtensa.exe'
$crashRe = 'Guru Meditation|abort\(\) was called|assert failed|^Backtrace:|^rst:0x|^Rebooting\.\.\.'

$results = [ordered]@{}     # test -> "STATUS detail"
$seconds = @{}              # test -> wall-clock seconds between BEGIN and RESULT
$crashes = @{}              # test -> first crash line (a test gets one retry)
$skip = [Collections.Generic.List[string]]::new()
foreach ($s in $SkipTests) { $skip.Add($s) }
if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
  throw "Port $Port is already in use (another QEMU still running?). Stop it or pass -Port."
}
$log = [Collections.Generic.List[string]]::new()
$boots = [Collections.Generic.List[object]]::new()
$uart1 = ''
$total = [Diagnostics.Stopwatch]::StartNew()
$runs = 0
$deadStarts = 0
$done = $false
$bytes = [byte[]]::new(8192)

function Set-Result([string]$name, [string]$value) {
  # Never let a later SKIP/rerun overwrite a CRASH or HANG verdict.
  if ($results.Contains($name) -and $results[$name] -match '^(CRASH|HANG)') { return }
  $results[$name] = $value
}

while (-not $done -and $total.Elapsed.TotalSeconds -lt $TotalSec) {
  $runs++
  $qemuArgs = @('-machine', $Chip, '-display', 'none', '-monitor', 'none',
    '-serial', "tcp:127.0.0.1:${Port},server=on,wait=on",
    '-serial', 'file:probe.uart1.log',
    '-drive', 'file=probe-flash.bin,if=mtd,format=raw') + $QemuExtra
  $proc = Start-Process -FilePath $qemu -ArgumentList $qemuArgs -WorkingDirectory $dir -PassThru -NoNewWindow
  try {
  $client = $null
  for ($i = 0; $i -lt 100 -and -not $client; $i++) {
    try { $client = [Net.Sockets.TcpClient]::new('127.0.0.1', $Port) } catch { Start-Sleep -Milliseconds 100 }
  }
  if (-not $client) { Stop-Process -Id $proc.Id -Force; throw "Could not connect to QEMU's UART on port $Port" }
  $stream = $client.GetStream()
  $clock = [Diagnostics.Stopwatch]::StartNew()
  $lastData = 0.0
  $buf = ''
  $current = $null
  $beginAt = 0.0
  $restart = $false
  $log.Add("[harness] run $runs started; skip list: $($skip -join ',')")

  while (-not $done -and -not $restart -and $total.Elapsed.TotalSeconds -lt $TotalSec) {
    if ($proc.HasExited) {
      $log.Add("[harness] QEMU exited with code $($proc.ExitCode)")
      if ($lastData -eq 0.0 -and ++$deadStarts -ge 3) { throw 'QEMU exited three times without output; giving up' }
      if ($current) { Set-Result $current "CRASH QEMU itself exited (code $($proc.ExitCode))"; $skip.Add($current) }
      $restart = $true
      break
    }
    if ($stream.DataAvailable) {
      $n = $stream.Read($bytes, 0, $bytes.Length)
      $buf += [Text.Encoding]::Latin1.GetString($bytes, 0, $n)
      $lastData = $clock.Elapsed.TotalSeconds
      while (($nl = $buf.IndexOf("`n")) -ge 0) {
        $line = $buf.Substring(0, $nl).TrimEnd("`r") -replace "`e\[[0-9;]*m", ''   # drop ANSI log colours
        $buf = $buf.Substring($nl + 1)
        $now = $clock.Elapsed.TotalSeconds
        $log.Add(('[run {0} {1,8:N3}s] {2}' -f $runs, $now, $line))
        if ($line -match '^PROBE READY') {
          $boots.Add([pscustomobject]@{ Run = $runs; WallSec = [math]::Round($now, 2); Line = $line })
          $msg = if ($skip.Count) { 'SKIP ' + ($skip -join ',') } else { 'SKIP -' }
          $out = [Text.Encoding]::ASCII.GetBytes("$msg`n")
          $stream.Write($out, 0, $out.Length)
          $current = $null
        }
        elseif ($line -match '^BEGIN (\S+)') { $current = $Matches[1]; $beginAt = $now }
        elseif ($line -match '^RESULT (\S+) (.*)$') {
          $name = $Matches[1]
          $value = $Matches[2]
          if ($crashes.ContainsKey($name)) { $value += " [crashed once before passing on retry: $($crashes[$name])]" }
          Set-Result $name $value
          if ($name -eq $current) { $seconds[$name] = [math]::Round($now - $beginAt, 2) }
          if (-not $skip.Contains($name)) { $skip.Add($name) }   # don't rerun it after a later crash
          $current = $null
        }
        elseif ($line -eq 'PROBE DONE') { $done = $true }
        elseif ($current -and $line -match $crashRe) {
          # Under host CPU load QEMU produces spurious panics, so a first crash gets one retry.
          if ($crashes.ContainsKey($current)) {
            Set-Result $current "CRASH twice: $($crashes[$current]) / then: $line"
            $seconds[$current] = [math]::Round($now - $beginAt, 2)
            if (-not $skip.Contains($current)) { $skip.Add($current) }
            $log.Add("[harness] '$current' crashed the chip a second time; skipping it")
          }
          else {
            $crashes[$current] = $line
            $log.Add("[harness] '$current' crashed the chip; it will be retried once after the reboot")
          }
          $current = $null
        }
      }
    }
    else {
      $quiet = $clock.Elapsed.TotalSeconds - $lastData
      if ($current -and $quiet -gt $SilenceSec) {
        Set-Result $current "HANG whole chip silent for $SilenceSec s"
        if (-not $skip.Contains($current)) { $skip.Add($current) }
        $log.Add("[harness] '$current' hung the whole chip; restarting QEMU")
        $restart = $true
      }
      elseif (-not $current -and $quiet -gt ($SilenceSec + 15)) {
        $log.Add('[harness] chip silent outside a test; restarting QEMU')
        $restart = $true
      }
      else { Start-Sleep -Milliseconds 20 }
    }
  }
  }
  finally {
    # Always stop QEMU, even if the harness fails: an orphan would hold the port.
    if ($client) { $client.Close() }
    if (-not $proc.HasExited) { Stop-Process -Id $proc.Id -Force }
    $proc.WaitForExit()
  }
  $u1 = Join-Path $dir 'probe.uart1.log'
  if (Test-Path $u1) { $uart1 += [IO.File]::ReadAllText($u1) }
}

# UART1's verdict comes from the capture file, not from the chip.
if ($results.Contains('uart1') -and $results['uart1'] -match '^RAN') {
  $results['uart1'] = if ($uart1 -match 'probe uart1 says hello') { 'OK host received the UART1 text' } else { "FAIL host captured: '$($uart1.Trim())'" }
}

$log | Set-Content -Encoding utf8 (Join-Path $dir 'probe.uart0.log')
$errLines = $log | ForEach-Object { if ($_ -match '\] ([EW] \(\d+\) .*)$') { $Matches[1] -replace '^([EW]) \(\d+\)', '$1' } } | Select-Object -Unique

New-Item -ItemType Directory -Force $ResultsDir | Out-Null
$md = [Collections.Generic.List[string]]::new()
$md.Add("# Probe results: $build ($Chip)")
$md.Add('')
$md.Add("- Image: ``$($src.Name)``, run $(Get-Date -Format 'yyyy-MM-dd HH:mm'); QEMU runs needed: $runs; finished: $done")
$md.Add("- Extra QEMU args: $(if ($QemuExtra) { '``' + ($QemuExtra -join ' ') + '``' } else { 'none' }); skipped from the start: $(if ($SkipTests) { $SkipTests -join ', ' } else { 'none' })")
$md.Add("- Spurious 'Cache disabled but cached memory region accessed' panics seen: $(($log | Select-String 'Cache disabled but cached memory region accessed').Count); chip resets seen: $(($log | Select-String '\] rst:0x').Count)")
$cpuLoad = (Get-CimInstance Win32_Processor | Measure-Object LoadPercentage -Average).Average
$md.Add("- Host CPU load when the run ended: $cpuLoad%")
foreach ($b in $boots) { $md.Add("- Boot (run $($b.Run)): host wall-clock $($b.WallSec) s to ``$($b.Line)``") }
$md.Add('')
$md.Add('| Test | Status | Detail | Wall s |')
$md.Add('|---|---|---|---|')
foreach ($k in $results.Keys) {
  $status, $detail = $results[$k] -split ' ', 2
  $md.Add("| $k | $status | $($detail -replace '\|', '/') | $($seconds[$k]) |")
}
$md.Add('')
$md.Add('## ESP-IDF errors and warnings seen (deduplicated, timestamps removed)')
$md.Add('')
$md.Add('```')
if ($errLines) { $errLines | Select-Object -First 40 | ForEach-Object { $md.Add($_) } } else { $md.Add('(none)') }
$md.Add('```')
$resultFile = Join-Path $ResultsDir ("$build" + $(if ($Tag) { "-$Tag" } else { '' }) + '.md')
$md | Set-Content -Encoding utf8 $resultFile

Write-Host ($md -join "`n")
Write-Host "`nSaved: $resultFile"
