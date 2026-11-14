package localagent

import (
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
)

// ErrSymlinkedPath identifies a path that crosses a symbolic link below its
// managed home.
var ErrSymlinkedPath = errors.New("symlinked path component")

// AssertNoSymlinkComponents checks each existing component of path below home.
// Intermediate symlinks are refused because path-based removal would resolve
// them in the caller's filesystem context. A symlink at the final component is
// accepted because rm removes the link itself. A missing component ends the
// check successfully because the remaining descendants cannot exist.
func AssertNoSymlinkComponents(home, path string) error {
	rel, err := filepath.Rel(filepath.Clean(home), filepath.Clean(path))
	if err != nil {
		return fmt.Errorf("resolve %q relative to agent home %q: %w", path, home, err)
	}
	if rel == "." || rel == ".." || strings.HasPrefix(rel, ".."+string(filepath.Separator)) || filepath.IsAbs(rel) {
		return fmt.Errorf("path %q must be a strict descendant of agent home %q", path, home)
	}

	parts := strings.Split(rel, string(filepath.Separator))
	current := filepath.Clean(home)
	for i, part := range parts {
		current = filepath.Join(current, part)
		info, err := os.Lstat(current)
		if errors.Is(err, os.ErrNotExist) {
			return nil
		}
		if err != nil {
			return fmt.Errorf("inspect path component %q: %w", current, err)
		}
		if info.Mode()&os.ModeSymlink != 0 && i < len(parts)-1 {
			return fmt.Errorf("%w: %s", ErrSymlinkedPath, current)
		}
	}
	return nil
}

// KillAgentProcessesCmd terminates every process owned by agentUser. Reset runs
// this before privileged filesystem steps so the account cannot continue to
// mutate its home during teardown. An invalid account name returns nil.
func KillAgentProcessesCmd(agentUser string) *exec.Cmd {
	if err := ValidateAgentUser(agentUser); err != nil {
		return nil
	}
	return exec.Command("sudo", "pkill", "-KILL", "-u", agentUser) //nolint:gosec // agentUser is validated by ValidateAgentUser.
}

// RemoveAsAgentCmd removes paths with agentUser's own OS permissions. The
// command uses direct argv throughout, and /bin/rm receives an option terminator
// before the paths. Invalid account names and empty path lists return nil.
func RemoveAsAgentCmd(agentUser string, paths []string) *exec.Cmd {
	if err := ValidateAgentUser(agentUser); err != nil || len(paths) == 0 {
		return nil
	}
	args := append([]string{"-u", agentUser, "--", "/bin/rm", "-rf", "--"}, paths...)
	return exec.Command("sudo", args...) //nolint:gosec // agentUser is validated and paths are passed as direct argv.
}
