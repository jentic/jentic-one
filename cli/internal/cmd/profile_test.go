package cmd

import (
	"bytes"
	"strings"
	"testing"

	"github.com/jentic/jentic-one/cli/internal/config"
	"github.com/jentic/jentic-one/cli/internal/profile"
	"github.com/jentic/jentic-one/cli/internal/theme"
)

// seedProfile creates a profile directory, optionally writing agent metadata so
// the listing shows a registered row.
func seedProfile(t *testing.T, app *App, name, agentID string) {
	t.Helper()
	p, err := profile.Open(app.Paths, name)
	if err != nil {
		t.Fatalf("open profile %q: %v", name, err)
	}
	if agentID != "" {
		if err := p.SaveMeta(&profile.Meta{AgentID: agentID, BaseURL: "http://example:9000"}); err != nil {
			t.Fatalf("save meta %q: %v", name, err)
		}
	}
}

func TestProfileListMarksActive(t *testing.T) {
	app := testApp(t)
	seedProfile(t, app, "default", "")
	seedProfile(t, app, "work", "agnt_123")
	if err := config.SetDefaultProfile(app.Paths, "work"); err != nil {
		t.Fatalf("set default: %v", err)
	}

	if err := app.profileList(); err != nil {
		t.Fatalf("profileList: %v", err)
	}
	got := app.Out.(*bytes.Buffer).String()
	for _, want := range []string{"default", "work", "agnt_123", theme.SelectOn, theme.SelectOff, "active:"} {
		if !strings.Contains(got, want) {
			t.Errorf("list output missing %q\n---\n%s", want, got)
		}
	}
}

func TestProfileListDiscoversAgentOwnedProfiles(t *testing.T) {
	app := testApp(t)
	seedProfile(t, app, "default", "")

	// An agent registered as its own Unix user writes its identity into its own
	// home (<ConfigDir>/profiles), not the operator's ~/.jentic. Simulate that by
	// pointing a configured agent at a separate config root and seeding a profile
	// there.
	agentRoot := t.TempDir()
	agentPaths := config.Paths{Root: agentRoot}
	ap, err := profile.Open(agentPaths, "botprofile")
	if err != nil {
		t.Fatalf("open agent profile: %v", err)
	}
	if err := ap.SaveMeta(&profile.Meta{AgentID: "agnt_bot", BaseURL: "http://bot:9000"}); err != nil {
		t.Fatalf("save agent meta: %v", err)
	}
	cfg, err := config.Load(app.Paths)
	if err != nil {
		t.Fatalf("load cfg: %v", err)
	}
	cfg.SetLocalAgent("mybot", config.LocalAgent{User: "mybot-agent", ConfigDir: agentRoot})
	if err := cfg.Save(app.Paths); err != nil {
		t.Fatalf("save cfg: %v", err)
	}

	if err := app.profileList(); err != nil {
		t.Fatalf("profileList: %v", err)
	}
	got := app.Out.(*bytes.Buffer).String()
	for _, want := range []string{"botprofile", "agnt_bot", "(agent: mybot)"} {
		if !strings.Contains(got, want) {
			t.Errorf("list output missing agent-owned profile marker %q\n---\n%s", want, got)
		}
	}
}

func TestRenderAccessTreeCollapsesNestedAndMarksSubtree(t *testing.T) {
	out := renderAccessTree([]string{
		"/opt/data",
		"/opt/data/inner", // nested under /opt/data → folded away
		"/Users/Shared/bot",
	})
	if !strings.Contains(out, "/opt/data/*") {
		t.Errorf("expected whole-subtree marker on /opt/data:\n%s", out)
	}
	if strings.Contains(out, "/opt/data/inner") {
		t.Errorf("nested grant should be folded into its parent:\n%s", out)
	}
	if !strings.Contains(out, "/Users/Shared/bot/*") {
		t.Errorf("expected the agent home in the tree:\n%s", out)
	}
}

func TestRenderAccessTreeEmpty(t *testing.T) {
	if out := renderAccessTree(nil); !strings.Contains(out, "no directories") {
		t.Errorf("expected empty marker, got:\n%s", out)
	}
}

func TestProfileViewShowsAccessTree(t *testing.T) {
	app := testApp(t)
	seedProfile(t, app, "bot", "agnt_1")

	cfg, err := config.Load(app.Paths)
	if err != nil {
		t.Fatalf("load cfg: %v", err)
	}
	cfg.SetLocalAgent("bot", config.LocalAgent{
		User:        "bot-agent",
		HomeDir:     "/Users/Shared/bot-agent",
		GrantedDirs: []string{"/opt/data", "/Users/alice/projects/api"},
	})
	if err := cfg.Save(app.Paths); err != nil {
		t.Fatalf("save cfg: %v", err)
	}

	if err := app.profileView("bot"); err != nil {
		t.Fatalf("profileView: %v", err)
	}
	got := app.Out.(*bytes.Buffer).String()
	for _, want := range []string{
		"Filesystem access",
		"/Users/Shared/bot-agent/*",
		"/opt/data/*",
		"/Users/alice/projects/api/*",
	} {
		if !strings.Contains(got, want) {
			t.Errorf("profile view missing %q\n---\n%s", want, got)
		}
	}
}

