// The sudo-shim isolation flow assembles privileged Unix commands (sudo,
// install, sysadminctl/useradd) and POSIX key material — Unix-only, like the
// feature itself.
//
//go:build !windows

package localagentcmd

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"

	sdkconfig "github.com/jentic/jentic-one/cli/client/config"
	"github.com/jentic/jentic-one/cli/internal/cli/clictx"
	"github.com/jentic/jentic-one/cli/internal/localagent"
)

// TestSudoShimPrivilegedStepsOrder pins the load-bearing ordering of the
// privileged plan (the CRITICAL from the 2-E3 self-review): the service home
// is created FIRST (root-side, 0700, no operator grant), the context material
// lands via `sudo install` NEXT (the operator never writes into the home
// itself), and the NOPASSWD sudoers rule comes LAST — it must not exist
// before the material it grants access to is in place.
func TestSudoShimPrivilegedStepsOrder(t *testing.T) {
	user := localagent.ServiceUserName("cursor")
	home := localagent.ServiceHomeDir(user)
	mat := &exportMaterial{
		configYAML: []byte("a: 1\n"),
		relDirs:    []string{".config/jentic", ".config/jentic/keys"},
	}
	rule := localagent.McpSudoersRule("alice", user, "/abs/jentic", "cursor")

	steps := sudoShimPrivilegedSteps(true, user, home, "/tmp/staging", mat, rule)
	var kinds []string
	for _, s := range steps {
		joined := strings.Join(s.Cmd.Args, " ")
		switch {
		case strings.Contains(joined, "sysadminctl") || strings.Contains(joined, "useradd") ||
			strings.Contains(joined, "mkdir") || strings.Contains(joined, "chmod") || strings.Contains(joined, "chown"):
			kinds = append(kinds, "create")
		case strings.Contains(joined, "install -d") || strings.Contains(joined, "install -o"):
			kinds = append(kinds, "export")
		case strings.Contains(joined, "sudoers"):
			kinds = append(kinds, "sudoers")
		default:
			t.Fatalf("unclassified step %q: %s", s.What, joined)
		}
	}
	// Strictly monotone create → export → sudoers, with sudoers dead last.
	order := map[string]int{"create": 0, "export": 1, "sudoers": 2}
	for i := 1; i < len(kinds); i++ {
		if order[kinds[i]] < order[kinds[i-1]] {
			t.Fatalf("privileged plan out of order at step %d: %v", i, kinds)
		}
	}
	if kinds[0] != "create" || kinds[len(kinds)-1] != "sudoers" {
		t.Fatalf("plan must start with home creation and end with the sudoers rule: %v", kinds)
	}
	if !strings.Contains(strings.Join(kinds, " "), "export") {
		t.Fatalf("plan must contain export installs: %v", kinds)
	}

	// Reused account: no create steps, export still before sudoers.
	reuse := sudoShimPrivilegedSteps(false, user, home, "/tmp/staging", mat, rule)
	if strings.Contains(strings.Join(reuse[0].Cmd.Args, " "), "sysadminctl") {
		t.Fatal("reuse plan must not re-create the account")
	}
	if last := reuse[len(reuse)-1]; !strings.Contains(strings.Join(last.Cmd.Args, " "), "sudoers") {
		t.Fatalf("reuse plan must still end with the sudoers rule: %q", last.What)
	}
}

// exportTestState wires a fake active context (identity/env "agent-a"/"prod")
// into ctx and returns it. The XDG dirs come from testApp's fresh
// XDG_CONFIG_HOME; XDG_STATE_HOME is isolated here.
func exportTestState(t *testing.T) context.Context {
	t.Helper()
	t.Setenv("XDG_STATE_HOME", t.TempDir())
	return clictx.WithActiveState(t.Context(), &clictx.ActiveState{
		ResolvedState: &sdkconfig.ResolvedState{
			IdentityName:    "agent-a",
			EnvironmentName: "prod",
			BaseURL:         "https://api.example.test",
		},
	})
}

// TestBuildExportMaterialRequiresKey is the loud-failure guard from the
// self-review: a context whose signing key is missing on disk must FAIL the
// export (a keyless hand-off would leave the service account looking
// provisioned but unable to authenticate) — never silently skip the copy.
func TestBuildExportMaterialRequiresKey(t *testing.T) {
	app := testApp(t)
	ctx := exportTestState(t)

	_, skip, err := app.buildExportMaterial(ctx)
	if skip || err == nil {
		t.Fatalf("skip=%v err=%v, want loud error for a missing key", skip, err)
	}
	if !strings.Contains(err.Error(), "no signing key") {
		t.Errorf("error should name the missing key: %v", err)
	}
}

// TestBuildExportMaterialWithKey: with the key on disk the material renders —
// config.yaml plus the key file, laid out home-relative, and the staging
// render produces exactly those files under 0700/0600.
func TestBuildExportMaterialWithKey(t *testing.T) {
	app := testApp(t)
	ctx := exportTestState(t)
	keyPath := filepath.Join(os.Getenv("XDG_CONFIG_HOME"), "jentic", "keys", "agent-a_prod.key")
	if err := os.MkdirAll(filepath.Dir(keyPath), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(keyPath, []byte("PEM"), 0o600); err != nil {
		t.Fatal(err)
	}

	mat, skip, err := app.buildExportMaterial(ctx)
	if err != nil || skip {
		t.Fatalf("skip=%v err=%v", skip, err)
	}
	files := strings.Join(mat.relFiles(), "\n")
	if !strings.Contains(files, filepath.Join(".config", "jentic", "config.yaml")) ||
		!strings.Contains(files, filepath.Join(".config", "jentic", "keys", "agent-a_prod.key")) {
		t.Errorf("relFiles = %v", mat.relFiles())
	}

	staging, err := app.renderExportStaging(mat)
	if staging != "" {
		defer os.RemoveAll(staging)
	}
	if err != nil {
		t.Fatal(err)
	}
	if info, err := os.Stat(staging); err != nil || info.Mode().Perm() != 0o700 {
		t.Errorf("staging dir mode = %v (%v), want 0700", info.Mode().Perm(), err)
	}
	key := filepath.Join(staging, ".config", "jentic", "keys", "agent-a_prod.key")
	if data, err := os.ReadFile(key); err != nil || string(data) != "PEM" {
		t.Errorf("staged key = %q (%v)", data, err)
	}
	if _, err := os.Stat(filepath.Join(staging, ".config", "jentic", "config.yaml")); err != nil {
		t.Errorf("staged config missing: %v", err)
	}
}
