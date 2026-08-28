package mcpcfg

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func plain() Entry { return PlainEntry("/opt/homebrew/bin/jentic", "cursor") }

// --- JSON merge (cursor / claude-desktop shape) ------------------------------

// TestMergeJSONFreshFile: a new file gets exactly our entry, and merging the
// result again is byte-identical (write twice, diff clean).
func TestMergeJSONFreshFile(t *testing.T) {
	out, changed, err := MergeJSON(nil, plain())
	if err != nil {
		t.Fatal(err)
	}
	if !changed {
		t.Fatal("fresh merge must report changed")
	}
	want := `{
  "mcpServers": {
    "jentic": {
      "args": [
        "mcp",
        "--context",
        "cursor"
      ],
      "command": "/opt/homebrew/bin/jentic"
    }
  }
}
`
	if string(out) != want {
		t.Errorf("fresh file mismatch:\n got: %s\nwant: %s", out, want)
	}

	again, changed, err := MergeJSON(out, plain())
	if err != nil {
		t.Fatal(err)
	}
	if changed {
		t.Errorf("second merge must be a no-op, rewrote to:\n%s", again)
	}
	if string(again) != string(out) {
		t.Errorf("idempotency: second merge differs:\n%s", again)
	}
}

// TestMergeJSONPreservesForeignKeys: sibling servers and unrelated top-level
// keys survive the merge with their values intact.
func TestMergeJSONPreservesForeignKeys(t *testing.T) {
	existing := []byte(`{
  "mcpServers": {
    "github": {"command": "gh-mcp", "args": ["--stdio"], "env": {"TOKEN": "a&b"}}
  },
  "theme": "dark"
}`)
	out, changed, err := MergeJSON(existing, plain())
	if err != nil {
		t.Fatal(err)
	}
	if !changed {
		t.Fatal("merge into a foreign-only file must report changed")
	}
	for _, needle := range []string{`"github"`, `"gh-mcp"`, `"TOKEN": "a&b"`, `"theme": "dark"`, `"jentic"`} {
		if !strings.Contains(string(out), needle) {
			t.Errorf("output lost %s:\n%s", needle, out)
		}
	}

	// Idempotency over the merged result.
	if _, changed, _ := MergeJSON(out, plain()); changed {
		t.Error("re-merge over merged output must be a no-op")
	}
}

// TestMergeJSONUpdatesOwnEntry: a stale jentic entry (old path/context) is
// replaced; foreign keys still preserved.
func TestMergeJSONUpdatesOwnEntry(t *testing.T) {
	existing, _, err := MergeJSON([]byte(`{"mcpServers":{"other":{"command":"x"}}}`), PlainEntry("/old/jentic", "stale"))
	if err != nil {
		t.Fatal(err)
	}
	out, changed, err := MergeJSON(existing, plain())
	if err != nil {
		t.Fatal(err)
	}
	if !changed {
		t.Fatal("changed entry must rewrite")
	}
	if strings.Contains(string(out), "/old/jentic") || strings.Contains(string(out), `"stale"`) {
		t.Errorf("stale entry survived:\n%s", out)
	}
	if !strings.Contains(string(out), `"other"`) {
		t.Errorf("foreign server lost:\n%s", out)
	}
}

// TestMergeJSONRefusesNonObject: a corrupt file is refused, never clobbered.
func TestMergeJSONRefusesNonObject(t *testing.T) {
	for _, bad := range []string{`[1,2]`, `"str"`, `{"mcpServers": []}`} {
		if _, _, err := MergeJSON([]byte(bad), plain()); err == nil {
			t.Errorf("MergeJSON(%q) should refuse", bad)
		}
	}
}

// --- Codex TOML managed block -------------------------------------------------

