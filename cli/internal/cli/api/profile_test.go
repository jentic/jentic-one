package api

import (
	"bytes"
	"strings"
	"testing"

	"github.com/jentic/jentic-one/cli/internal/config"
	"github.com/jentic/jentic-one/cli/internal/localagent"
	"github.com/jentic/jentic-one/cli/internal/profile"
	"github.com/jentic/jentic-one/cli/internal/theme"
)

// seedProfile creates a profile directory, optionally writing agent metadata so
// the listing shows a registered row.
func seedProfile(t *testing.T, app *app, name, agentID string) {
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
	cfg.SetAgentAccount(config.AgentAccount{User: "mybot-agent", AccountCreated: true, Enabled: true, ConfigDir: agentRoot})
	if err := cfg.Save(app.Paths); err != nil {
		t.Fatalf("save cfg: %v", err)
	}

	if err := app.profileList(); err != nil {
		t.Fatalf("profileList: %v", err)
	}
	got := app.Out.(*bytes.Buffer).String()
	for _, want := range []string{"botprofile", "agnt_bot", "(agent)"} {
		if !strings.Contains(got, want) {
			t.Errorf("list output missing agent-owned profile marker %q\n---\n%s", want, got)
		}
	}
}

func TestRenderAccessTreeCollapsesNestedAndMarksSubtree(t *testing.T) {
	out := renderAccessTree([]localagent.SessionDir{
		{Path: "/opt/data", Kind: localagent.AccessReadWrite},
		{Path: "/opt/data/inner", Kind: localagent.AccessReadWrite}, // nested → folded away
		{Path: "/Users/Shared/bot", Kind: localagent.AccessReadWrite},
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

// A read-only exec route renders after the read/write grants under a
// "(read-only)" tag, and is NOT folded into a read/write entry even if nested.
func TestRenderAccessTreeSeparatesReadOnlyRoutes(t *testing.T) {
	out := renderAccessTree([]localagent.SessionDir{
		{Path: "/Users/Shared/bot", Kind: localagent.AccessReadWrite},
		{Path: "/usr/bin", Kind: localagent.AccessReadOnly},
	})
	if !strings.Contains(out, "/usr/bin/*") || !strings.Contains(out, "read-only") {
		t.Errorf("expected read-only exec route with tag:\n%s", out)
	}
	// Ordering: the read/write grant comes before the read-only route.
	if rw, ro := strings.Index(out, "/Users/Shared/bot"), strings.Index(out, "/usr/bin"); rw < 0 || ro < 0 || ro < rw {
		t.Errorf("read/write grant must render before read-only route (rw@%d ro@%d)\n%s", rw, ro, out)
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
	cfg.SetAgentAccount(config.AgentAccount{
		User:           "bot-agent",
		AccountCreated: true,
		Enabled:        true,
		HomeDir:        "/Users/Shared/bot-agent",
		GrantedDirs:    []string{"/opt/data", "/Users/alice/projects/api"},
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
		// The read-only executable routes the sandbox mounts are shown too, so the
		// operator sees the full set the agent can reach. /usr/bin exists on every
		// dev box and is a sanctioned exec route.
		"/usr/bin/*",
		"read-only",
		// The access tree always tells the operator how to take access back. Grants
		// are account-scoped (one set for every agent binary), so the hint is generic
		// over which `<agent>` binary is named.
		"jentic run <agent> --revoke",
	} {
		if !strings.Contains(got, want) {
			t.Errorf("profile view missing %q\n---\n%s", want, got)
		}
	}
}

// A bare `jentic profile view` (no name) resolves the currently active profile,
// so an agent can see its own access map without knowing its profile name.
func TestProfileViewNoNameShowsActive(t *testing.T) {
	app := testApp(t)
	seedProfile(t, app, "bot", "agnt_1")
	if err := config.SetDefaultProfile(app.Paths, "bot"); err != nil {
		t.Fatalf("set default: %v", err)
	}
	cfg, err := config.Load(app.Paths)
	if err != nil {
		t.Fatalf("load cfg: %v", err)
	}
	cfg.SetAgentAccount(config.AgentAccount{
		User:           "bot-agent",
		AccountCreated: true,
		Enabled:        true,
		HomeDir:        "/Users/Shared/bot-agent",
		GrantedDirs:    []string{"/opt/data"},
	})
	if err := cfg.Save(app.Paths); err != nil {
		t.Fatalf("save cfg: %v", err)
	}

	// No argument: must not error, and must show the active profile's access map.
	if err := app.profileView(""); err != nil {
		t.Fatalf("profileView(\"\"): %v", err)
	}
	got := app.Out.(*bytes.Buffer).String()
	for _, want := range []string{"Filesystem access", "/opt/data/*", "jentic run <agent> --revoke"} {
		if !strings.Contains(got, want) {
			t.Errorf("bare profile view missing %q\n---\n%s", want, got)
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

// Switching to an agent-owned profile checks it out for run-as: the operator's
// own default_profile is set to that name, so subsequent profile-scoped commands
// resolve it from the agent store and run in-process as the agent.
func TestProfileSwitchAgentOwnedChecksOut(t *testing.T) {
	app := testApp(t)
	seedProfile(t, app, "default", "")

	agentRoot := t.TempDir()
	ap, err := profile.Open(config.Paths{Root: agentRoot}, "botprofile")
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
	cfg.SetAgentAccount(config.AgentAccount{User: "mybot-agent", AccountCreated: true, Enabled: true, ConfigDir: agentRoot})
	if err := cfg.Save(app.Paths); err != nil {
		t.Fatalf("save cfg: %v", err)
	}

	if err := app.profileSwitch(nil, "botprofile"); err != nil {
		t.Fatalf("switch to agent-owned profile: %v", err)
	}
	reloaded, err := config.Load(app.Paths)
	if err != nil {
		t.Fatalf("reload cfg: %v", err)
	}
	if reloaded.DefaultProfile != "botprofile" {
		t.Errorf("agent-owned profile should become the operator default, got %q", reloaded.DefaultProfile)
	}

	// The active profile now resolves to the agent store, so a session opens
	// against the agent home rather than the operator's ~/.jentic.
	paths, err := app.sessionPaths("botprofile")
	if err != nil {
		t.Fatalf("sessionPaths: %v", err)
	}
	if paths.Root != agentRoot {
		t.Errorf("sessionPaths for a checked-out agent profile = %q, want agent home %q", paths.Root, agentRoot)
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

	if it := app.loadProfileItem(profileRef{name: "fresh", paths: app.Paths}); it.registered {
		t.Errorf("unregistered profile marked registered: %+v", it)
	}
	it := app.loadProfileItem(profileRef{name: "reg", paths: app.Paths})
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
	it := app.loadProfileItem(profileRef{name: "keyed", paths: app.Paths})
	if !it.registered || !it.apiKey || it.keyLabel != "jak_…1234" {
		t.Errorf("api-key item not loaded: %+v", it)
	}
}
