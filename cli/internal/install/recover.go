package install

import (
	"errors"
	"fmt"
	"io"
	"os/exec"
	"strings"
)

// This file is the fresh-install migration-failure recovery (#992 item 3).
//
// The failure it heals: `docker compose run app … migrations.run` starts the
// db service, whose named volume is created — and initdb'd — on first use. If
// that first migration then fails for any reason (a transient build problem, a
// half-pulled image, the mid-init crash from the original report), the volume
// stays behind holding a half-initialized database. Every later install then
// finds "data present" and treats it as a live database to be preserved, so
// the breakage silently outlives the retry that should have fixed it.
//
// The fix is scoped by one question: did the data volume exist BEFORE this
// install ran migrations? If not, this run created it, there is nothing in it
// worth keeping, and it is discarded automatically so a re-run starts clean.
// If it pre-existed, it may hold real data — recovery is never automatic and
// the operator gets the literal reset command plus a backup warning instead.

// VolumeExists reports whether the named docker volume exists. "No such
// volume" is a clean false; any other inspect failure (daemon down, permission
// denied) is an error so callers do not mistake "could not tell" for "fresh" —
// that mistake would make the recovery path destroy a real database.
func VolumeExists(name string) (bool, error) {
	if _, err := exec.LookPath("docker"); err != nil {
		return false, errors.New("`docker` not found on PATH; cannot inspect volumes")
	}
	out, err := exec.Command("docker", "volume", "inspect", name).CombinedOutput() //nolint:gosec // name comes from DataVolumeNames, a CLI-owned constant set.
	if err == nil {
		return true, nil
	}
	if strings.Contains(strings.ToLower(string(out)), "no such volume") {
		return false, nil
	}
	return false, fmt.Errorf("docker volume inspect %s: %w", name, err)
}

// ResetFreshDataVolumes tears the stack down and removes the given data
// volumes. Only call this when the volumes are known to be fresh (created by
// the current, failed install run — see VolumeExists); it destroys the
// database. RemoveDataVolumes runs even if `down -v` fails: the goal is
// "ensure gone", and the explicit removal also covers volumes `down -v`
// misses (see its doc comment).
func ResetFreshDataVolumes(w io.Writer, composePath string, volumes []string) error {
	downErr := ComposeDownVolumes(w, composePath)
	if _, rmErr := RemoveDataVolumes(w, volumes); rmErr != nil {
		return rmErr
	}
	return downErr
}

// ManualResetCommand is the literal command an operator runs to discard the
// stack's containers and data volumes. Printed (never executed) when a
// migration fails over a pre-existing volume.
func ManualResetCommand(composePath string) string {
	return "docker compose -p " + composeProjectName + " -f " + composePath + " down -v"
}
