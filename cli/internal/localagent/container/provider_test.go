package container

import (
	"encoding/json"
	"maps"
	"slices"
	"strings"
	"testing"
)

func TestProviderEnvBedrockKeepsOnlyProviderVariables(t *testing.T) {
	settings := map[string]string{
		"CLAUDE_CODE_USE_BEDROCK":        "1",
		"AWS_PROFILE":                    "dev-sso",
		"AWS_REGION":                     "eu-west-1",
		"ANTHROPIC_MODEL":                "eu.anthropic.claude-opus",
		"ANTHROPIC_DEFAULT_OPUS_MODEL":   "eu.anthropic.claude-opus",
		"ANTHROPIC_DEFAULT_HAIKU_MODEL":  "eu.anthropic.claude-haiku",
		"ANTHROPIC_SMALL_FAST_MODEL":     "eu.anthropic.claude-haiku",
		"CLAUDE_CODE_MAX_OUTPUT_TOKENS":  "64000",
		"MAX_THINKING_TOKENS":            "31999",
		"ANTHROPIC_API_KEY":              "sk-from-file",
		"ANTHROPIC_MODEL_API_KEY":        "sk-smuggled",
		"AWS_SECRET_ACCESS_KEY":          "secret",
		"OTEL_EXPORTER_OTLP_ENDPOINT":    "http://collector",
		"CLAUDE_CODE_ENABLE_TELEMETRY":   "1",
		"GITHUB_TOKEN":                   "ghp_x",
		"ANTHROPIC_CUSTOM_HEADERS":       "x: y",
		"ANTHROPIC_SMALL_FAST_MODEL_KEY": "nope",
	}
	environ := []string{
		"AWS_REGION=us-east-1", // the settings value wins
		"AWS_DEFAULT_REGION=eu-west-1",
		"ANTHROPIC_DEFAULT_SONNET_MODEL=eu.anthropic.claude-sonnet",
		"ANTHROPIC_API_KEY=sk-process", // not for Bedrock
		"AWS_ACCESS_KEY_ID=AKIA",
		"HOME=/Users/alice",
		"PATH=/usr/bin",
	}
	provider, env, gac := ProviderEnv(settings, environ)
	if provider != ProviderBedrock || gac != "" {
		t.Fatalf("provider = %q, gac = %q", provider, gac)
	}
	want := map[string]string{
		"CLAUDE_CODE_USE_BEDROCK":        "1",
		"AWS_PROFILE":                    "dev-sso",
		"AWS_REGION":                     "eu-west-1",
		"AWS_DEFAULT_REGION":             "eu-west-1",
		"ANTHROPIC_MODEL":                "eu.anthropic.claude-opus",
		"ANTHROPIC_DEFAULT_OPUS_MODEL":   "eu.anthropic.claude-opus",
		"ANTHROPIC_DEFAULT_HAIKU_MODEL":  "eu.anthropic.claude-haiku",
		"ANTHROPIC_DEFAULT_SONNET_MODEL": "eu.anthropic.claude-sonnet",
		"ANTHROPIC_SMALL_FAST_MODEL":     "eu.anthropic.claude-haiku",
		"CLAUDE_CODE_MAX_OUTPUT_TOKENS":  "64000",
		"MAX_THINKING_TOKENS":            "31999",
	}
	if !maps.Equal(env, want) {
		t.Errorf("env = %v\nwant %v", env, want)
	}
}

func TestProviderEnvAnthropicKeyComesFromTheProcessOnly(t *testing.T) {
	_, env, _ := ProviderEnv(map[string]string{"ANTHROPIC_API_KEY": "sk-from-file"}, nil)
	if _, ok := env["ANTHROPIC_API_KEY"]; ok {
		t.Errorf("a key from settings.json is never passed: %v", env)
	}
	provider, env, _ := ProviderEnv(map[string]string{"ANTHROPIC_API_KEY": "sk-from-file"},
		[]string{"ANTHROPIC_API_KEY=sk-process", "AWS_PROFILE=x"})
	if provider != ProviderAnthropic || !maps.Equal(env, map[string]string{"ANTHROPIC_API_KEY": "sk-process"}) {
		t.Errorf("provider %q env %v", provider, env)
	}
}

