package gitauth

import (
	"encoding/base64"
	"strings"
	"testing"
)

func TestEnvCarriesScopedHeaderNotArgv(t *testing.T) {
	if got := Env(""); got != nil {
		t.Fatalf("Env(\"\") = %v, want nil", got)
	}

	const token = "ghp_unit_test_tok"
	wantBasic := base64.StdEncoding.EncodeToString([]byte("x-access-token:" + token))
	got := Env(token)
	want := []string{
		"GIT_CONFIG_COUNT=1",
		"GIT_CONFIG_KEY_0=http.https://github.com/.extraheader",
		"GIT_CONFIG_VALUE_0=Authorization: Basic " + wantBasic,
	}
	if len(got) != len(want) {
		t.Fatalf("len(Env) = %d, want %d: %v", len(got), len(want), got)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Errorf("Env[%d] = %q, want %q", i, got[i], want[i])
		}
		if strings.HasPrefix(got[i], "-c") {
			t.Errorf("Env[%d] looks like a git argv flag: %q", i, got[i])
		}
	}
}

func TestScrubbedEnvDropsSecrets(t *testing.T) {
	t.Setenv("GITHUB_TOKEN", "ghp_drop")
	t.Setenv("JENTIC_BEARER_TOKEN", "bearer_drop")
	t.Setenv("JENTIC_MCP_BEARER", "mcp_drop")
	t.Setenv("JENTIC_SESSION_ID", "sess_drop")
	t.Setenv("SERVICE_TOKEN", "tok_drop")
	t.Setenv("CLIENT_SECRET", "sec_drop")
	t.Setenv("DATABASE_PASSWORD", "pw_drop")
	t.Setenv("SAFE_PUBLIC_VAR", "keep_value")

	got := strings.Join(ScrubbedEnv(), "\n")
	for _, dropped := range []string{
		"GITHUB_TOKEN=",
		"JENTIC_BEARER_TOKEN=",
		"JENTIC_MCP_BEARER=",
		"JENTIC_SESSION_ID=",
		"SERVICE_TOKEN=",
		"CLIENT_SECRET=",
		"DATABASE_PASSWORD=",
		"ghp_drop",
		"bearer_drop",
		"mcp_drop",
		"sess_drop",
		"tok_drop",
		"sec_drop",
		"pw_drop",
	} {
		if strings.Contains(got, dropped) {
			t.Errorf("ScrubbedEnv retained %q", dropped)
		}
	}
	if !strings.Contains(got, "SAFE_PUBLIC_VAR=keep_value") {
		t.Error("ScrubbedEnv dropped unrelated SAFE_PUBLIC_VAR")
	}
}

func TestScrubbedEnvKeepsListed(t *testing.T) {
	t.Setenv("GITHUB_TOKEN", "ghp_keep")
	t.Setenv("JENTIC_BEARER_TOKEN", "bearer_drop")
	t.Setenv("JENTIC_MCP_BEARER", "mcp_drop")
	t.Setenv("JENTIC_SESSION_ID", "sess_drop")
	t.Setenv("OTHER_SECRET", "sec_drop")

	got := strings.Join(ScrubbedEnv("GITHUB_TOKEN"), "\n")
	if !strings.Contains(got, "GITHUB_TOKEN=ghp_keep") {
		t.Error("ScrubbedEnv(\"GITHUB_TOKEN\") did not preserve GITHUB_TOKEN")
	}
	for _, dropped := range []string{
		"JENTIC_BEARER_TOKEN=",
		"JENTIC_MCP_BEARER=",
		"JENTIC_SESSION_ID=",
		"OTHER_SECRET=",
	} {
		if strings.Contains(got, dropped) {
			t.Errorf("ScrubbedEnv(\"GITHUB_TOKEN\") retained %q", dropped)
		}
	}
}
