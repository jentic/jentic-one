package localagentcmd

import (
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	sdkconfig "github.com/jentic/jentic-one/cli/client/config"
	"github.com/jentic/jentic-one/cli/internal/cli/clictx"
	"github.com/jentic/jentic-one/cli/internal/cli/ux"
	"github.com/jentic/jentic-one/cli/internal/localagent/container"
)

// workFolder is a temporary work folder outside the system trees the grant
// policy bans (macOS's default temp dir is under /var).
func workFolder(t *testing.T) string {
	t.Helper()
	dir, err := os.MkdirTemp("/tmp", "jentic-run-container-test-")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = os.RemoveAll(dir) })
	return dir
}

func TestJenticLoopbackPorts(t *testing.T) {
	for _, tc := range []struct {
		base, broker string
		want         []int
	}{
		{"http://127.0.0.1:8000", "", []int{8000, defaultBrokerPort}},
		{"http://localhost:8000", "http://127.0.0.1:8100", []int{8000, 8100}},
		{"http://127.0.0.1:9000", "http://127.0.0.1:9000", []int{9000}},
		{"http://[::1]:8000", "", []int{8000, defaultBrokerPort}},
		{"http://127.0.0.1", "", []int{80, defaultBrokerPort}},
	} {
		got, err := jenticLoopbackPorts(tc.base, tc.broker)
		if err != nil || !slices.Equal(got, tc.want) {
			t.Errorf("jenticLoopbackPorts(%q, %q) = %v, %v; want %v", tc.base, tc.broker, got, err, tc.want)
		}
	}
	for _, tc := range [][2]string{
		{"https://jentic.example.com", ""},
		{"http://10.0.0.5:8000", ""},
		{"http://127.0.0.1:8000", "https://broker.example.com"},
		{"127.0.0.1:8000", ""},
		{"ftp://127.0.0.1", ""},
		{"http://127.0.0.1:0", ""},
	} {
		if got, err := jenticLoopbackPorts(tc[0], tc[1]); err == nil {
			t.Errorf("jenticLoopbackPorts(%q, %q) = %v, want an error", tc[0], tc[1], got)
		}
	}
}

func TestRunContainerRefusesAgentUserFlags(t *testing.T) {
	for _, flag := range []string{"--home", "--allow-dir", "--agent-user=x", "--grant=/tmp", "--list-grants"} {
		app := testApp(t)
		cmd := NewRunCmd(app.App)
		if err := cmd.ParseFlags([]string{"--container", flag}); err != nil {
			t.Fatal(err)
		}
		err := app.runE(cmd, &runOptions{container: containerOptions{enabled: true}}, []string{"claude"})
		if err == nil || !strings.Contains(err.Error(), "cannot be combined") {
			t.Errorf("%s with --container: %v", flag, err)
		}
	}
}

func TestContainerFlagsNeedContainer(t *testing.T) {
	app := testApp(t)
	cmd := NewRunCmd(app.App)
	if err := cmd.ParseFlags([]string{"--container-image", "x:y"}); err != nil {
		t.Fatal(err)
	}
	err := app.runE(cmd, &runOptions{}, []string{"claude"})
	if err == nil || !strings.Contains(err.Error(), "needs --container") {
		t.Errorf("--container-image without --container: %v", err)
	}
}

func TestRunContainerRunsOnlyContainerAgents(t *testing.T) {
	app := testApp(t)
	cmd := NewRunCmd(app.App)
	err := app.runE(cmd, &runOptions{container: containerOptions{enabled: true}}, []string{"codex"})
	if err == nil || !strings.Contains(err.Error(), "runs only claude") {
		t.Errorf("codex with --container: %v", err)
	}
}

func TestRunContainerNeedsALocalContext(t *testing.T) {
	work := workFolder(t)
	app := testApp(t)
	cmd := NewRunCmd(app.App)
	cmd.SetContext(clictx.WithActiveState(t.Context(), &clictx.ActiveState{
		ResolvedState: &sdkconfig.ResolvedState{EnvironmentName: "prod", BaseURL: "https://jentic.example.com"},
	}))
	err := app.runE(cmd, &runOptions{container: containerOptions{enabled: true, egress: container.EgressNone}},
		[]string{"claude", work})
	var coded *ux.CodedError
	if !errors.As(err, &coded) || coded.Code != ux.CodeConfinementMissing || !strings.Contains(coded.Msg, "loopback") {
		t.Errorf("a remote context must fail closed with CONFINEMENT_UNAVAILABLE: %v", err)
	}

	cmd.SetContext(t.Context())
	err = app.runE(cmd, &runOptions{container: containerOptions{enabled: true}}, []string{"claude", work})
	if !errors.As(err, &coded) || !strings.Contains(coded.Msg, "active context") {
		t.Errorf("no active context: %v", err)
	}

	err = app.runE(cmd, &runOptions{container: containerOptions{enabled: true, egress: "socks"}}, []string{"claude", work})
	if err == nil || !strings.Contains(err.Error(), "egress") {
		t.Errorf("an unknown egress mode is refused before anything runs: %v", err)
	}
}

