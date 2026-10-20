package localagentcmd

import (
	"bytes"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	"github.com/jentic/jentic-one/cli/internal/cli/ux"
	"github.com/jentic/jentic-one/cli/internal/localagent"
	"github.com/jentic/jentic-one/cli/internal/localagent/container"
	"github.com/jentic/jentic-one/cli/internal/localagent/egressproxy"
)

// bedrockHome is an operator home whose Claude Code uses Bedrock through an
// SSO profile, with the hooks and awsAuthRefresh a real setup carries.
func bedrockHome(t *testing.T) string {
	t.Helper()
	home, err := filepath.EvalSymlinks(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	for _, d := range []string{".claude", ".aws/sso/cache"} {
		if err := os.MkdirAll(filepath.Join(home, d), 0o700); err != nil {
			t.Fatal(err)
		}
	}
	settings := `{
	  "env": {
	    "CLAUDE_CODE_USE_BEDROCK": "1", "AWS_PROFILE": "dev-sso", "AWS_REGION": "eu-west-1",
	    "ANTHROPIC_MODEL": "eu.anthropic.claude-opus", "ANTHROPIC_SMALL_FAST_MODEL": "eu.anthropic.claude-haiku",
	    "CLAUDE_CODE_MAX_OUTPUT_TOKENS": "64000", "MAX_THINKING_TOKENS": 31999,
	    "OTEL_EXPORTER_OTLP_HEADERS": "authorization=secret"
	  },
	  "awsAuthRefresh": "aws sso login --profile dev-sso",
	  "hooks": {"PreToolUse": [{"matcher": "*", "hooks": [{"type": "command", "command": "rm -rf /"}]}]}
	}`
	for name, data := range map[string]string{
		".claude/settings.json":   settings,
		".aws/config":             "[profile dev-sso]\nsso_session = s\n",
		".aws/sso/cache/abc.json": `{"accessToken":"t"}`,
	} {
		if err := os.WriteFile(filepath.Join(home, name), []byte(data), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	return home
}

func TestProviderShareBedrock(t *testing.T) {
	home := bedrockHome(t)
	share, err := buildProviderShare(home, []string{"ANTHROPIC_API_KEY=sk-process", "GITHUB_TOKEN=x"})
	if err != nil {
		t.Fatal(err)
	}
	if want := []container.Mount{{Type: "bind", Source: filepath.Join(home, ".aws"), Target: "/home/agent/.aws", ReadOnly: true}}; !slices.Equal(share.Mounts, want) {
		t.Errorf("mounts = %v, want %v", share.Mounts, want)
	}
	want := []string{
		"ANTHROPIC_MODEL", "ANTHROPIC_SMALL_FAST_MODEL", "AWS_PROFILE", "AWS_REGION",
		"CLAUDE_CODE_MAX_OUTPUT_TOKENS", "CLAUDE_CODE_USE_BEDROCK", "MAX_THINKING_TOKENS",
	}
	if got := share.EnvNames(); !slices.Equal(got, want) {
		t.Errorf("env names = %v, want %v", got, want)
	}

	app := testApp(t)
	files, err := claudeContainerFiles(app, seedPrefs{forceSeed: true}, "/src/repo", &share)
	if err != nil {
		t.Fatal(err)
	}
	var settings map[string]any
	for _, f := range files {
		if f.Rel == ".claude/settings.json" {
			if err := json.Unmarshal(f.Data, &settings); err != nil {
				t.Fatal(err)
			}
		}
	}
	for _, banned := range []string{"hooks", "awsAuthRefresh"} {
		if _, ok := settings[banned]; ok {
			t.Errorf("the agent's settings carry %s", banned)
		}
	}
	env, _ := settings["env"].(map[string]any)
	if env["AWS_PROFILE"] != "dev-sso" || env["MAX_THINKING_TOKENS"] != "31999" || len(env) != len(want) {
		t.Errorf("the agent's settings env = %v", env)
	}
}

func TestProviderShareWarnsAndConfirms(t *testing.T) {
	home := bedrockHome(t)
	asked := 0
	answer := false
	orig := confirmProviderShare
	confirmProviderShare = func() (bool, error) { asked++; return answer, nil }
	t.Cleanup(func() { confirmProviderShare = orig })

	run := func(in providerShareInputs) (*container.ProviderShare, string, error) {
		app := testApp(t)
		in.home = home
		share, err := app.prepareProviderShareFrom(in)
		return share, app.Err.(*bytes.Buffer).String(), err
	}

	share, stderr, err := run(providerShareInputs{yes: true})
	if err != nil || share == nil || asked != 0 {
		t.Fatalf("--yes shares without asking: %v %v asked=%d", share, err, asked)
	}
	if strings.Count(stderr, "WARNING") != 1 || !strings.Contains(stderr, "bypassing Jentic") ||
		!strings.Contains(stderr, "local testing only") || !strings.Contains(stderr, "aws sso login") ||
		!strings.Contains(stderr, "Mounted read-only: "+filepath.Join(home, ".aws")+" -> /home/agent/.aws") {
		t.Errorf("warning:\n%s", stderr)
	}
	if strings.Contains(stderr, "dev-sso") || strings.Contains(stderr, "eu.anthropic") {
		t.Errorf("the warning names variables, never their values:\n%s", stderr)
	}

	_, stderr, err = run(providerShareInputs{})
	var coded *ux.CodedError
	if !errors.As(err, &coded) || coded.Code != ux.CodeConfirmBlocked || asked != 0 {
		t.Errorf("non-interactive without --yes is blocked: %v asked=%d", err, asked)
	}
	if !strings.Contains(stderr, "WARNING") {
		t.Error("the warning is printed before the refusal")
	}

	if share, _, err := run(providerShareInputs{interactive: true}); err == nil || share != nil || asked != 1 {
		t.Errorf("a declined prompt aborts: %v %v asked=%d", share, err, asked)
	}
	answer = true
	if share, _, err := run(providerShareInputs{interactive: true}); err != nil || share == nil || asked != 2 {
		t.Errorf("an accepted prompt shares: %v %v asked=%d", share, err, asked)
	}
}

func TestProviderShareRefusesSymlinkEscapes(t *testing.T) {
	for _, target := range []func(home string) string{
		func(string) string { return t.TempDir() },                      // outside the home
		func(home string) string { return filepath.Join(home, ".ssh") }, // another sensitive dir
		func(home string) string { return home },                        // the whole home
	} {
		home := bedrockHome(t)
		if err := os.MkdirAll(filepath.Join(home, ".ssh"), 0o700); err != nil {
			t.Fatal(err)
		}
		aws := filepath.Join(home, ".aws")
		if err := os.RemoveAll(aws); err != nil {
			t.Fatal(err)
		}
		dst := target(home)
		if err := os.Symlink(dst, aws); err != nil {
			t.Fatal(err)
		}
		if share, err := buildProviderShare(home, nil); err == nil {
			t.Errorf("~/.aws -> %s shared: %v", dst, share.Mounts)
		}
	}

	home := bedrockHome(t)
	if err := os.RemoveAll(filepath.Join(home, ".aws")); err != nil {
		t.Fatal(err)
	}
	share, err := buildProviderShare(home, nil)
	if err != nil || len(share.Mounts) != 0 {
		t.Errorf("a missing ~/.aws mounts nothing: %v %v", share.Mounts, err)
	}
}

func TestProviderShareVertexCredentialsFile(t *testing.T) {
	home, err := filepath.EvalSymlinks(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	for _, d := range []string{".claude", ".config/gcloud", "keys"} {
		if err := os.MkdirAll(filepath.Join(home, d), 0o700); err != nil {
			t.Fatal(err)
		}
	}
	key := filepath.Join(home, "keys", "sa.json")
	outside := filepath.Join(t.TempDir(), "sa.json")
	for _, f := range []string{key, outside} {
		if err := os.WriteFile(f, []byte("{}"), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	settings := `{"env":{"CLAUDE_CODE_USE_VERTEX":"1","CLOUD_ML_REGION":"us-east5","ANTHROPIC_VERTEX_PROJECT_ID":"p"}}`
	if err := os.WriteFile(filepath.Join(home, ".claude", "settings.json"), []byte(settings), 0o600); err != nil {
		t.Fatal(err)
	}
	share, err := buildProviderShare(home, []string{"GOOGLE_APPLICATION_CREDENTIALS=" + key})
	if err != nil {
		t.Fatal(err)
	}
	want := []container.Mount{
		{Type: "bind", Source: filepath.Join(home, ".config", "gcloud"), Target: "/home/agent/.config/gcloud", ReadOnly: true},
		{Type: "bind", Source: key, Target: container.GoogleCredentialsTarget, ReadOnly: true},
	}
	if !slices.Equal(share.Mounts, want) || share.Env["GOOGLE_APPLICATION_CREDENTIALS"] != container.GoogleCredentialsTarget {
		t.Errorf("mounts %v env %v", share.Mounts, share.Env)
	}
	if _, err := buildProviderShare(home, []string{"GOOGLE_APPLICATION_CREDENTIALS=" + outside}); err == nil {
		t.Error("a credentials file outside the home is refused")
	}
	if _, err := buildProviderShare(home, []string{"GOOGLE_APPLICATION_CREDENTIALS=" + filepath.Join(home, "keys", "gone.json")}); err == nil {
		t.Error("a named credentials file that is missing is refused")
	}
}

func TestProviderShareTunnelsBedrockAndKeepsSTSGoverned(t *testing.T) {
	t.Setenv("CLAUDE_CODE_USE_BEDROCK", "")
	desc, _ := localagent.Lookup("claude")
	share, err := buildProviderShare(bedrockHome(t), nil)
	if err != nil {
		t.Fatal(err)
	}
	policy, err := egressproxy.NewPolicy([]string{"aws"}, containerTunnelHosts(desc, providerShareGetenv(&share)))
	if err != nil {
		t.Fatal(err)
	}
	for host, want := range map[string]egressproxy.Action{
		"bedrock-runtime.eu-west-1.amazonaws.com": egressproxy.Tunnel,
		"oidc.eu-west-1.amazonaws.com":            egressproxy.Tunnel,
		"portal.sso.eu-west-1.amazonaws.com":      egressproxy.Tunnel,
		"sts.eu-west-1.amazonaws.com":             egressproxy.Govern,
		"sts.amazonaws.com":                       egressproxy.Govern,
		"s3.eu-west-1.amazonaws.com":              egressproxy.Govern,
	} {
		if got := policy.Decide(host + ":443").Action; got != want {
			t.Errorf("%s: %s, want %s", host, got, want)
		}
	}

	// Without a share the operator's settings do not open the Bedrock tunnel.
	policy, err = egressproxy.NewPolicy([]string{"aws"}, containerTunnelHosts(desc, providerShareGetenv(nil)))
	if err != nil {
		t.Fatal(err)
	}
	if got := policy.Decide("bedrock-runtime.eu-west-1.amazonaws.com:443").Action; got != egressproxy.Govern {
		t.Errorf("Bedrock without a share: %s", got)
	}
}

func TestShareProviderFlagNeedsContainer(t *testing.T) {
	app := testApp(t)
	cmd := NewRunCmd(app.App)
	if err := cmd.ParseFlags([]string{"--" + shareProviderFlag}); err != nil {
		t.Fatal(err)
	}
	err := app.runE(cmd, &runOptions{}, []string{"claude"})
	if err == nil || !strings.Contains(err.Error(), "needs --container") {
		t.Errorf("--%s without --container: %v", shareProviderFlag, err)
	}
	if f := cmd.Flags().Lookup(shareProviderFlag); f == nil || f.DefValue != "false" {
		t.Errorf("the flag is off by default: %+v", f)
	}
}
