package install

import (
	"encoding/base64"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

func TestVenvPython(t *testing.T) {
	got := VenvPython("/venv")
	var want string
	if runtime.GOOS == "windows" {
		want = filepath.Join("/venv", "Scripts", "python.exe")
	} else {
		want = filepath.Join("/venv", "bin", "python")
	}
	if got != want {
		t.Errorf("VenvPython = %q, want %q", got, want)
	}
}

func TestBuildPlanVenvPython(t *testing.T) {
	p := BuildPlan{VenvDir: "/v"}
	if p.VenvPython() != VenvPython("/v") {
		t.Errorf("BuildPlan.VenvPython mismatch")
	}
}

func TestPlanLocalBuildInsideRepo(t *testing.T) {
	t.Setenv(SrcEnv, "")
	root := makeFakeRepo(t)
	chdir(t, root)

	plan := PlanLocalBuild("/venv", "/clone")
	if plan.FromGit {
		t.Errorf("inside a repo, build should use local source not git")
	}
	if plan.SourceDir != root {
		t.Errorf("SourceDir = %q, want repo root %q", plan.SourceDir, root)
	}
}

func TestPlanLocalBuildOutsideRepo(t *testing.T) {
	t.Setenv(SrcEnv, "")
	chdir(t, t.TempDir())

	plan := PlanLocalBuild("/venv", "/clone")
	if !plan.FromGit {
		t.Errorf("outside a repo, build should clone from git")
	}
	if plan.SourceDir != "/clone" {
		t.Errorf("SourceDir = %q, want clone dir", plan.SourceDir)
	}
	if plan.GitURL != GitURL {
		t.Errorf("GitURL = %q", plan.GitURL)
	}
}

// TestPlanLocalBuildSrcEnvOverride pins the source via $JENTIC_SRC from a
// non-repo cwd: the build must use that checkout (not clone), regardless of
// where the binary runs.
func TestPlanLocalBuildSrcEnvOverride(t *testing.T) {
	root := makeFakeRepo(t)
	chdir(t, t.TempDir()) // cwd is NOT a repo
	t.Setenv(SrcEnv, root)

	plan := PlanLocalBuild("/venv", "/clone")
	if plan.FromGit {
		t.Errorf("with %s set to a checkout, build should use it, not clone", SrcEnv)
	}
	if plan.SourceDir != root {
		t.Errorf("SourceDir = %q, want %s value %q", plan.SourceDir, SrcEnv, root)
	}
}

// TestPlanLocalBuildSrcEnvInvalid ignores a $JENTIC_SRC that is not a real
// checkout and falls back to the normal cwd-walk / clone behaviour.
func TestPlanLocalBuildSrcEnvInvalid(t *testing.T) {
	chdir(t, t.TempDir())
	t.Setenv(SrcEnv, t.TempDir()) // exists but lacks repo markers

	plan := PlanLocalBuild("/venv", "/clone")
	if !plan.FromGit {
		t.Errorf("an invalid %s should be ignored, falling back to clone", SrcEnv)
	}
}

func TestEnsureUvNoopWhenPresent(t *testing.T) {
	// Stub a `uv` on PATH so EnsureUv takes its fast path and runs no installer.
	dir := t.TempDir()
	uv := filepath.Join(dir, "uv")
	if runtime.GOOS == "windows" {
		uv += ".exe"
	}
	if err := os.WriteFile(uv, []byte("#!/bin/sh\n"), 0o755); err != nil {
		t.Fatalf("write uv stub: %v", err)
	}
	t.Setenv("PATH", dir+string(os.PathListSeparator)+os.Getenv("PATH"))

	var buf strings.Builder
	EnsureUv(&buf)
	if buf.Len() != 0 {
		t.Errorf("EnsureUv wrote output when uv already present: %q", buf.String())
	}
}

func TestRepoRootDetection(t *testing.T) {
	t.Setenv(SrcEnv, "")
	root := makeFakeRepo(t)
	// From a nested subdirectory, RepoRoot should walk up to the marker.
	sub := filepath.Join(root, "a", "b")
	if err := os.MkdirAll(sub, 0o755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}
	chdir(t, sub)

	got, ok := RepoRoot()
	if !ok {
		t.Fatalf("RepoRoot not found")
	}
	if got != root {
		t.Errorf("RepoRoot = %q, want %q", got, root)
	}
}

// makeFakeRepo creates a directory that looks like a jentic-one checkout.
func makeFakeRepo(t *testing.T) string {
	t.Helper()
	root := t.TempDir()
	// EvalSymlinks so macOS /var -> /private/var matches os.Getwd later.
	resolved, err := filepath.EvalSymlinks(root)
	if err == nil {
		root = resolved
	}
	if err := os.MkdirAll(filepath.Join(root, "src", "jentic_one"), 0o755); err != nil {
		t.Fatalf("mkdir src: %v", err)
	}
	if err := os.WriteFile(filepath.Join(root, "pyproject.toml"), []byte(`name = "jentic-one"`+"\n"), 0o600); err != nil {
		t.Fatalf("write pyproject: %v", err)
	}
	return root
}

// chdir changes into dir for the duration of the test and restores afterwards.
func chdir(t *testing.T, dir string) {
	t.Helper()
	prev, err := os.Getwd()
	if err != nil {
		t.Fatalf("getwd: %v", err)
	}
	if err := os.Chdir(dir); err != nil {
		t.Fatalf("chdir: %v", err)
	}
	t.Cleanup(func() { _ = os.Chdir(prev) })
}

func TestRunGitNeverPutsTokenInArgv(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("shell stub test targets unix")
	}
	dir := t.TempDir()
	argsFile := filepath.Join(dir, "git.args")
	envFile := filepath.Join(dir, "git.env")
	gitStub := filepath.Join(dir, "git")
	script := fmt.Sprintf("#!/bin/sh\nprintf '%%s\\n' \"$@\" >> %q\nenv >> %q\nexit 0\n", argsFile, envFile)
	if err := os.WriteFile(gitStub, []byte(script), 0o755); err != nil {
		t.Fatalf("write git stub: %v", err)
	}
	t.Setenv("PATH", dir+string(os.PathListSeparator)+os.Getenv("PATH"))

	const tokVal = "ghp_build_tok_val_456"
	basic := base64.StdEncoding.EncodeToString([]byte("x-access-token:" + tokVal))
	t.Setenv("GITHUB_TOKEN", tokVal)

	cloneDir := filepath.Join(t.TempDir(), "clone")
	if err := os.MkdirAll(filepath.Join(cloneDir, ".git"), 0o755); err != nil {
		t.Fatalf("mkdir .git: %v", err)
	}
	plan := BuildPlan{
		SourceDir: cloneDir,
		VenvDir:   filepath.Join(t.TempDir(), "venv"),
		FromGit:   true,
		GitURL:    GitURL,
	}
	var buf strings.Builder
	if err := plan.fetchSource(&buf); err != nil {
		t.Fatalf("fetchSource: %v", err)
	}

	gotArgsBytes, err := os.ReadFile(argsFile)
	if err != nil {
		t.Fatalf("read recorded args: %v", err)
	}
	gotArgs := string(gotArgsBytes)
	if strings.Contains(gotArgs, tokVal) || strings.Contains(gotArgs, basic) || strings.Contains(gotArgs, "http.extraheader") {
		t.Fatalf("git argv exposed token or auth header:\n%s", gotArgs)
	}

	gotEnvBytes, err := os.ReadFile(envFile)
	if err != nil {
		t.Fatalf("read recorded env: %v", err)
	}
	gotEnv := string(gotEnvBytes)
	for _, want := range []string{
		"GIT_CONFIG_COUNT=1",
		"GIT_CONFIG_KEY_0=http.https://github.com/.extraheader",
		"GIT_CONFIG_VALUE_0=Authorization: Basic " + basic,
	} {
		if !strings.Contains(gotEnv, want) {
			t.Errorf("git env missing %q", want)
		}
	}
}