func TestContainerWorkDirRefusesBannedFolders(t *testing.T) {
	home, err := os.UserHomeDir()
	if err != nil {
		t.Skip("no home directory")
	}
	for _, dir := range []string{home, filepath.Join(home, ".ssh"), "/etc"} {
		if _, err := os.Stat(dir); err != nil {
			continue
		}
		if got, err := containerWorkDir([]string{"claude", dir}); err == nil {
			t.Errorf("containerWorkDir(%s) = %s, want a refusal", dir, got)
		}
	}
	work := workFolder(t)
	got, err := containerWorkDir([]string{"claude", work})
	if err != nil {
		t.Fatal(err)
	}
	if want, _ := filepath.EvalSymlinks(work); got != want {
		t.Errorf("containerWorkDir = %s, want the canonical %s", got, want)
	}
	file := filepath.Join(work, "f")
	if err := os.WriteFile(file, nil, 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := containerWorkDir([]string{"claude", file}); err == nil {
		t.Error("a file is not a work folder")
	}
}

func TestExportMaterialHomeFiles(t *testing.T) {
	dir := t.TempDir()
	key := filepath.Join(dir, "bot_dev.key")
	if err := os.WriteFile(key, []byte("key"), 0o600); err != nil {
		t.Fatal(err)
	}
	link := filepath.Join(dir, "bot_dev.apikey")
	if err := os.Symlink(key, link); err != nil {
		t.Fatal(err)
	}
	m := &exportMaterial{
		configYAML: []byte("active_context: x\n"),
		copies: [][2]string{
			{key, filepath.Join(".config", "jentic", "keys", "bot_dev.key")},
			{filepath.Join(dir, "missing_tokens.json"), filepath.Join(".local", "state", "jentic", "bot_dev_tokens.json")},
		},
	}
	files, err := m.homeFiles()
	if err != nil {
		t.Fatal(err)
	}
	got := map[string]string{}
	for _, f := range files {
		got[f.Rel] = string(f.Data)
	}
	if len(got) != 2 || got[".config/jentic/config.yaml"] != "active_context: x\n" || got[".config/jentic/keys/bot_dev.key"] != "key" {
		t.Errorf("home files = %v", got)
	}

	m.copies = append(m.copies, [2]string{link, filepath.Join(".local", "state", "jentic", "bot_dev.apikey")})
	if _, err := m.homeFiles(); err == nil {
		t.Error("a symlinked credential source is refused")
	}
}

func TestClaudeContainerFilesHonourNoSeed(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	if err := os.WriteFile(filepath.Join(home, ".claude.json"), []byte(`{"theme":"light","primaryApiKey":"sk-x"}`), 0o600); err != nil {
		t.Fatal(err)
	}
	app := testApp(t)
	files, err := claudeContainerFiles(app, seedPrefs{forceNoSeed: true}, "/w", nil)
	if err != nil {
		t.Fatal(err)
	}
	if len(files) != 1 || strings.Contains(string(files[0].Data), "light") {
		t.Errorf("--no-seed-config must not read the operator's config: %v", files)
	}
	files, err = claudeContainerFiles(app, seedPrefs{forceSeed: true}, "/w", nil)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(files[0].Data), "light") || strings.Contains(string(files[0].Data), "sk-x") {
		t.Errorf("--seed-config keeps allowlisted keys only: %s", files[0].Data)
	}
	files, err = claudeContainerFiles(app, seedPrefs{}, "/w", nil)
	if err != nil || strings.Contains(string(files[0].Data), "light") {
		t.Errorf("non-interactive default is not to seed: %v %v", files, err)
	}
}

func TestContainerLocks(t *testing.T) {
	locks := containerLocks{dir: t.TempDir()}
	const id = "0123456789ab"
	if locks.alive(id) {
		t.Fatal("a session nobody locks is not alive")
	}
	held, err := locks.acquire(id)
	if err != nil {
		t.Fatal(err)
	}
	// A lock is per open file description, so the check must come from
	// another process to see it held, as the reaper of a later run does.
	if !lockedElsewhere(t, locks.path(id)) {
		t.Error("an acquired session lock is held")
	}
	_ = held.Unlock()
	if locks.alive(id) {
		t.Error("a released session lock is not alive")
	}
	locks.remove(id)
	if _, err := os.Stat(locks.path(id)); !errors.Is(err, os.ErrNotExist) {
		t.Errorf("remove leaves %v", err)
	}
}

// lockedElsewhere probes path's lock from a child process (the test binary
// re-run as TestContainerLockProbe).
func lockedElsewhere(t *testing.T, path string) bool {
	t.Helper()
	cmd := exec.Command(os.Args[0], "-test.run=^TestContainerLockProbe$")
	cmd.Env = append(os.Environ(), "JENTIC_LOCK_PROBE="+path)
	out, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("lock probe: %v\n%s", err, out)
	}
	return strings.Contains(string(out), "probe:alive")
}

func TestContainerLockProbe(t *testing.T) {
	path := os.Getenv("JENTIC_LOCK_PROBE")
	if path == "" {
		t.Skip("helper for TestContainerLocks")
	}
	locks := containerLocks{dir: filepath.Dir(path)}
	if locks.alive(strings.TrimSuffix(filepath.Base(path), ".lock")) {
		os.Stdout.WriteString("probe:alive\n")
	} else {
		os.Stdout.WriteString("probe:dead\n")
	}
}
