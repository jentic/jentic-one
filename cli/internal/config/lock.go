package config

import (
	"fmt"
	"os"
	"syscall"
)

// configLockName is the sidecar advisory-lock file that serialises config.yaml
// mutations. It is separate from config.yaml itself so the lock survives the
// atomic rename in Save (which replaces the config.yaml inode) — locking the file
// being renamed over would drop the lock the instant the rename lands.
const configLockName = ".config.lock"

// lockConfig takes an exclusive advisory lock (flock) on the sidecar lock file in
// the config directory, returning an unlock function the caller MUST defer. The
// lock is process-wide advisory: it coordinates concurrent jentic invocations that
// route their config writes through Mutate, so two `jentic run` grants can't
// interleave a read-modify-write and lose one another's change. It is best-effort
// against a hand-run editor (advisory locks don't stop a writer that ignores
// them), which is why Save is also atomic. flock is released automatically if the
// process dies, so a crashed holder never wedges the lock.
func lockConfig(paths Paths) (func(), error) {
	path := paths.Dir() + string(os.PathSeparator) + configLockName
	f, err := os.OpenFile(path, os.O_CREATE|os.O_RDWR, 0o600) //nolint:gosec // path is under the CLI's own JENTIC_HOME, not user input.
	if err != nil {
		return nil, fmt.Errorf("open config lock %s: %w", path, err)
	}
	if err := syscall.Flock(int(f.Fd()), syscall.LOCK_EX); err != nil {
		_ = f.Close()
		return nil, fmt.Errorf("lock config %s: %w", path, err)
	}
	return func() {
		// Releasing the flock and closing the fd; the lock file itself is left in
		// place (cheap, and re-created next time regardless).
		_ = syscall.Flock(int(f.Fd()), syscall.LOCK_UN)
		_ = f.Close()
	}, nil
}