func TestEnsureUvEnvHasNoTokens(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("shell stub test targets unix")
	}
	dir := t.TempDir()
	envFile := filepath.Join(dir, "uv.env")
	// Provide only curl and sh on PATH (no uv) so EnsureUv runs the installer command.
	curlStub := filepath.Join(dir, "curl")
	if err := os.WriteFile(curlStub, []byte("#!/bin/sh\nexit 0\n"), 0o755); err != nil {
		t.Fatalf("write curl stub: %v", err)
	}
	shStub := filepath.Join(dir, "sh")
	script := fmt.Sprintf("#!/bin/sh\n/usr/bin/env > %q\nexit 0\n", envFile)
	if err := os.WriteFile(shStub, []byte(script), 0o755); err != nil {
		t.Fatalf("write sh stub: %v", err)
	}
	t.Setenv("PATH", dir)
	t.Setenv("GITHUB_TOKEN", "ghp_secret_uv")
	t.Setenv("JENTIC_BEARER_TOKEN", "bearer_secret_uv")
	t.Setenv("JENTIC_MCP_BEARER", "mcp_secret_uv")
	t.Setenv("JENTIC_SESSION_ID", "sess_secret_uv")
	t.Setenv("CUSTOM_API_TOKEN", "custom_tok_uv")
	t.Setenv("CUSTOM_CLIENT_SECRET", "custom_sec_uv")
	t.Setenv("DB_PASSWORD", "custom_pw_uv")

	var buf strings.Builder
	EnsureUv(&buf)

	gotEnvBytes, err := os.ReadFile(envFile)
	if err != nil {
		t.Fatalf("read recorded uv installer env: %v", err)
	}
	gotEnv := string(gotEnvBytes)
	for _, key := range []string{
		"GITHUB_TOKEN",
		"JENTIC_BEARER_TOKEN",
		"JENTIC_MCP_BEARER",
		"JENTIC_SESSION_ID",
		"CUSTOM_API_TOKEN",
		"CUSTOM_CLIENT_SECRET",
		"DB_PASSWORD",
	} {
		if strings.Contains(gotEnv, key+"=") {
			t.Errorf("EnsureUv leaked %q into child environment:\n%s", key, gotEnv)
		}
	}
}