func TestProfileViewMissingErrors(t *testing.T) {
	app := testApp(t)
	seedProfile(t, app, "default", "")
	err := app.profileView("ghost")
	if err == nil || !strings.Contains(err.Error(), "does not exist") {
		t.Fatalf("expected does-not-exist error, got %v", err)
	}
}

func TestProfileListEmpty(t *testing.T) {
	app := testApp(t)
	if err := app.profileList(); err != nil {
		t.Fatalf("profileList: %v", err)
	}
	if got := app.Out.(*bytes.Buffer).String(); !strings.Contains(got, "no profiles yet") {
		t.Errorf("expected empty hint, got:\n%s", got)
	}
}

func TestProfileUsePersists(t *testing.T) {
	app := testApp(t)
	seedProfile(t, app, "default", "")
	seedProfile(t, app, "other", "agnt_9")

	if err := app.profileSwitch(nil, "other"); err != nil {
		t.Fatalf("profileSwitch: %v", err)
	}
	cfg, err := config.Load(app.Paths)
	if err != nil {
		t.Fatalf("load: %v", err)
	}
	if cfg.DefaultProfile != "other" {
		t.Errorf("DefaultProfile = %q, want other", cfg.DefaultProfile)
	}
	if got := app.Out.(*bytes.Buffer).String(); !strings.Contains(got, "Active profile set") {
		t.Errorf("missing confirmation, got:\n%s", got)
	}
}

func TestProfileUseMissingErrors(t *testing.T) {
	app := testApp(t)
	seedProfile(t, app, "default", "")
	err := app.profileSwitch(nil, "ghost")
	if err == nil || !strings.Contains(err.Error(), "does not exist") {
		t.Fatalf("expected does-not-exist error, got %v", err)
	}
}

// In the test runner stdin is not a TTY, so a bare switch with profiles present
// must error rather than block on an interactive picker.
func TestProfileSwitchNoNameNonTTYErrors(t *testing.T) {
	app := testApp(t)
	seedProfile(t, app, "default", "")
	err := app.profileSwitch(nil, "")
	if err == nil || !strings.Contains(err.Error(), "no profile name given") {
		t.Fatalf("expected no-name error, got %v", err)
	}
}

func TestProfileSwitchEmptyListErrors(t *testing.T) {
	app := testApp(t)
	err := app.profileSwitch(nil, "")
	if err == nil || !strings.Contains(err.Error(), "no profiles found") {
		t.Fatalf("expected empty-list error, got %v", err)
	}
}

func TestLoadProfileItem(t *testing.T) {
	app := testApp(t)
	seedProfile(t, app, "fresh", "")
	seedProfile(t, app, "reg", "agnt_42")

	if it := app.loadProfileItem("fresh"); it.registered {
		t.Errorf("unregistered profile marked registered: %+v", it)
	}
	it := app.loadProfileItem("reg")
	if !it.registered || it.agentID != "agnt_42" || it.baseURL != "http://example:9000" {
		t.Errorf("registered profile not loaded: %+v", it)
	}
}

func TestProfileDetailView(t *testing.T) {
	unreg := profileDetailView(profileItem{name: "fresh"})
	if !strings.Contains(unreg, "not registered") {
		t.Errorf("unregistered detail missing hint: %q", unreg)
	}
	reg := profileDetailView(profileItem{name: "reg", registered: true, baseURL: "http://x", agentID: "agnt_42", token: "valid"})
	for _, want := range []string{"agnt_42", "http://x", "valid"} {
		if !strings.Contains(reg, want) {
			t.Errorf("registered detail missing %q: %q", want, reg)
		}
	}

	keyed := profileDetailView(profileItem{name: "keyed", registered: true, apiKey: true, baseURL: "http://x", keyLabel: "jak_…1234"})
	for _, want := range []string{"api-key", "jak_…1234", "http://x"} {
		if !strings.Contains(keyed, want) {
			t.Errorf("api-key detail missing %q: %q", want, keyed)
		}
	}
}

func TestLoadProfileItemAPIKey(t *testing.T) {
	app := testApp(t)
	p, err := profile.Open(app.Paths, "keyed")
	if err != nil {
		t.Fatalf("open: %v", err)
	}
	if err := p.SaveMeta(&profile.Meta{BaseURL: "http://x", AuthMode: profile.AuthModeAPIKey}); err != nil {
		t.Fatalf("save meta: %v", err)
	}
	if err := p.SaveAPIKey("jak_abcdefgh1234"); err != nil {
		t.Fatalf("save key: %v", err)
	}
	it := app.loadProfileItem("keyed")
	if !it.registered || !it.apiKey || it.keyLabel != "jak_…1234" {
		t.Errorf("api-key item not loaded: %+v", it)
	}
}
