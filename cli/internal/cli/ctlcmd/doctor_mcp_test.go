package ctlcmd

// Tests for doctor's MCP section (2-E3): the four legs an auto-registered MCP
// entry stands on — binary, CA bundle, pinned context, broker — judged from
// the entries ACTUALLY WRITTEN into the runtime configs, not from PATH or the
// active context. No live sudo, no live broker: only the deterministic checks
// are unit-tested here (the broker probe reuses serverinfo.Probe, covered by
// the Server checks' tests).

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"

	sdkconfig "github.com/jentic/jentic-one/cli/client/config"
	"github.com/jentic/jentic-one/cli/internal/mcpcfg"
)

// writtenEntry builds the parsed form of one runtime's written entry.
func writtenEntry(rt mcpcfg.Runtime, entry mcpcfg.Entry) mcpWrittenEntry {
	return mcpWrittenEntry{runtime: rt, path: "/ignored", entry: entry}
}

// validCfg is a config where context "dev" fully resolves.
func validCfg() *sdkconfig.Config {
	return &sdkconfig.Config{
		Contexts:     map[string]sdkconfig.Context{"dev": {Environment: "local", Identity: "me", Mode: "agent"}},
		Environments: map[string]sdkconfig.Env{"local": {BaseURL: "https://example.test"}},
		Identities:   map[string]sdkconfig.Identity{"me": {Type: "agent"}},
	}
}

// TestDoctorMCPNoEntriesIsSingleWarn: with nothing registered there is
// nothing truthful to probe — one CI-safe warn row pointing at setup.
func TestDoctorMCPNoEntriesIsSingleWarn(t *testing.T) {
	d := &doctor{app: testApp(t), ctx: context.Background()}
	d.checkMCPEntries(validCfg(), nil)

	if len(d.checks) != 1 || d.checks[0].status != statusWarn {
		t.Fatalf("no entries should be a single warn row, got %+v", d.checks)
	}
	if !strings.Contains(d.checks[0].hint, "jentic setup") {
		t.Errorf("row should hint at setup, got %q", d.checks[0].hint)
	}
	if d.failed() != 0 {
		t.Error("no entries must not flip doctor's exit code")
	}
}

// TestDoctorMCPEntryBinaryChecksWrittenPath: the binary row judges the path
// the ENTRY pins — present passes, missing warns — for both plain and
// sudo-shim shapes. A `jentic` on PATH is irrelevant (and deliberately absent
// here).
func TestDoctorMCPEntryBinaryChecksWrittenPath(t *testing.T) {
	t.Setenv("PATH", t.TempDir()) // guaranteed-absent jentic on PATH
	realBin := filepath.Join(t.TempDir(), "jentic")
	if err := os.WriteFile(realBin, []byte("#!"), 0o755); err != nil {
		t.Fatal(err)
	}

	d := &doctor{app: testApp(t), ctx: context.Background()}
	d.checkMCPEntries(validCfg(), []mcpWrittenEntry{
		writtenEntry(mcpcfg.RuntimeCursor, mcpcfg.PlainEntry(realBin, "dev")),
		writtenEntry(mcpcfg.RuntimeCodex, mcpcfg.SudoShimEntry("_jentic-codex", "/gone/jentic", "dev")),
	})

	byName := map[string]checkStatus{}
	for _, c := range d.checks {
		byName[c.name] = c.status
	}
	if byName["binary (cursor)"] != statusPass {
		t.Errorf("existing pinned binary should pass, got %+v", d.checks)
	}
	if byName["binary (codex)"] != statusWarn {
		t.Errorf("missing pinned binary should warn (CI-safe), got %+v", d.checks)
	}
	if d.failed() != 0 {
		t.Error("a stranded entry must not flip doctor's exit code")
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

// TestDoctorMCPEntryContextValidPasses: the context row validates the name
// the entry PINS against the config.
func TestDoctorMCPEntryContextValidPasses(t *testing.T) {
	d := &doctor{app: testApp(t), ctx: context.Background()}
	d.checkMCPEntryContext(validCfg(), writtenEntry(mcpcfg.RuntimeCursor, mcpcfg.PlainEntry("/abs/jentic", "dev")))

	if len(d.checks) != 1 || d.checks[0].status != statusPass {
		t.Fatalf("a fully-resolving pinned context should pass, got %+v", d.checks)
	}
}

// TestDoctorMCPEntryContextIgnoresActiveContext: switching the ACTIVE context
// must not flip the row — the entry still pins "dev", and that is what the
// runtime will spawn.
func TestDoctorMCPEntryContextIgnoresActiveContext(t *testing.T) {
	cfg := validCfg()
	cfg.ActiveContext = "somewhere-else" // undefined on purpose

	d := &doctor{app: testApp(t), ctx: context.Background()}
	d.checkMCPEntryContext(cfg, writtenEntry(mcpcfg.RuntimeCursor, mcpcfg.PlainEntry("/abs/jentic", "dev")))

	if len(d.checks) != 1 || d.checks[0].status != statusPass {
		t.Fatalf("the pinned context resolves — the active context is irrelevant, got %+v", d.checks)
	}
}

func TestDoctorMCPEntryContextUndefinedEnvironmentWarns(t *testing.T) {
	cfg := validCfg()
	// Simulate the strand-every-entry case: the pinned context's environment
	// was removed after the entries were written.
	cctx := cfg.Contexts["dev"]
	cctx.Environment = "gone"
	cfg.Contexts["dev"] = cctx

	d := &doctor{app: testApp(t), ctx: context.Background()}
	d.checkMCPEntryContext(cfg, writtenEntry(mcpcfg.RuntimeCursor, mcpcfg.PlainEntry("/abs/jentic", "dev")))

	if len(d.checks) != 1 || d.checks[0].status != statusWarn {
		t.Fatalf("an undefined environment should warn, got %+v", d.checks)
	}
	if !strings.Contains(d.checks[0].detail, "undefined environment") {
		t.Errorf("warn must be for the undefined environment, got %q", d.checks[0].detail)
	}
}

func TestDoctorMCPEntryContextUndefinedNameWarns(t *testing.T) {
	d := &doctor{app: testApp(t), ctx: context.Background()}
	d.checkMCPEntryContext(&sdkconfig.Config{},
		writtenEntry(mcpcfg.RuntimeCursor, mcpcfg.PlainEntry("/abs/jentic", "deleted-ctx")))

	if len(d.checks) != 1 || d.checks[0].status != statusWarn {
		t.Fatalf("an undefined pinned context should warn, got %+v", d.checks)
	}
	if d.checks[0].hint == "" {
		t.Error("the row should carry a remediation hint")
	}
}

// TestReadMCPEntriesFindsWrittenConfigs: the survey parses real written
// files back into entries (cursor JSON here; the parsers themselves are
// golden-tested in mcpcfg).
func TestReadMCPEntriesFindsWrittenConfigs(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	if _, err := mcpcfg.WriteJSONEntry(mcpcfg.RuntimeCursor,
		mcpcfg.CursorConfigPath(home), mcpcfg.PlainEntry("/abs/jentic", "dev")); err != nil {
		t.Fatal(err)
	}

	entries := readMCPEntries()
	if len(entries) != 1 || entries[0].runtime != mcpcfg.RuntimeCursor {
		t.Fatalf("entries = %+v, want the cursor entry", entries)
	}
	if entries[0].entry.PinnedContext() != "dev" {
		t.Errorf("parsed entry = %+v", entries[0].entry)
	}
}