func TestMergeCodexTOMLFreshFile(t *testing.T) {
	entry := PlainEntry("/usr/local/bin/jentic", "codex")
	out, changed, err := MergeCodexTOML(nil, entry)
	if err != nil {
		t.Fatal(err)
	}
	if !changed {
		t.Fatal("fresh merge must report changed")
	}
	want := tomlBeginMarker + "\n" +
		"[mcp_servers.jentic]\n" +
		`command = "/usr/local/bin/jentic"` + "\n" +
		`args = ["mcp", "--context", "codex"]` + "\n" +
		tomlEndMarker + "\n"
	if string(out) != want {
		t.Errorf("fresh file mismatch:\n got: %q\nwant: %q", out, want)
	}

	// Write twice, diff clean.
	again, changed, err := MergeCodexTOML(out, entry)
	if err != nil {
		t.Fatal(err)
	}
	if changed || string(again) != string(out) {
		t.Errorf("second merge must be a byte-identical no-op:\n%s", again)
	}
}

// TestMergeCodexTOMLPreservesForeignContent: user content around the block —
// comments, other tables, other MCP servers — round-trips byte-identical.
func TestMergeCodexTOMLPreservesForeignContent(t *testing.T) {
	existing := "# my codex config\nmodel = \"o3\"\n\n[mcp_servers.github]\ncommand = \"gh-mcp\"\n"
	entry := PlainEntry("/usr/local/bin/jentic", "codex")
	out, changed, err := MergeCodexTOML([]byte(existing), entry)
	if err != nil {
		t.Fatal(err)
	}
	if !changed {
		t.Fatal("append must report changed")
	}
	if !strings.HasPrefix(string(out), "# my codex config\nmodel = \"o3\"\n\n[mcp_servers.github]\ncommand = \"gh-mcp\"\n") {
		t.Errorf("foreign prefix not preserved byte-identical:\n%s", out)
	}

	// Update in place: context changes, foreign content still byte-identical.
	updated, changed, err := MergeCodexTOML(out, PlainEntry("/usr/local/bin/jentic", "codex2"))
	if err != nil {
		t.Fatal(err)
	}
	if !changed {
		t.Fatal("changed context must rewrite the block")
	}
	if !strings.Contains(string(updated), `"codex2"`) || strings.Contains(string(updated), `"codex"]`) {
		t.Errorf("block not updated in place:\n%s", updated)
	}
	if !strings.HasPrefix(string(updated), "# my codex config\nmodel = \"o3\"\n") {
		t.Errorf("foreign prefix lost on update:\n%s", updated)
	}
}

// TestMergeCodexTOMLRefusesManualEntry: a hand-written [mcp_servers.jentic]
// outside our markers would become a duplicate table (invalid TOML) — refuse.
func TestMergeCodexTOMLRefusesManualEntry(t *testing.T) {
	existing := "[mcp_servers.jentic]\ncommand = \"/hand/rolled\"\n"
	if _, _, err := MergeCodexTOML([]byte(existing), plain()); err == nil {
		t.Fatal("must refuse a manual jentic table outside the managed block")
	}
}

// --- Claude Code exec plan ------------------------------------------------------

// TestClaudeCodeSteps: the plan is remove (best-effort) then add, with the
// entry's command/args after the `--` separator so claude never parses them
// as its own flags. Assembly only — nothing is executed.
func TestClaudeCodeSteps(t *testing.T) {
	steps := ClaudeCodeSteps("/usr/local/bin/claude", PlainEntry("/opt/homebrew/bin/jentic", "claude-code"))
	if len(steps) != 2 {
		t.Fatalf("want 2 steps, got %d", len(steps))
	}
	if !steps[0].BestEffort {
		t.Error("remove step must be best-effort (absent entry on first run)")
	}
	wantRemove := "/usr/local/bin/claude mcp remove --scope user jentic"
	if got := strings.Join(steps[0].Argv, " "); got != wantRemove {
		t.Errorf("remove argv:\n got %q\nwant %q", got, wantRemove)
	}
	wantAdd := "/usr/local/bin/claude mcp add --scope user jentic -- /opt/homebrew/bin/jentic mcp --context claude-code"
	if got := strings.Join(steps[1].Argv, " "); got != wantAdd {
		t.Errorf("add argv:\n got %q\nwant %q", got, wantAdd)
	}
	if steps[1].BestEffort {
		t.Error("add step must not be best-effort")
	}
}

