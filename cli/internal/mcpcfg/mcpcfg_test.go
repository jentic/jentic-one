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

// TestMergeJSONPreservesLargeIntegers: a sibling server carrying an integer
// above 2^53 (an ID, a timestamp in ns) must round-trip exactly — a float64
// detour would silently corrupt it.
func TestMergeJSONPreservesLargeIntegers(t *testing.T) {
	existing := []byte(`{"mcpServers":{"other":{"command":"x","env":{"ACCOUNT_ID":9007199254740993}}}}`)
	out, _, err := MergeJSON(existing, plain())
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(out), "9007199254740993") {
		t.Errorf("large integer corrupted:\n%s", out)
	}
	if strings.Contains(string(out), "9007199254740992") || strings.Contains(string(out), "e+") {
		t.Errorf("large integer took the float64 detour:\n%s", out)
	}
}

// TestReadJSONEntry: the read-back doctor uses — absent file / absent entry /
// present entry round-trips the exact command+args we wrote.
func TestReadJSONEntry(t *testing.T) {
	path := filepath.Join(t.TempDir(), "mcp.json")
	if _, ok, err := ReadJSONEntry(path); err != nil || ok {
		t.Fatalf("absent file: ok=%v err=%v, want false/nil", ok, err)
	}

	if err := os.WriteFile(path, []byte(`{"mcpServers":{"other":{"command":"x"}}}`), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, ok, err := ReadJSONEntry(path); err != nil || ok {
		t.Fatalf("foreign-only file: ok=%v err=%v, want false/nil", ok, err)
	}

	if _, err := WriteJSONEntry(RuntimeCursor, path, plain()); err != nil {
		t.Fatal(err)
	}
	entry, ok, err := ReadJSONEntry(path)
	if err != nil || !ok {
		t.Fatalf("written file: ok=%v err=%v", ok, err)
	}
	if entry.Command != plain().Command || strings.Join(entry.Args, " ") != strings.Join(plain().Args, " ") {
		t.Errorf("round-trip mismatch: %+v", entry)
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

// TestMergeCodexTOMLCRLFRoundTrip: a CRLF-saved config keeps every byte
// outside the managed block exactly as the operator's editor wrote it — the
// splice searches newline-agnostically but never rewrites foreign line
// endings. Covers both the first append and an in-place block update.
func TestMergeCodexTOMLCRLFRoundTrip(t *testing.T) {
	foreign := "# crlf config\r\nmodel = \"o3\"\r\n\r\n[mcp_servers.github]\r\ncommand = \"gh-mcp\"\r\n"
	entry := PlainEntry("/usr/local/bin/jentic", "codex")

	out, changed, err := MergeCodexTOML([]byte(foreign), entry)
	if err != nil {
		t.Fatal(err)
	}
	if !changed {
		t.Fatal("append must report changed")
	}
	if !strings.HasPrefix(string(out), foreign) {
		t.Errorf("CRLF foreign content not byte-identical:\n%q", out)
	}

	// Idempotent over the mixed-endings result.
	if _, changed, err := MergeCodexTOML(out, entry); err != nil || changed {
		t.Errorf("re-merge must be a no-op: changed=%v err=%v", changed, err)
	}

	// Update in place: the block (found after a \r\n boundary in the mixed
	// file? here after \n) changes, the CRLF prefix still survives untouched.
	updated, changed, err := MergeCodexTOML(out, PlainEntry("/usr/local/bin/jentic", "codex2"))
	if err != nil || !changed {
		t.Fatalf("update: changed=%v err=%v", changed, err)
	}
	if !strings.HasPrefix(string(updated), foreign) {
		t.Errorf("CRLF foreign content lost on update:\n%q", updated)
	}

	// Markers preceded by \r\n directly are found too (fully-CRLF file whose
	// block a previous tool converted): synthesize one.
	crlfBlocked := "a = 1\r\n" + tomlBeginMarker + "\n[mcp_servers.jentic]\ncommand = \"/old\"\nargs = []\n" + tomlEndMarker + "\r\ntail = 2\r\n"
	out2, changed, err := MergeCodexTOML([]byte(crlfBlocked), entry)
	if err != nil || !changed {
		t.Fatalf("crlf-boundary update: changed=%v err=%v", changed, err)
	}
	if !strings.HasPrefix(string(out2), "a = 1\r\n") || !strings.HasSuffix(string(out2), "\r\ntail = 2\r\n") {
		t.Errorf("bytes around the block not preserved:\n%q", out2)
	}
	if !strings.Contains(string(out2), "/usr/local/bin/jentic") || strings.Contains(string(out2), "/old") {
		t.Errorf("block not replaced:\n%q", out2)
	}
}

// TestReadCodexEntry: the managed-block read-back doctor uses.
func TestReadCodexEntry(t *testing.T) {
	path := filepath.Join(t.TempDir(), "config.toml")
	if _, ok, err := ReadCodexEntry(path); err != nil || ok {
		t.Fatalf("absent file: ok=%v err=%v", ok, err)
	}
	entry := PlainEntry(`/abs/with"quote/jentic`, "codex")
	if _, err := WriteCodexEntry(path, entry); err != nil {
		t.Fatal(err)
	}
	got, ok, err := ReadCodexEntry(path)
	if err != nil || !ok {
		t.Fatalf("written file: ok=%v err=%v", ok, err)
	}
	if got.Command != entry.Command || strings.Join(got.Args, "\x00") != strings.Join(entry.Args, "\x00") {
		t.Errorf("round-trip mismatch:\n got %+v\nwant %+v", got, entry)
	}
}

// --- Claude Code exec plan ------------------------------------------------------

// TestClaudeCodeSteps: with a stale entry present the plan is remove
// (best-effort) then add; with no entry it is add ONLY (no destructive
// remove window on a first run). The entry's command/args ride after the
// `--` separator so claude never parses them as its own flags. Assembly only
// — nothing is executed.
func TestClaudeCodeSteps(t *testing.T) {
	entry := PlainEntry("/opt/homebrew/bin/jentic", "claude-code")

	steps := ClaudeCodeSteps("/usr/local/bin/claude", entry, true)
	if len(steps) != 2 {
		t.Fatalf("stale entry: want 2 steps, got %d", len(steps))
	}
	if !steps[0].BestEffort {
		t.Error("remove step must be best-effort")
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

	fresh := ClaudeCodeSteps("/usr/local/bin/claude", entry, false)
	if len(fresh) != 1 {
		t.Fatalf("absent entry: want 1 step (add only), got %d", len(fresh))
	}
	if got := strings.Join(fresh[0].Argv, " "); got != wantAdd {
		t.Errorf("fresh add argv:\n got %q\nwant %q", got, wantAdd)
	}
}

// TestClaudeCodeProbeArgv pins the read-only probe that decides between
// no-op, add-only, and remove-then-add.
func TestClaudeCodeProbeArgv(t *testing.T) {
	want := "/usr/local/bin/claude mcp get jentic"
	if got := strings.Join(ClaudeCodeProbeArgv("/usr/local/bin/claude"), " "); got != want {
		t.Errorf("probe argv = %q, want %q", got, want)
	}
}

// TestClaudeCodeConverged: convergence requires BOTH the command path and the
// full space-joined argv — a partial or reordered match must re-run the
// rewrite, never skip it.
func TestClaudeCodeConverged(t *testing.T) {
	entry := PlainEntry("/opt/homebrew/bin/jentic", "claude-code")
	cases := []struct {
		name string
		out  string
		want bool
	}{
		{"typical get output", "jentic:\n  Command: /opt/homebrew/bin/jentic\n  Args: mcp --context claude-code\n", true},
		{"command missing", "Args: mcp --context claude-code", false},
		{"different context", "Command: /opt/homebrew/bin/jentic\nArgs: mcp --context other", false},
		{"partial args", "Command: /opt/homebrew/bin/jentic\nArgs: mcp", false},
		{"empty", "", false},
	}
	for _, tc := range cases {
		if got := ClaudeCodeConverged([]byte(tc.out), entry); got != tc.want {
			t.Errorf("%s: converged = %v, want %v", tc.name, got, tc.want)
		}
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
	// -H is load-bearing: without it macOS's `env_keep HOME` leaks the
	// operator's home into the isolated server's XDG resolution.
	want := "-n -H -u _jentic-cursor /abs/jentic mcp --context cursor"
	if got := strings.Join(e.Args, " "); got != want {
		t.Errorf("args:\n got %q\nwant %q", got, want)
	}
}

// TestEntryPins covers the read-back helpers doctor keys off: the binary an
// entry actually spawns and the context it actually pins, for both plain and
// sudo-shim shapes.
func TestEntryPins(t *testing.T) {
	plainE := PlainEntry("/abs/jentic", "cursor")
	if got := plainE.PinnedBinary(); got != "/abs/jentic" {
		t.Errorf("plain PinnedBinary = %q", got)
	}
	if got := plainE.PinnedContext(); got != "cursor" {
		t.Errorf("plain PinnedContext = %q", got)
	}
	shim := SudoShimEntry("_jentic-cursor", "/abs/jentic", "cursor")
	if got := shim.PinnedBinary(); got != "/abs/jentic" {
		t.Errorf("shim PinnedBinary = %q", got)
	}
	if got := shim.PinnedContext(); got != "cursor" {
		t.Errorf("shim PinnedContext = %q", got)
	}
	if got := (Entry{Command: "sudo", Args: []string{"-n"}}).PinnedBinary(); got != "" {
		t.Errorf("unrecognized shim PinnedBinary = %q, want empty", got)
	}
	if got := (Entry{Command: "/abs/jentic"}).PinnedContext(); got != "" {
		t.Errorf("context-less PinnedContext = %q, want empty", got)
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

// TestWriteFilePreservingAtomicity pins the crash-safety semantics of the
// third-party config writer: the rewrite lands via a rename of a temp file
// staged IN THE TARGET DIR (same filesystem, so the rename is atomic and a
// crash can never leave a truncated config), no temp litter survives, and an
// existing file's wider mode is preserved while a fresh file lands 0600.
func TestWriteFilePreservingAtomicity(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "mcp.json")
	if err := os.WriteFile(path, []byte("old"), 0o644); err != nil {
		t.Fatal(err)
	}
	before, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}

	if err := writeFilePreserving(path, []byte("new"), true); err != nil {
		t.Fatal(err)
	}
	after, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	// Rename-based replace: the path names a NEW file, never a truncated
	// in-place rewrite of the old inode.
	if os.SameFile(before, after) {
		t.Error("rewrite reused the old inode — expected an atomic rename of a fresh temp file")
	}
	if after.Mode().Perm() != 0o644 {
		t.Errorf("existing mode not preserved: %o", after.Mode().Perm())
	}
	if data, _ := os.ReadFile(path); string(data) != "new" {
		t.Errorf("content = %q", data)
	}
	entries, err := os.ReadDir(dir)
	if err != nil {
		t.Fatal(err)
	}
	for _, e := range entries {
		if strings.Contains(e.Name(), ".tmp") {
			t.Errorf("temp file litter left behind: %s", e.Name())
		}
	}

	// Fresh file: parent created, 0600.
	freshPath := filepath.Join(dir, "sub", "config.toml")
	if err := writeFilePreserving(freshPath, []byte("x"), false); err != nil {
		t.Fatal(err)
	}
	if info, err := os.Stat(freshPath); err != nil || info.Mode().Perm() != 0o600 {
		t.Errorf("fresh file mode = %v (%v), want 0600", info.Mode().Perm(), err)
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
