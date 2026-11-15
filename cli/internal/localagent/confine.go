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

// AssertManagedHomeComponents checks every existing component of an absolute
// managed home from the filesystem root through the home itself. This includes
// AgentHomeRoot, nested parents, and a symlink at the recorded home. Missing
// components end the check because their descendants cannot exist.
func AssertManagedHomeComponents(home string) error {
	clean := filepath.Clean(home)
	if !filepath.IsAbs(clean) || clean != home {
		return fmt.Errorf("managed agent home %q must be an absolute clean path", home)
	}
	separator := string(filepath.Separator)
	current := filepath.VolumeName(clean) + separator
	rest := strings.TrimPrefix(clean, current)
	for _, part := range strings.Split(rest, separator) {
		if part == "" {
			continue
		}
		current = filepath.Join(current, part)
		info, err := os.Lstat(current)
		if errors.Is(err, os.ErrNotExist) {
			return nil
		}
		if err != nil {
			return fmt.Errorf("inspect managed-home component %q: %w", current, err)
		}
		if info.Mode()&os.ModeSymlink != 0 {
			return fmt.Errorf("%w: managed-home component %s", ErrSymlinkedPath, current)
		}
	}
	return nil
}

// KillAgentProcessesCmd terminates every process owned by agentUser. The inner
// shell maps pkill's documented no-match status to success; sudo failures remain
// outside that mapping and therefore fail closed. An invalid account name returns nil.
func KillAgentProcessesCmd(agentUser string) *exec.Cmd {
	if err := ValidateAgentUser(agentUser); err != nil {
		return nil
	}
	script := fixedPATHPrefix + "/usr/bin/pkill -KILL -u " + shellQuote(agentUser) + `; status=$?; ` +
		`if [ "$status" -eq 1 ]; then exit 0; fi; exit "$status"`
	return resetSudoCmd("/bin/sh", "-c", script)
}

// AssertNoAgentProcessesCmd succeeds only when pgrep reports the documented
// no-process status. Surviving processes, probe failures, and sudo failures are
// all non-zero results. An invalid account name returns nil.
func AssertNoAgentProcessesCmd(agentUser string) *exec.Cmd {
	if err := ValidateAgentUser(agentUser); err != nil {
		return nil
	}
	script := fixedPATHPrefix + "/usr/bin/pgrep -u " + shellQuote(agentUser) + `; status=$?; ` +
		`if [ "$status" -eq 1 ]; then exit 0; fi; ` +
		`if [ "$status" -eq 0 ]; then printf '%s\n' 'agent processes are still running' >&2; exit 2; fi; ` +
		`exit "$status"`
	return resetSudoCmd("/bin/sh", "-c", script)
}

// RemoveAsAgentCmd removes paths with agentUser's own OS permissions. The
// command uses direct argv throughout, and /bin/rm receives an option terminator
// before the paths. Invalid account names and empty path lists return nil.
func RemoveAsAgentCmd(agentUser string, paths []string) *exec.Cmd {
	if err := ValidateAgentUser(agentUser); err != nil || len(paths) == 0 {
		return nil
	}
	args := append([]string{"-u", agentUser, "--", "/bin/rm", "-rf", "--"}, paths...)
	return resetSudoCmd(args...)
}
