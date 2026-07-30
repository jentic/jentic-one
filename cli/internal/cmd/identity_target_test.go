package cmd

import (
	"bytes"
	"os"
	"path/filepath"
	"syscall"
	"testing"

	"github.com/jentic/jentic-one/cli/internal/config"
	"github.com/jentic/jentic-one/cli/internal/profile"
)

// TestResolveIdentityTargetNoAccount returns an operator-owned target when no
// agent account is set up: identities land in the operator's own ~/.jentic.
func TestResolveIdentityTargetNoAccount(t *testing.T) {
	app := testApp(t)
	cfg := &config.FileConfig{}
	target := app.resolveIdentityTarget(cfg)
	if target.agentOwned {
		t.Fatal("no account → target must be operator-owned")
	}
	if target.paths.Root != app.Paths.Root {
		t.Errorf("operator target root = %q, want %q", target.paths.Root, app.Paths.Root)
	}
}

// TestResolveIdentityTargetWithAccount routes identities into the shared agent
// home once an account exists — regardless of whether it was created this run.
func TestResolveIdentityTargetWithAccount(t *testing.T) {
	app := testApp(t)
	agentRoot := t.TempDir()
	cfg := &config.FileConfig{}
	cfg.SetAgentAccount(config.AgentAccount{
		User: "alice-local-agent", AccountCreated: true, Enabled: true, ConfigDir: agentRoot,
	})
	target := app.resolveIdentityTarget(cfg)
	if !target.agentOwned {
		t.Fatal("account present → target must be agent-owned")
	}
	if target.paths.Root != agentRoot || target.configDir != agentRoot || target.agentUser != "alice-local-agent" {
		t.Errorf("agent target = %+v", target)
	}
}

// TestResolveIdentityTargetDeclinedAccount treats a declined account
// (AccountCreated=false) as no account: identities stay operator-owned.
func TestResolveIdentityTargetDeclinedAccount(t *testing.T) {
	app := testApp(t)
	cfg := &config.FileConfig{}
	cfg.SetAgentAccount(config.AgentAccount{User: "alice-local-agent"}) // declined: not created
	if app.resolveIdentityTarget(cfg).agentOwned {
		t.Fatal("a declined account must not route identities into an agent home")
	}
}

// TestCheckOutAgentProfileWritesAgentDefault checks that checking out an
// agent-owned profile sets the AGENT home's default_profile and never the
// operator's own, even when activateOperator is false.
func TestCheckOutAgentProfileWritesAgentDefault(t *testing.T) {
	app := testApp(t)
	agentRoot := t.TempDir()
	target := identityTarget{paths: config.Paths{Root: agentRoot}, agentUser: "a", configDir: agentRoot, agentOwned: true}

	if err := app.checkOutProfile(target, "botprofile", false); err != nil {
		t.Fatalf("checkOutProfile: %v", err)
	}
	agentCfg, err := config.Load(config.Paths{Root: agentRoot})
	if err != nil {
		t.Fatalf("load agent cfg: %v", err)
	}
	if agentCfg.DefaultProfile != "botprofile" {
		t.Errorf("agent default_profile = %q, want botprofile", agentCfg.DefaultProfile)
	}
	// The operator's own default must be untouched.
	opCfg, err := config.Load(app.Paths)
	if err != nil {
		t.Fatalf("load op cfg: %v", err)
	}
	if opCfg.DefaultProfile != "" {
		t.Errorf("operator default_profile changed to %q; check-out must not touch it", opCfg.DefaultProfile)
	}
}

// TestCheckOutOperatorProfileHonoursActivate sets the operator default only when
// activateOperator is true.
func TestCheckOutOperatorProfileHonoursActivate(t *testing.T) {
	app := testApp(t)
	target := identityTarget{paths: app.Paths}

	if err := app.checkOutProfile(target, "work", false); err != nil {
		t.Fatalf("checkOutProfile(no-activate): %v", err)
	}
	cfg, _ := config.Load(app.Paths)
	if cfg.DefaultProfile != "" {
		t.Errorf("no-activate must not set a default, got %q", cfg.DefaultProfile)
	}

	if err := app.checkOutProfile(target, "work", true); err != nil {
		t.Fatalf("checkOutProfile(activate): %v", err)
	}
	cfg, _ = config.Load(app.Paths)
	if cfg.DefaultProfile != "work" {
		t.Errorf("activate must set the operator default, got %q", cfg.DefaultProfile)
	}
}

// TestTranslateOperatorProfileMovesIntoAgentHome copies a pre-existing
// operator-owned profile into the shared agent home and removes the original, so
// enabling isolation carries the existing identity over.
func TestTranslateOperatorProfileMovesIntoAgentHome(t *testing.T) {
	app := testApp(t)
	seedProfile(t, app, "carryover", "agnt_carry")

	agentRoot := t.TempDir()
	target := identityTarget{paths: config.Paths{Root: agentRoot}, agentUser: "a", configDir: agentRoot, agentOwned: true}

	moved, err := app.translateOperatorProfile(target, "carryover")
	if err != nil {
		t.Fatalf("translate: %v", err)
	}
	if !moved {
		t.Fatal("expected a translation to happen")
	}

	// Present in the agent home with its metadata intact.
	ap, err := profile.Open(config.Paths{Root: agentRoot}, "carryover")
	if err != nil {
		t.Fatalf("open agent profile: %v", err)
	}
	meta, err := ap.LoadMeta()
	if err != nil {
		t.Fatalf("load agent meta: %v", err)
	}
	if meta.AgentID != "agnt_carry" {
		t.Errorf("translated meta agent_id = %q, want agnt_carry", meta.AgentID)
	}
	// Gone from the operator store.
	opNames, _ := profile.List(app.Paths)
	for _, n := range opNames {
		if n == "carryover" {
			t.Error("operator original must be removed after translation")
		}
	}
}

