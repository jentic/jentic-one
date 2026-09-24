package ctlcmd

// doctor_mcp.go adds the MCP section to `jenticctl doctor` (local-MCP 2-E3):
// whether an auto-registered MCP entry would actually work on this machine.
// The checks read the entries ACTUALLY WRITTEN into each runtime's config
// (the absolute binary path and the pinned context they carry) rather than
// re-deriving them from the live environment — a PATH lookup or the active
// context can name a different binary/context than the entries do, and
// doctor must judge what the runtimes will spawn. Read-only, like the rest
// of doctor.

import (
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"time"

	sdkconfig "github.com/jentic/jentic-one/cli/client/config"
	"github.com/jentic/jentic-one/cli/internal/config"
	"github.com/jentic/jentic-one/cli/internal/mcpcfg"
	"github.com/jentic/jentic-one/cli/internal/serverinfo"
)

// mcpSection is the report section all MCP rows land under.
const mcpSection = "MCP"

// checkMCP runs the MCP registration health checks. Everything is warn-only
// except an unreadable ca_cert_path: that is broken persisted config (the
// checkConfigValidity precedent), while "no entries"/"binary gone"/"broker
// down" are the fresh-box/not-running cases doctor keeps CI-safe.
func (d *doctor) checkMCP() {
	cfg, err := sdkconfig.Load()
	if err != nil || cfg == nil {
		// No readable agent config: the per-entry context rows report the gap.
		cfg = &sdkconfig.Config{}
	}
	d.checkMCPEntries(cfg, readMCPEntries())
	d.checkMCPCACerts(cfg)
	d.checkMCPBroker()
}

// mcpWrittenEntry is one jentic entry read back out of a runtime's config.
type mcpWrittenEntry struct {
	runtime mcpcfg.Runtime
	path    string
	entry   mcpcfg.Entry
}

// readMCPEntries parses the jentic entry out of every file-backed runtime
// config present on this machine (Cursor and Claude Desktop JSON, Codex
// TOML). Claude Code's entry lives inside claude's own store, reachable only
// by exec'ing its CLI — doctor stays read-only and skips it. Unreadable or
// foreign-shaped files simply yield no entry; the checks below then report
// what IS registered rather than failing on what isn't.
func readMCPEntries() []mcpWrittenEntry {
	home, err := os.UserHomeDir()
	if err != nil {
		return nil
	}
	var out []mcpWrittenEntry
	add := func(rt mcpcfg.Runtime, path string, entry mcpcfg.Entry, ok bool, err error) {
		if err == nil && ok {
			out = append(out, mcpWrittenEntry{runtime: rt, path: path, entry: entry})
		}
	}
	p := mcpcfg.CursorConfigPath(home)
	entry, ok, err := mcpcfg.ReadJSONEntry(p)
	add(mcpcfg.RuntimeCursor, p, entry, ok, err)
	if p = mcpcfg.ClaudeDesktopConfigPath(home, runtime.GOOS); p != "" {
		entry, ok, err = mcpcfg.ReadJSONEntry(p)
		add(mcpcfg.RuntimeClaudeDesktop, p, entry, ok, err)
	}
	p = mcpcfg.CodexConfigPath(home)
	entry, ok, err = mcpcfg.ReadCodexEntry(p)
	add(mcpcfg.RuntimeCodex, p, entry, ok, err)
	return out
}

// checkMCPEntries validates each written entry's two legs: the binary path
// the entry actually spawns (absolute + present on disk) and the context name
// it actually pins (still resolving to a defined environment and identity).
// With no entries at all, a single warn row points at `jentic setup` — there
// is nothing truthful to probe.
func (d *doctor) checkMCPEntries(cfg *sdkconfig.Config, entries []mcpWrittenEntry) {
	const section = mcpSection
	if len(entries) == 0 {
		d.add(section, "entries", statusWarn, "no jentic MCP entries found (cursor / claude-desktop / codex)",
			"run `jentic setup` to register MCP entries")
		return
	}
	for _, we := range entries {
		d.checkMCPEntryBinary(we)
		d.checkMCPEntryContext(cfg, we)
	}
}

