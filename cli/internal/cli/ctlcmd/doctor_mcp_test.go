package ctlcmd

// Tests for doctor's MCP section (2-E3): the four legs an auto-registered MCP
// entry stands on — binary, CA bundle, pinned context, broker. No live sudo,
// no live broker: only the deterministic checks are unit-tested here (the
// broker probe reuses serverinfo.Probe, covered by the Server checks' tests).

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"

	sdkconfig "github.com/jentic/jentic-one/cli/client/config"
)

// seedAgentConfig writes a minimal config.yaml into the (already isolated)
// XDG_CONFIG_HOME and returns the parsed form (what checkMCP passes around).
// Call testApp first: it points XDG_CONFIG_HOME at a fresh temp dir.
func seedAgentConfig(t *testing.T, yaml string) *sdkconfig.Config {
	t.Helper()
	t.Setenv("JENTIC_BASE_URL", "")
	t.Setenv("JENTIC_BEARER_TOKEN", "")
	dir := filepath.Join(os.Getenv("XDG_CONFIG_HOME"), "jentic")
	if err := os.MkdirAll(dir, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "config.yaml"), []byte(yaml), 0o600); err != nil {
		t.Fatal(err)
	}
	cfg, err := sdkconfig.Load()
	if err != nil {
		t.Fatalf("load seeded config: %v", err)
	}
	return cfg
}

func TestDoctorMCPBinaryMissingIsWarn(t *testing.T) {
	t.Setenv("PATH", t.TempDir()) // guaranteed-absent jentic
	d := &doctor{app: testApp(t), ctx: context.Background()}
	d.checkMCPBinary()

	if len(d.checks) != 1 || d.checks[0].status != statusWarn {
		t.Fatalf("missing jentic binary should be a single warn row (CI-safe), got %+v", d.checks)
	}
	if d.failed() != 0 {
		t.Error("a missing jentic binary must not flip doctor's exit code")
	}
}

func TestDoctorMCPCACertReadablePassesAndDanglingFails(t *testing.T) {
	tmp := t.TempDir()
	ca := filepath.Join(tmp, "ca.pem")
	if err := os.WriteFile(ca, []byte("cert"), 0o600); err != nil {
		t.Fatal(err)
	}
	cfg := &sdkconfig.Config{Environments: map[string]sdkconfig.Env{
		"good":    {BaseURL: "https://a.test", CACertPath: ca},
		"broken":  {BaseURL: "https://b.test", CACertPath: filepath.Join(tmp, "gone.pem")},
		"no-cert": {BaseURL: "https://c.test"}, // no row at all
	}}

	d := &doctor{app: testApp(t), ctx: context.Background()}
	d.checkMCPCACerts(cfg)

	byName := map[string]checkStatus{}
	for _, c := range d.checks {
		byName[c.name] = c.status
	}
	if got, ok := byName["ca_cert_path (good)"]; !ok || got != statusPass {
		t.Errorf("readable ca_cert_path should pass, got %+v", d.checks)
	}
	if got, ok := byName["ca_cert_path (broken)"]; !ok || got != statusFail {
		t.Errorf("dangling ca_cert_path is broken config and should FAIL, got %+v", d.checks)
	}
	if _, ok := byName["ca_cert_path (no-cert)"]; ok {
		t.Error("environments without a ca_cert_path must not emit a row")
	}
}

func TestDoctorMCPContextValidPasses(t *testing.T) {
	app := testApp(t)
	cfg := seedAgentConfig(t, `
active_context: dev
contexts:
  dev: {environment: local, identity: me, mode: agent}
environments:
  local: {base_url: https://example.test}
identities:
  me: {type: agent}
`)
	d := &doctor{app: app, ctx: context.Background()}
	d.checkMCPContext(cfg)

	if len(d.checks) != 1 || d.checks[0].status != statusPass {
		t.Fatalf("a fully-defined active context should pass, got %+v", d.checks)
	}
}

func TestDoctorMCPContextUndefinedEnvironmentWarns(t *testing.T) {
	app := testApp(t)
	cfg := seedAgentConfig(t, `
active_context: dev
contexts:
  dev: {environment: local, identity: me, mode: agent}
environments:
  local: {base_url: https://example.test}
identities:
  me: {type: agent}
`)
	// Simulate the strand-every-entry case: the context's environment was
	// removed after the entries were written.
	cctx := cfg.Contexts["dev"]
	cctx.Environment = "gone"
	cfg.Contexts["dev"] = cctx

	d := &doctor{app: app, ctx: context.Background()}
	d.checkMCPContext(cfg)

	if len(d.checks) != 1 || d.checks[0].status != statusWarn {
		t.Fatalf("an undefined environment should warn, got %+v", d.checks)
	}
	if !strings.Contains(d.checks[0].detail, "undefined environment") {
		t.Errorf("warn must be for the undefined environment, got %q", d.checks[0].detail)
	}
}

func TestDoctorMCPContextMissingWarnsWithSetupHint(t *testing.T) {
	t.Setenv("XDG_CONFIG_HOME", t.TempDir())
	t.Setenv("JENTIC_BASE_URL", "")
	t.Setenv("JENTIC_BEARER_TOKEN", "")

	d := &doctor{app: testApp(t), ctx: context.Background()}
	d.checkMCPContext(&sdkconfig.Config{})

	if len(d.checks) != 1 || d.checks[0].status != statusWarn {
		t.Fatalf("no active context should warn, got %+v", d.checks)
	}
	if d.checks[0].hint == "" {
		t.Error("the no-context row should carry a remediation hint")
	}
}