// TestCopyTreePinsPermsUnderLooseUmask proves the copy lands with exactly the
// source's perms (0700 dir, 0600 file) even when the process umask is 0, so a
// loose umask can never publish the profile's secrets. Without the explicit
// chmod, MkdirAll/O_CREATE modes are masked by umask.
func TestCopyTreePinsPermsUnderLooseUmask(t *testing.T) {
	old := syscall.Umask(0)
	defer syscall.Umask(old)

	src := t.TempDir()
	if err := os.Mkdir(filepath.Join(src, "prof"), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(src, "prof", "key"), []byte("secret"), 0o600); err != nil {
		t.Fatal(err)
	}

	dst := filepath.Join(t.TempDir(), "prof")
	if err := copyTree(filepath.Join(src, "prof"), dst); err != nil {
		t.Fatalf("copyTree: %v", err)
	}
	di, err := os.Stat(dst)
	if err != nil {
		t.Fatal(err)
	}
	if di.Mode().Perm() != 0o700 {
		t.Errorf("copied dir perm = %o, want 700", di.Mode().Perm())
	}
	fi, err := os.Stat(filepath.Join(dst, "key"))
	if err != nil {
		t.Fatal(err)
	}
	if fi.Mode().Perm() != 0o600 {
		t.Errorf("copied file perm = %o, want 600", fi.Mode().Perm())
	}
}

// TestTranslateOperatorProfileNoClobber never overwrites an identity that already
// exists in the agent home.
func TestTranslateOperatorProfileNoClobber(t *testing.T) {
	app := testApp(t)
	seedProfile(t, app, "dup", "agnt_operator")

	agentRoot := t.TempDir()
	ap, err := profile.Open(config.Paths{Root: agentRoot}, "dup")
	if err != nil {
		t.Fatalf("open agent profile: %v", err)
	}
	if err := ap.SaveMeta(&profile.Meta{AgentID: "agnt_agenthome"}); err != nil {
		t.Fatalf("save agent meta: %v", err)
	}
	target := identityTarget{paths: config.Paths{Root: agentRoot}, agentUser: "a", configDir: agentRoot, agentOwned: true}

	moved, err := app.translateOperatorProfile(target, "dup")
	if err != nil {
		t.Fatalf("translate: %v", err)
	}
	if moved {
		t.Fatal("must not translate over an existing agent-home profile")
	}
	meta, _ := ap.LoadMeta()
	if meta.AgentID != "agnt_agenthome" {
		t.Errorf("agent-home profile clobbered: agent_id = %q", meta.AgentID)
	}
}

// TestTranslateOperatorProfileOperatorTargetNoop is a no-op when the target is not
// agent-owned.
func TestTranslateOperatorProfileOperatorTargetNoop(t *testing.T) {
	app := &App{Paths: config.Paths{Root: t.TempDir()}, Out: &bytes.Buffer{}, Err: &bytes.Buffer{}}
	seedProfile(t, app, "x", "agnt_x")
	moved, err := app.translateOperatorProfile(identityTarget{paths: app.Paths}, "x")
	if err != nil || moved {
		t.Fatalf("operator target must be a no-op, got moved=%v err=%v", moved, err)
	}
}

// TestTranslateOperatorProfileRefusesSymlinkAndKeepsOriginal proves the hand-off is
// all-or-nothing: a symlink inside the operator profile dir aborts the copy, and
// because the copy failed the operator's original must NOT be deleted (the delete is
// irreversible — losing it would take the key/tokens with it).
func TestTranslateOperatorProfileRefusesSymlinkAndKeepsOriginal(t *testing.T) {
	app := testApp(t)
	seedProfile(t, app, "linky", "agnt_link")

	// Plant a symlink inside the operator profile dir.
	profDir := filepath.Join(app.Paths.ProfilesDir(), "linky")
	if err := os.Symlink("/etc/passwd", filepath.Join(profDir, "evil")); err != nil {
		t.Fatalf("plant symlink: %v", err)
	}

	agentRoot := t.TempDir()
	target := identityTarget{paths: config.Paths{Root: agentRoot}, agentUser: "a", configDir: agentRoot, agentOwned: true}

	moved, err := app.translateOperatorProfile(target, "linky")
	if err == nil {
		t.Fatal("expected translation to fail on a symlink in the profile dir")
	}
	if moved {
		t.Fatal("a failed translation must not report moved=true")
	}
	// The operator's original survives the failed copy.
	opNames, _ := profile.List(app.Paths)
	found := false
	for _, n := range opNames {
		if n == "linky" {
			found = true
		}
	}
	if !found {
		t.Error("operator original must be kept when the copy fails")
	}
}
