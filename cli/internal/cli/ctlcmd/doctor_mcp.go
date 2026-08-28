package ctlcmd

// doctor_mcp.go adds the MCP section to `jenticctl doctor` (local-MCP 2-E3):
// whether an auto-registered MCP entry would actually work on this machine.
// The written entries carry an absolute `jentic` path and a pinned context and
// then depend on the environment's TLS material and the broker being up —
// exactly the four legs probed here. Read-only, like the rest of doctor.

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"time"

	sdkconfig "github.com/jentic/jentic-one/cli/client/config"
	"github.com/jentic/jentic-one/cli/internal/config"
	"github.com/jentic/jentic-one/cli/internal/serverinfo"
)

// mcpSection is the report section all MCP rows land under.
const mcpSection = "MCP"

// checkMCP runs the MCP registration health checks. Everything is warn-only
// except an unreadable ca_cert_path: that is broken persisted config (the
// checkConfigValidity precedent), while "binary not installed"/"broker down"
// are the fresh-box/not-running cases doctor keeps CI-safe.
func (d *doctor) checkMCP() {
	d.checkMCPBinary()

	cfg, err := sdkconfig.Load()
	if err != nil || cfg == nil {
		// No readable agent config: the context row below reports the gap.
		cfg = &sdkconfig.Config{}
	}
	d.checkMCPCACerts(cfg)
	d.checkMCPContext(cfg)
	d.checkMCPBroker()
}

// checkMCPBinary verifies the `jentic` binary MCP entries spawn resolves on
// PATH to an absolute path. GUI runtimes spawn servers with a minimal PATH, so
// the entries embed the absolute path — but a missing/relative lookup here
// means `jentic setup` could not have written a sound entry.
func (d *doctor) checkMCPBinary() {
	const section = mcpSection
	path, err := exec.LookPath("jentic")
	switch {
	case err != nil:
		d.add(section, "jentic binary", statusWarn, "not found on PATH",
			"install the jentic CLI, then re-run `jentic setup` to register MCP entries")
	case !filepath.IsAbs(path):
		d.add(section, "jentic binary", statusWarn, path+" (not absolute)",
			"GUI runtimes need an absolute path; re-run `jentic setup` after fixing PATH")
	default:
		d.add(section, "jentic binary", statusPass, path, "")
	}
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

// checkMCPContext verifies the active context — the name auto-registered
// entries pin with --context — still resolves to a defined environment and
// identity. A renamed/removed context strands every written entry at once.
func (d *doctor) checkMCPContext(cfg *sdkconfig.Config) {
	const section = mcpSection
	st, err := sdkconfig.LoadState("")
	if err != nil {
		d.add(section, "context", statusWarn, "no active context to pin: "+err.Error(),
			"run `jentic register`, then `jentic setup` to write the MCP entries")
		return
	}
	if st.InjectedBearerToken != "" {
		d.add(section, "context", statusPass, "file-less session ($JENTIC_BEARER_TOKEN)", "")
		return
	}
	name := cfg.ActiveContext
	cctx, ok := cfg.Contexts[name]
	if !ok {
		d.add(section, "context", statusWarn, fmt.Sprintf("active context %q is not defined", name),
			"run `jentic context use <name>`, then re-run `jentic setup`")
		return
	}
	if _, envOK := cfg.Environments[cctx.Environment]; !envOK {
		d.add(section, "context", statusWarn,
			fmt.Sprintf("context %q references undefined environment %q", name, cctx.Environment),
			"fix ~/.config/jentic/config.yaml or re-run `jentic register`")
		return
	}
	if _, idOK := cfg.Identities[cctx.Identity]; !idOK {
		d.add(section, "context", statusWarn,
			fmt.Sprintf("context %q references undefined identity %q", name, cctx.Identity),
			"fix ~/.config/jentic/config.yaml or re-run `jentic register`")
		return
	}
	d.add(section, "context", statusPass, fmt.Sprintf("%s (identity %q, environment %q)", name, cctx.Identity, cctx.Environment), "")
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
