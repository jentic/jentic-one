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
  [string]$BinDir
)

$ErrorActionPreference = 'Stop'

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
function Fail($m) { Write-Error "  FAIL $m"; exit 1 }

try {
  Write-Host "== jentic CLI smoke (bin: $BinDir) =="

  if (-not (Test-Path $jentic))    { Fail "jentic binary not found at $jentic" }
  if (-not (Test-Path $jenticctl)) { Fail "jenticctl binary not found at $jenticctl" }
  Pass "both binaries present"

  # 1. --version on both binaries.
  if ((& $jentic --version)    -notmatch 'jentic') { Fail "jentic --version had no version line" }
  if ((& $jenticctl --version) -notmatch 'jentic') { Fail "jenticctl --version had no version line" }
  Pass "--version on both binaries"

  # 2. jentic doctor --json parses; exits 0 unless a HARD check fails.
  $doctorErr = Join-Path $scratch 'doctor.err'
  $doctor = & $jentic doctor --json 2>$doctorErr
  if ($LASTEXITCODE -ne 0) {
    Write-Host "---- jentic doctor --json (exit $LASTEXITCODE) ----"
    Write-Host $doctor
    if (Test-Path $doctorErr) { Write-Host "---- stderr ----"; Get-Content $doctorErr | Write-Host }
    Fail "jentic doctor --json exited non-zero (a hard check failed)"
  }
  if ($doctor -notmatch '"checks"')    { Fail "jentic doctor JSON had no checks array" }
  Pass "jentic doctor --json parses"

  # 3. jentic profile list exits 0 (empty is fine on a fresh scratch home).
  & $jentic profile list *> $null
  if ($LASTEXITCODE -ne 0) { Fail "jentic profile list exited non-zero" }
  Pass "jentic profile list"

  # 4. jenticctl doctor --json parses (non-zero only on a fail row; a scratch
  #    home with no install may warn but must produce a well-formed envelope).
  $ctlDoctor = & $jenticctl doctor --json 2>$null
  if (($ctlDoctor -notmatch '"checks"') -and ($ctlDoctor -notmatch '"failures"')) {
    Fail "jenticctl doctor JSON was not well-formed"
  }
  Pass "jenticctl doctor --json parses"

  Write-Host "== smoke passed =="
}
finally {
  Remove-Item -Recurse -Force $scratch -ErrorAction SilentlyContinue
}
