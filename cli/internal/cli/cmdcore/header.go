package cmdcore

import (
	"fmt"
	"os"

	"github.com/charmbracelet/x/term"
	"github.com/jentic/jentic-one/cli/internal/config"
	"github.com/jentic/jentic-one/cli/internal/serverinfo"
	"github.com/jentic/jentic-one/cli/internal/theme"
	"github.com/spf13/cobra"
)

// BrandHeader renders the gradient wordmark with a right-aligned version panel
// (CLI version + probed server version). The panel is only drawn for an
// interactive terminal — we need its width and want to avoid a network probe
// when output is piped — otherwise it falls back to the plain logo. baseURLFlag
// overrides the configured control-plane URL for the server probe.
func (a *App) BrandHeader(baseURLFlag, cliVersion string) string {
	fd := os.Stdout.Fd()
	if !term.IsTerminal(fd) {
		return theme.Logo()
	}
	width, _, err := term.GetSize(fd)
	if err != nil || width <= 0 {
		return theme.Logo()
	}

	baseURL := config.DefaultBaseURL
	if cfg, err := config.Load(a.Paths); err == nil {
		baseURL = cfg.ResolvedBaseURLOr(baseURLFlag)
	} else if baseURLFlag != "" {
		baseURL = baseURLFlag
	}
	info := serverinfo.Probe(baseURL, serverinfo.DefaultTimeout)

	panel := theme.VersionPanel(cliVersion, info.Version, info.Running)
	return theme.LogoHeader(width, panel)
}

// banner prints the jentic wordmark before a command runs, so the brand mark is
// present across the whole CLI. It is installed once as the root's
// PersistentPreRun and is deliberately conservative: it stays silent for
// non-interactive output (so pipes/scripts stay clean), for the completion
// script, for commands that render their own branded header (help, install,
// update), and whenever JENTIC_NO_BANNER is set.
//
// The logo sits flush at the top (no leading blank line) with a single blank
// line beneath it before the command's own output — the spacing used by every
// branded surface (help, update) so the brand mark looks the same everywhere.
func (a *App) banner(cmd *cobra.Command) {
	if os.Getenv("JENTIC_NO_BANNER") != "" {
		return
	}
	if bannerSkip(cmd) {
		return
	}
	if !term.IsTerminal(os.Stdout.Fd()) {
		return
	}
	fmt.Fprint(a.Out, theme.Logo())
	fmt.Fprintln(a.Out)
}

// bannerSkip reports whether the global banner should be suppressed for cmd:
// non-runnable parents (which fall through to the help screen, where helpFunc
// draws the header itself), the help/completion/install/update command trees
// (which either own their header or must emit machine-readable output — e.g.
// the completion *script* must stay clean), and `execute`, whose output is the
// upstream response and is commonly piped/captured, so the logo would be noise.
func bannerSkip(cmd *cobra.Command) bool {
	if !cmd.Runnable() {
		return true
	}
	for c := cmd; c != nil; c = c.Parent() {
		switch c.Name() {
		case "help", "completion", "install", "update", "execute":
			return true
		}
	}
	return false
}