func TestProviderEnvVertex(t *testing.T) {
	provider, env, gac := ProviderEnv(map[string]string{
		"CLAUDE_CODE_USE_VERTEX":         "true",
		"CLOUD_ML_REGION":                "us-east5",
		"ANTHROPIC_VERTEX_PROJECT_ID":    "proj",
		"GOOGLE_APPLICATION_CREDENTIALS": "/Users/alice/keys/sa.json",
		"AWS_PROFILE":                    "x",
	}, []string{"ANTHROPIC_API_KEY=sk-process"})
	want := map[string]string{"CLAUDE_CODE_USE_VERTEX": "true", "CLOUD_ML_REGION": "us-east5", "ANTHROPIC_VERTEX_PROJECT_ID": "proj"}
	if provider != ProviderVertex || gac != "/Users/alice/keys/sa.json" || !maps.Equal(env, want) {
		t.Errorf("provider %q gac %q env %v", provider, gac, env)
	}
}

func TestNewProviderShareMountsReadOnlyAtExactPaths(t *testing.T) {
	src := ProviderSources{AWSDir: "/Users/alice/.aws", GCloudDir: "/Users/alice/.config/gcloud", GoogleCredentials: "/Users/alice/keys/sa.json"}

	bedrock, err := NewProviderShare(ProviderBedrock, map[string]string{"AWS_PROFILE": "p"}, src)
	if err != nil {
		t.Fatal(err)
	}
	if want := []Mount{{Type: "bind", Source: "/Users/alice/.aws", Target: "/home/agent/.aws", ReadOnly: true}}; !slices.Equal(bedrock.Mounts, want) || len(bedrock.Dirs) != 0 {
		t.Errorf("bedrock mounts %v dirs %v", bedrock.Mounts, bedrock.Dirs)
	}

	vertex, err := NewProviderShare(ProviderVertex, map[string]string{"CLOUD_ML_REGION": "us-east5"}, src)
	if err != nil {
		t.Fatal(err)
	}
	want := []Mount{
		{Type: "bind", Source: "/Users/alice/.config/gcloud", Target: "/home/agent/.config/gcloud", ReadOnly: true},
		{Type: "bind", Source: "/Users/alice/keys/sa.json", Target: "/run/jentic-provider/google-application-credentials.json", ReadOnly: true},
	}
	if !slices.Equal(vertex.Mounts, want) || !slices.Equal(vertex.Dirs, []string{".config"}) {
		t.Errorf("vertex mounts %v dirs %v", vertex.Mounts, vertex.Dirs)
	}
	if vertex.Env["GOOGLE_APPLICATION_CREDENTIALS"] != GoogleCredentialsTarget {
		t.Errorf("GOOGLE_APPLICATION_CREDENTIALS must name the mounted copy: %v", vertex.Env)
	}

	anthropic, err := NewProviderShare(ProviderAnthropic, map[string]string{"ANTHROPIC_API_KEY": "k"}, src)
	if err != nil || len(anthropic.Mounts) != 0 {
		t.Errorf("the Anthropic API mounts nothing: %v %v", anthropic.Mounts, err)
	}
	if _, err := NewProviderShare("openai", nil, src); err == nil {
		t.Error("an unknown provider is refused")
	}
}

func TestValidateProviderMountsRefusesWritableOrForeignMounts(t *testing.T) {
	for _, m := range []Mount{
		{Type: "bind", Source: "/Users/alice/.aws", Target: AWSConfigTarget},
		{Type: "volume", Source: "v", Target: AWSConfigTarget, ReadOnly: true},
		{Type: "bind", Source: "/Users/alice/.ssh", Target: AgentHome + "/.ssh", ReadOnly: true},
		{Type: "bind", Source: "/", Target: AWSConfigTarget, ReadOnly: true},
		{Type: "bind", Source: "relative/.aws", Target: AWSConfigTarget, ReadOnly: true},
		{Type: "bind", Source: "/Users/alice/../bob/.aws", Target: AWSConfigTarget, ReadOnly: true},
	} {
		if err := ValidateProviderMounts([]Mount{m}); err == nil {
			t.Errorf("mount %+v accepted", m)
		}
	}
	dup := Mount{Type: "bind", Source: "/Users/alice/.aws", Target: AWSConfigTarget, ReadOnly: true}
	if err := ValidateProviderMounts([]Mount{dup, dup}); err == nil {
		t.Error("a target given twice is refused")
	}
}