// --- entry shapes ---------------------------------------------------------------

func TestPlainEntryPinsContext(t *testing.T) {
	e := PlainEntry("/abs/jentic", "my-ctx")
	if got := strings.Join(e.Args, " "); got != "mcp --context my-ctx" {
		t.Errorf("args = %q", got)
	}
}

func TestSudoShimEntry(t *testing.T) {
	e := SudoShimEntry("_jentic-cursor", "/abs/jentic", "cursor")
	if e.Command != "sudo" {
		t.Errorf("command = %q", e.Command)
	}
	want := "-n -u _jentic-cursor /abs/jentic mcp --context cursor"
	if got := strings.Join(e.Args, " "); got != want {
		t.Errorf("args:\n got %q\nwant %q", got, want)
	}
}

func TestContainerEntryHardening(t *testing.T) {
	e := ContainerEntry("ghcr.io/jentic/jentic-one-cli:1.0.0", "cursor")
	joined := strings.Join(e.Args, " ")
	for _, flag := range []string{"run", "-i", "--rm", "--read-only", "--cap-drop=ALL", "no-new-privileges", "jentic-mcp-cursor:"} {
		if !strings.Contains(joined, flag) {
			t.Errorf("container args missing %q: %s", flag, joined)
		}
	}
	if !strings.HasSuffix(joined, "jentic mcp --context cursor") {
		t.Errorf("container argv must end with the pinned mcp command: %s", joined)
	}
}

func TestWireTags(t *testing.T) {
	want := map[Runtime]string{
		RuntimeCursor:        "cursor",
		RuntimeClaudeDesktop: "claude_desktop",
		RuntimeClaudeCode:    "claude_code",
		RuntimeCodex:         "codex",
		Runtime("hermes"):    "other",
	}
	for r, tag := range want {
		if got := r.WireTag(); got != tag {
			t.Errorf("WireTag(%s) = %q, want %q", r, got, tag)
		}
	}
}

// --- file-level writers -----------------------------------------------------------

// TestWriteJSONEntryLifecycle: create → no-op re-run → update, with the file
// mode staying tight on create.
func TestWriteJSONEntryLifecycle(t *testing.T) {
	path := filepath.Join(t.TempDir(), ".cursor", "mcp.json")
	entry := plain()

	out, err := WriteJSONEntry(RuntimeCursor, path, entry)
	if err != nil {
		t.Fatal(err)
	}
	if !out.Created || !out.Changed {
		t.Errorf("first write: %+v", out)
	}
	if info, err := os.Stat(path); err != nil || info.Mode().Perm() != 0o600 {
		t.Errorf("new config should be 0600, got %v (%v)", info.Mode().Perm(), err)
	}

	out, err = WriteJSONEntry(RuntimeCursor, path, entry)
	if err != nil {
		t.Fatal(err)
	}
	if out.Changed || out.Created {
		t.Errorf("re-run must be a no-op: %+v", out)
	}

	out, err = WriteJSONEntry(RuntimeCursor, path, PlainEntry("/new/jentic", "cursor"))
	if err != nil {
		t.Fatal(err)
	}
	if !out.Changed || out.Created {
		t.Errorf("update: %+v", out)
	}
}

func TestWriteCodexEntryLifecycle(t *testing.T) {
	path := filepath.Join(t.TempDir(), ".codex", "config.toml")
	entry := PlainEntry("/abs/jentic", "codex")

	out, err := WriteCodexEntry(path, entry)
	if err != nil {
		t.Fatal(err)
	}
	if !out.Created || !out.Changed {
		t.Errorf("first write: %+v", out)
	}
	out, err = WriteCodexEntry(path, entry)
	if err != nil {
		t.Fatal(err)
	}
	if out.Changed {
		t.Errorf("re-run must be a no-op: %+v", out)
	}
}

