package localagent

// servicebinary.go names the ROOT-OWNED copy of the jentic binary that older
// releases' sudo-shim MCP entries and their argv-pinned sudoers lines ran, and
// builds the command `jentic reset` uses to remove it once every service
// account is gone.

import (
	"os"
	"os/exec"
)

// serviceBinDir is the root-owned directory holding the pinned copy. libexec
// is the conventional home for binaries run by other programs rather than
// typed by users, and /usr/local stays writable by root on macOS under SIP.
// The same path is used on Linux (FHS: /usr/local/libexec for locally
// installed helpers).
const serviceBinDir = "/usr/local/libexec/jentic"

// serviceBinName is the file name of the pinned copy inside serviceBinDir.
const serviceBinName = "jentic"

// ServiceBinaryPath is the absolute path of the root-owned jentic copy the
// sudo-shim MCP entries ran.
func ServiceBinaryPath() string { return serviceBinDir + "/" + serviceBinName }

// RemoveServiceBinaryCmd returns the root-side command that removes the pinned
// copy (a symlink at the path is removed as a link, never followed), any temp
// file an interrupted install left behind, and the managed directory once
// empty. A no-op when nothing is installed. Parent directories are left
// alone: they may predate jentic. Runs as root.
func RemoveServiceBinaryCmd() *exec.Cmd {
	return exec.Command("sudo", "sh", "-c", serviceBinaryRemoveScript(serviceBinDir)) //nolint:gosec // fixed root-owned path, shell-quoted.
}

// serviceBinaryRemoveScript is RemoveServiceBinaryCmd's script, parameterised
// on dir so tests can run it unprivileged against a temp tree.
func serviceBinaryRemoveScript(dir string) string {
	return fixedPATHPrefix + `d=` + shellQuote(dir) + `; ` +
		`if [ -L "$d" ] || [ ! -d "$d" ]; then exit 0; fi; ` +
		`rm -f -- "$d"/` + serviceBinName + ` "$d"/.` + serviceBinName + `.??????; ` +
		`rmdir -- "$d" 2>/dev/null || true`
}

// ServiceBinaryInstalled reports whether anything (file or link) occupies the
// pinned copy's path — the teardown survey.
func ServiceBinaryInstalled() bool {
	_, err := os.Lstat(ServiceBinaryPath())
	return err == nil
}
