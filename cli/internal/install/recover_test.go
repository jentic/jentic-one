package install

import (
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

// scriptDocker installs a `docker` stub on PATH built from the given shell body
// (which sees $1, $2, ... as the docker arguments) and returns a log-file path
// the body may append to via $LOG. POSIX-only, mirroring fakeDocker.
func scriptDocker(t *testing.T, body string) string {
	t.Helper()
	if runtime.GOOS == "windows" {
		t.Skip("shell-stub PATH technique is POSIX-only")
	}
	dir := t.TempDir()
	log := filepath.Join(dir, "log")
	script := "#!/bin/sh\nLOG='" + log + "'\n" + body
	if err := os.WriteFile(filepath.Join(dir, "docker"), []byte(script), 0o755); err != nil {
		t.Fatalf("write docker stub: %v", err)
	}
	t.Setenv("PATH", dir+string(os.PathListSeparator)+os.Getenv("PATH"))
	return log
}

func TestVolumeExists(t *testing.T) {
	// The stub knows one volume; inspect of anything else fails with the
	// daemon's real "no such volume" phrasing, and a special name simulates a
	// daemon-level failure.
	scriptDocker(t, `
if [ "$1" = "volume" ] && [ "$2" = "inspect" ]; then
  case "$3" in
    jentic_db-data) echo '[{"Name":"jentic_db-data"}]'; exit 0 ;;
    broken) echo "Cannot connect to the Docker daemon" 1>&2; exit 1 ;;
    *) echo "Error response from daemon: get $3: no such volume" 1>&2; exit 1 ;;
  esac
fi
exit 0
`)

	if got, err := VolumeExists("jentic_db-data"); err != nil || !got {
		t.Errorf("VolumeExists(existing) = %v, %v; want true, nil", got, err)
	}
	if got, err := VolumeExists("jentic_jentic-data"); err != nil || got {
		t.Errorf("VolumeExists(missing) = %v, %v; want false, nil", got, err)
	}
	// "Could not tell" must be an error, not false: the caller treats false as
	// "fresh volume, safe to discard on failure".
	if _, err := VolumeExists("broken"); err == nil {
		t.Errorf("VolumeExists(daemon down) should return an error")
	}
}

func TestResetFreshDataVolumes(t *testing.T) {
	log := scriptDocker(t, `
echo "$@" >> "$LOG"
if [ "$1" = "volume" ] && [ "$2" = "rm" ]; then
  echo "Error: No such volume: $3" 1>&2
  exit 1
fi
exit 0
`)

	var buf strings.Builder
	err := ResetFreshDataVolumes(&buf, "/home/u/.jentic/docker-compose.yaml", []string{"jentic_db-data"})
	if err != nil {
		t.Fatalf("ResetFreshDataVolumes: %v", err)
	}
	logged, _ := os.ReadFile(log)
	out := string(logged)
	// Both the compose teardown and the explicit removal must be attempted
	// (the latter covers volumes `down -v` misses; already-gone is a no-op).
	if !strings.Contains(out, "down -v") {
		t.Errorf("expected a compose down -v, log:\n%s", out)
	}
	if !strings.Contains(out, "volume rm jentic_db-data") {
		t.Errorf("expected an explicit volume rm, log:\n%s", out)
	}
}

func TestManualResetCommand(t *testing.T) {
	got := ManualResetCommand("/home/u/.jentic/docker-compose.yaml")
	want := "docker compose -p jentic -f /home/u/.jentic/docker-compose.yaml down -v"
	if got != want {
		t.Errorf("ManualResetCommand = %q, want %q", got, want)
	}
}
