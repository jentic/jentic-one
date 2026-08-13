# smoke.ps1 — cross-platform post-install smoke for the jentic/jenticctl binaries
# on Windows (CLI-V2 Phase 9, impl/9.0 §9.3). The PowerShell sibling of smoke.sh:
# it asserts the same binary + agent-surface contract that must hold with NO
# running server. install.sh is WSL-only, so on native Windows this runs against
# the binaries built by `make -C cli build` (or `go build ./cmd/...`).
#
# Usage: pwsh -File smoke.ps1 <bin-dir>
#   <bin-dir> holds jentic.exe and jenticctl.exe.
#
# Exits non-zero on the first failed assertion, using a scratch JENTIC_HOME so it
# never touches a real install.

param(
  [Parameter(Mandatory = $true)]
  [string]$BinDir,
  [string]$LiveUrl = ''
)

# NB: intentionally NOT 'Stop'. Under 'Stop', PowerShell turns any bytes a NATIVE
# command writes to stderr into a terminating error — so a binary that prints a
# perfectly benign warning line to stderr aborts the script before $LASTEXITCODE
# is even set, masquerading as a "hard check failed". We drive control flow off
# explicit $LASTEXITCODE checks instead, and fold each command's stderr into its
# captured output for diagnostics.
$ErrorActionPreference = 'Continue'

# `go build -o jentic` produces `jentic` on Windows unless the output name ends
# in .exe; `make build` does not add the suffix, so resolve either form.
function Resolve-Bin($dir, $name) {
  foreach ($candidate in @((Join-Path $dir "$name.exe"), (Join-Path $dir $name))) {
    if (Test-Path $candidate) { return $candidate }
  }
  return (Join-Path $dir "$name.exe") # non-existent; the presence check below reports it
}

$jentic    = Resolve-Bin $BinDir 'jentic'
$jenticctl = Resolve-Bin $BinDir 'jenticctl'

# Scratch state, removed on exit — never touch the runner's real profile.
$scratch = Join-Path ([System.IO.Path]::GetTempPath()) ("jentic-smoke-" + [System.Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $scratch -Force | Out-Null
$env:JENTIC_HOME      = Join-Path $scratch '.jentic'
$env:XDG_CONFIG_HOME  = Join-Path $scratch '.config'
$env:XDG_STATE_HOME   = Join-Path $scratch 'state'
$env:XDG_CACHE_HOME   = Join-Path $scratch 'cache'
$env:JENTIC_MODE      = 'agent'

function Pass($m) { Write-Host "  ok   $m" }
function Fail($m) { Write-Host "  FAIL $m"; exit 1 }

# Run a native command, capturing stdout+stderr as a single string and the exit
# code, without letting stderr bytes abort the script. Returns a PSCustomObject
# with .Output (string) and .Code (int).
function Invoke-Native {
  param([string]$Exe, [string[]]$CmdArgs)
  $out = & $Exe @CmdArgs 2>&1 | Out-String
  return [PSCustomObject]@{ Output = $out; Code = $LASTEXITCODE }
}

try {
  Write-Host "== jentic CLI smoke (bin: $BinDir) =="

  if (-not (Test-Path $jentic))    { Fail "jentic binary not found at $jentic" }
  if (-not (Test-Path $jenticctl)) { Fail "jenticctl binary not found at $jenticctl" }
  Pass "both binaries present"

  # 1. --version on both binaries.
  $v = Invoke-Native $jentic @('--version')
  if ($v.Output -notmatch 'jentic') { Fail "jentic --version had no version line`n$($v.Output)" }
  $v = Invoke-Native $jenticctl @('--version')
  if ($v.Output -notmatch 'jentic') { Fail "jenticctl --version had no version line`n$($v.Output)" }
  Pass "--version on both binaries"

  # 2. jentic doctor --json parses AND exits 0 (QA-1: assert the code too).
  #    Identity/reachability rows only WARN with no server, so a fresh scratch
  #    home is a clean (exit 0) report.
  $d = Invoke-Native $jentic @('doctor', '--json')
  if ($d.Code -ne 0) {
    Write-Host "---- jentic doctor --json (exit $($d.Code)) ----"
    Write-Host $d.Output
    Fail "jentic doctor --json exit=$($d.Code), want 0 (a hard check failed)"
  }
  if ($d.Output -notmatch '"checks"') { Fail "jentic doctor JSON had no checks array`n$($d.Output)" }
  Pass "jentic doctor --json parses and exits 0"

  # 3. jentic access whoami --json on a FRESH scratch home has no active context,
  #    so the contract is a RESOLVE_FAILED error envelope AND a non-zero exit
  #    (QA-1: assert BOTH, not merely "some well-formed envelope"). `context list`
  #    is a management command fenced in agent mode, so it is NOT used here.
  $p = Invoke-Native $jentic @('access', 'whoami', '--json')
  if ($p.Code -eq 0) { Fail "jentic access whoami --json exit=0 on a no-context home, want non-zero`n$($p.Output)" }
  if ($p.Output -notmatch '"error_code"\s*:\s*"RESOLVE_FAILED"') {
    Fail "jentic access whoami --json (no context) must emit error_code RESOLVE_FAILED`n$($p.Output)"
  }
  Pass "jentic access whoami --json (no context) -> RESOLVE_FAILED, exit $($p.Code)"

  # 4. jenticctl doctor --json parses (non-zero only on a fail row; a scratch
  #    home with no install may warn but must produce a well-formed envelope).
  $c = Invoke-Native $jenticctl @('doctor', '--json')
  if (($c.Output -notmatch '"checks"') -and ($c.Output -notmatch '"failures"')) {
    Fail "jenticctl doctor JSON was not well-formed`n$($c.Output)"
  }
  Pass "jenticctl doctor --json parses"

  # 5. LIVE ONLY (QA-2): prove reachability of the base URL the CLI resolves to —
  #    an assertion the offline legs structurally cannot make — then confirm
  #    `jentic doctor` sees the live stack (exit 0). A fresh stack has no approved
  #    agent token, so an authenticated data call cannot succeed here.
  if ($LiveUrl -ne '') {
    try { Invoke-WebRequest -UseBasicParsing -Uri "$LiveUrl/health" -TimeoutSec 10 | Out-Null }
    catch { Fail "live: control plane health endpoint at $LiveUrl/health not reachable`n$_" }
    $env:JENTIC_BASE_URL = $LiveUrl
    $ld = Invoke-Native $jentic @('doctor', '--json')
    if ($ld.Code -ne 0) { Fail "live: jentic doctor --json exit=$($ld.Code) against a live stack, want 0`n$($ld.Output)" }
    Pass "live: control plane reachable at $LiveUrl + jentic doctor exit 0"
  }

  Write-Host "== smoke passed =="
}
finally {
  Remove-Item -Recurse -Force $scratch -ErrorAction SilentlyContinue
}

# Fall-through means every assertion passed (failures call Fail -> exit 1). Exit
# 0 EXPLICITLY: without this the process inherits $LASTEXITCODE from the last
# native command run above (e.g. `jenticctl doctor` may exit non-zero on a warn
# row, which the smoke deliberately tolerates), leaking a false failure.
exit 0