// --- detection --------------------------------------------------------------------

func TestDetect(t *testing.T) {
	home := "/home/u"
	env := Env{
		Home: home,
		GOOS: "linux",
		Stat: func(p string) bool {
			return p == filepath.Join(home, ".cursor") ||
				p == filepath.Join(home, ".config", "Claude")
		},
		LookPath: func(name string) (string, error) {
			if name == "claude" {
				return "/usr/local/bin/claude", nil
			}
			return "", os.ErrNotExist
		},
	}
	for r, want := range map[Runtime]bool{
		RuntimeCursor:        true,  // ~/.cursor exists
		RuntimeClaudeDesktop: true,  // config parent dir exists
		RuntimeClaudeCode:    true,  // claude on PATH
		RuntimeCodex:         false, // nothing present
	} {
		if got := Detect(r, env); got != want {
			t.Errorf("Detect(%s) = %v, want %v", r, got, want)
		}
	}
}

func TestClaudeDesktopConfigPathPerOS(t *testing.T) {
	if p := ClaudeDesktopConfigPath("/Users/u", "darwin"); p != "/Users/u/Library/Application Support/Claude/claude_desktop_config.json" {
		t.Errorf("darwin path = %q", p)
	}
	if p := ClaudeDesktopConfigPath("/home/u", "linux"); p != "/home/u/.config/Claude/claude_desktop_config.json" {
		t.Errorf("linux path = %q", p)
	}
	if p := ClaudeDesktopConfigPath("/home/u", "windows"); p != "" {
		t.Errorf("windows must be unsupported, got %q", p)
	}
}

// --- stable binary path ---------------------------------------------------------

func TestStableBinaryPathPrefersBrewSymlink(t *testing.T) {
	const cellar = "/opt/homebrew/Cellar/jentic/1.2.3/bin/jentic"
	eval := func(p string) (string, error) {
		if p == cellar || p == "/opt/homebrew/bin/jentic" {
			return cellar, nil // the brew symlink resolves to the Cellar realpath
		}
		return "", os.ErrNotExist
	}
	lookPath := func(string) (string, error) { return "/opt/homebrew/bin/jentic", nil }
	got, err := stableBinaryPath(cellar, "/Users/u", lookPath, eval)
	if err != nil {
		t.Fatal(err)
	}
	if got != "/opt/homebrew/bin/jentic" {
		t.Errorf("got %q, want the stable brew symlink", got)
	}
}

func TestStableBinaryPathIgnoresForeignPATHEntry(t *testing.T) {
	// A different jentic earlier on PATH must not be written into the entry.
	eval := func(p string) (string, error) { return p, nil }
	lookPath := func(string) (string, error) { return "/somewhere/else/jentic", nil }
	got, err := stableBinaryPath("/real/exe/jentic", "", lookPath, eval)
	if err != nil {
		t.Fatal(err)
	}
	if got != "/real/exe/jentic" {
		t.Errorf("got %q, want the executable's own path", got)
	}
}

func TestStableBinaryPathLocalBinFallback(t *testing.T) {
	const realExe = "/home/u/.local/share/jentic/versions/1.0/jentic"
	eval := func(p string) (string, error) {
		if p == realExe || p == "/home/u/.local/bin/jentic" {
			return realExe, nil
		}
		return "", os.ErrNotExist
	}
	lookPath := func(string) (string, error) { return "", os.ErrNotExist }
	got, err := stableBinaryPath(realExe, "/home/u", lookPath, eval)
	if err != nil {
		t.Fatal(err)
	}
	if got != "/home/u/.local/bin/jentic" {
		t.Errorf("got %q, want the ~/.local/bin alias", got)
	}
}
