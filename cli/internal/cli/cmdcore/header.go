package cmdcore

import (
	"fmt"
	"os"

	"github.com/charmbracelet/x/term"
	sdkconfig "github.com/jentic/jentic-one/cli/client/config"
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

	baseURL := headerProbeURL(a.Paths, baseURLFlag)
	info := a.probeServer(baseURL)

	panel := theme.VersionPanel(cliVersion, info.Version, info.Running)
	header := theme.LogoHeader(width, panel)
	// Surface the active context under the version panel so the persistent,
	// always-on brand surface answers "who am I right now?" (UX5). Dim, like the
	// rest of the panel, and only on an interactive terminal — this branch is
	// already gated on a TTY, so it is suppressed for piped/machine output
	// exactly like the version panel above it.
	if name := activeContextName(); name != "" {
		header += "\n" + theme.Dim.Render("context: "+name)
	}
	return header
}

// activeContextName returns the name of the active context, or "" when none is
// set or the config can't be read. Best-effort and non-fatal: the header is
// cosmetic, so any resolution failure just omits the line.
func activeContextName() string {
	cfg, err := sdkconfig.Load()
	if err != nil {
		return ""
	}
	return cfg.ActiveContext
}

// headerProbeURL resolves the control-plane URL the help header probes for a
// server version, with precedence: an explicit --base-url flag > the active
// context's environment base_url > the legacy config's base_url > the built-in
// default (UX-25). Before this the header only consulted the legacy
// internal/config store, so a context-only machine pointed at a remote install
// still probed the local default (127.0.0.1:8000) in its banner. Every branch
// is best-effort and non-fatal — the header is cosmetic, so any resolution
// failure just falls through to the next source.
func headerProbeURL(paths config.Paths, baseURLFlag string) string {
	if baseURLFlag != "" {
		return baseURLFlag
	}
	// The active context's environment base_url is what data-plane
	// commands actually use, so the header should reflect the same target.
	if st, err := sdkconfig.LoadState(""); err == nil && st != nil && st.BaseURL != "" {
		return st.BaseURL
	}
	// Legacy fallback (a machine still on the ~/.jentic store).
	if cfg, err := config.Load(paths); err == nil {
		return cfg.ResolvedBaseURLOr("")
	}
	return config.DefaultBaseURL
}

// probeServer resolves the interactive header's server-version probe through the
// ProbeServer seam (QA-4), defaulting to the bounded serverinfo.Probe. Isolating
// it here means the help header can be tested (and disabled) without a live
// network dependency, and the DefaultTimeout bound is exercised via the seam.
func (a *App) probeServer(baseURL string) serverinfo.Info {
	if a.ProbeServer != nil {
		return a.ProbeServer(baseURL)
	}
	return serverinfo.Probe(baseURL, serverinfo.DefaultTimeout)
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