// checkMCPEntryBinary verifies the binary the entry pins: absolute (GUI
// runtimes spawn with a minimal PATH) and still present on disk (an uninstall
// or a moved binary strands the entry).
func (d *doctor) checkMCPEntryBinary(we mcpWrittenEntry) {
	const section = mcpSection
	row := "binary (" + string(we.runtime) + ")"
	pinned := we.entry.PinnedBinary()
	switch {
	case pinned == "":
		d.add(section, row, statusWarn, "entry does not name a jentic binary",
			"re-run `jentic setup` to rewrite the entry")
	case !filepath.IsAbs(pinned):
		d.add(section, row, statusWarn, pinned+" (not absolute)",
			"GUI runtimes need an absolute path; re-run `jentic setup`")
	default:
		if _, err := os.Stat(pinned); err != nil {
			d.add(section, row, statusWarn, fmt.Sprintf("%s: %v", pinned, err),
				"the entry points at a missing binary; reinstall jentic or re-run `jentic setup`")
			return
		}
		d.add(section, row, statusPass, pinned, "")
	}
}

// checkMCPEntryContext verifies the context name the entry PINS (not the
// operator's currently active context — switching contexts must not flip this
// row) still resolves to a defined environment and identity.
func (d *doctor) checkMCPEntryContext(cfg *sdkconfig.Config, we mcpWrittenEntry) {
	const section = mcpSection
	row := "context (" + string(we.runtime) + ")"
	name := we.entry.PinnedContext()
	if name == "" {
		d.add(section, row, statusWarn, "entry pins no --context",
			"re-run `jentic setup` to rewrite the entry with a pinned context")
		return
	}
	cctx, ok := cfg.Contexts[name]
	if !ok {
		d.add(section, row, statusWarn, fmt.Sprintf("pinned context %q is not defined", name),
			"re-run `jentic setup` (or `jentic register`) with the intended context active")
		return
	}
	if _, envOK := cfg.Environments[cctx.Environment]; !envOK {
		d.add(section, row, statusWarn,
			fmt.Sprintf("pinned context %q references undefined environment %q", name, cctx.Environment),
			"fix ~/.config/jentic/config.yaml or re-run `jentic register`")
		return
	}
	if _, idOK := cfg.Identities[cctx.Identity]; !idOK {
		d.add(section, row, statusWarn,
			fmt.Sprintf("pinned context %q references undefined identity %q", name, cctx.Identity),
			"fix ~/.config/jentic/config.yaml or re-run `jentic register`")
		return
	}
	d.add(section, row, statusPass, fmt.Sprintf("%s (identity %q, environment %q)", name, cctx.Identity, cctx.Environment), "")
}

// checkMCPCACerts verifies every environment's optional ca_cert_path is
// readable. `jentic mcp` fail-closes on TLS to a private-CA deployment when
// the bundle is gone, so a dangling path is broken config, not a warning.
func (d *doctor) checkMCPCACerts(cfg *sdkconfig.Config) {
	const section = mcpSection
	for name, env := range cfg.Environments {
		if env.CACertPath == "" {
			continue
		}
		row := "ca_cert_path (" + name + ")"
		f, err := os.Open(env.CACertPath)
		if err != nil {
			d.add(section, row, statusFail, fmt.Sprintf("%s: %v", env.CACertPath, err),
				"restore the CA bundle or update ca_cert_path in ~/.config/jentic/config.yaml")
			continue
		}
		_ = f.Close()
		d.add(section, row, statusPass, env.CACertPath, "")
	}
}

// checkMCPBroker probes the broker `jentic mcp`'s execute tool would dial: the
// active context's broker_url when set, else the local default the ctl config
// resolves (the same precedence status uses). Down is a warning — the CI-safe
// not-running posture shared with the control/deploy rows.
func (d *doctor) checkMCPBroker() {
	const section = mcpSection
	target := ""
	if st, err := sdkconfig.LoadState(""); err == nil && st.BrokerURL != "" {
		target = st.BrokerURL
	}
	if target == "" {
		scheme, host := config.DefaultBrokerScheme, config.DefaultBrokerHost
		if cfg, err := config.Load(d.app.Paths); err == nil {
			scheme = cfg.ResolvedBrokerScheme("", false)
			host = cfg.ResolvedBrokerHost("", false)
		}
		target = scheme + "://" + host
	}
	info := serverinfo.Probe(target, 2*time.Second)
	if info.Running {
		d.add(section, "broker", statusPass, target+" ("+valueOr(info.Version, "running")+")", "")
		return
	}
	d.add(section, "broker", statusWarn, target+" offline",
		"run `jenticctl start` (or set broker_url for the environment)")
}