func TestPlanRendersProviderMountsReadOnly(t *testing.T) {
	o := testOptions()
	o.ProviderMounts = []Mount{{Type: "bind", Source: "/Users/alice/.aws", Target: AWSConfigTarget, ReadOnly: true}}
	args, err := mustPlan(t, o).Agent.CreateArgs()
	if err != nil {
		t.Fatal(err)
	}
	if !slices.Contains(argValues(args, "--mount"), "type=bind,src=/Users/alice/.aws,dst=/home/agent/.aws,readonly") {
		t.Errorf("agent mounts: %v", argValues(args, "--mount"))
	}
	for _, m := range argValues(args, "--mount") {
		if strings.Contains(m, ".aws") && !strings.HasSuffix(m, ",readonly") {
			t.Errorf("writable provider mount %s", m)
		}
	}
	o.ProviderMounts[0].ReadOnly = false
	if _, err := NewPlan(o); err == nil {
		t.Error("a writable provider mount refuses the plan")
	}
	if args, _ := mustPlan(t, testOptions()).Agent.CreateArgs(); strings.Contains(strings.Join(args, " "), ".aws") {
		t.Error("no provider mount without a share")
	}
}

func TestClaudeHomeFilesWritesTheSharedEnvOnly(t *testing.T) {
	seed := ClaudeSeed{
		Settings: []byte(`{"model":"opus","env":{"AWS_PROFILE":"from-seed","OTEL_X":"1"},
			"hooks":{"PreToolUse":[]},"awsAuthRefresh":"aws sso login","awsCredentialExport":"x","apiKeyHelper":"x"}`),
		ProviderEnv: map[string]string{"CLAUDE_CODE_USE_BEDROCK": "1", "AWS_PROFILE": "dev-sso", "AWS_REGION": "eu-west-1"},
	}
	files, err := ClaudeHomeFiles("/w", seed)
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
	if settings == nil {
		t.Fatal("no settings.json written")
	}
	for _, banned := range []string{"hooks", "awsAuthRefresh", "awsCredentialExport", "apiKeyHelper"} {
		if _, ok := settings[banned]; ok {
			t.Errorf("settings carry %s: %v", banned, settings)
		}
	}
	env, _ := settings["env"].(map[string]any)
	want := map[string]any{"CLAUDE_CODE_USE_BEDROCK": "1", "AWS_PROFILE": "dev-sso", "AWS_REGION": "eu-west-1"}
	if !maps.Equal(env, want) {
		t.Errorf("settings env = %v, want %v", env, want)
	}

	// Without a share the operator's env block is never copied.
	files, err = ClaudeHomeFiles("/w", ClaudeSeed{Settings: seed.Settings})
	if err != nil {
		t.Fatal(err)
	}
	for _, f := range files {
		if f.Rel == ".claude/settings.json" && strings.Contains(string(f.Data), "env") {
			t.Errorf("settings without a share carry env: %s", f.Data)
		}
	}
}

func TestClaudeHomeFilesApprovesASharedAPIKey(t *testing.T) {
	key := "sk-ant-api03-" + strings.Repeat("a", 20) + "TAIL0123456789abcdef"
	files, err := ClaudeHomeFiles("/w", ClaudeSeed{ProviderEnv: map[string]string{"ANTHROPIC_API_KEY": key}})
	if err != nil {
		t.Fatal(err)
	}
	var state struct {
		CustomAPIKeyResponses struct {
			Approved []string `json:"approved"`
		} `json:"customApiKeyResponses"`
	}
	if err := json.Unmarshal(files[0].Data, &state); err != nil {
		t.Fatal(err)
	}
	if !slices.Equal(state.CustomAPIKeyResponses.Approved, []string{"TAIL0123456789abcdef"}) {
		t.Errorf("approved = %v", state.CustomAPIKeyResponses.Approved)
	}
	if strings.Contains(string(files[0].Data), key) {
		t.Error("the state file must not hold the whole key")
	}
}

func TestHomeArchiveDirEntries(t *testing.T) {
	data, err := HomeArchive(1000, 1000, []HomeFile{{Rel: ".config", Dir: true}, {Rel: ".claude.json", Data: []byte("{}")}})
	if err != nil {
		t.Fatal(err)
	}
	h := readArchive(t, data)
	got, ok := h["agent/.config/"]
	if !ok || got.Typeflag != '5' || got.Mode != 0o700 || got.Uid != 1000 {
		t.Errorf("agent/.config/ = %+v", got)
	}
	if _, err := HomeArchive(1000, 1000, []HomeFile{{Rel: "../x", Dir: true}}); err == nil {
		t.Error("an escaping directory entry is refused")
	}
}
