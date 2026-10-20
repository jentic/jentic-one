package container

import (
	"errors"
	"fmt"
	"path"
	"sort"
	"strings"
)

// LLM providers a container session can share the operator's login for.
const (
	ProviderAnthropic = "anthropic"
	ProviderBedrock   = "bedrock"
	ProviderVertex    = "vertex"
)

// Agent-side paths of the shared provider credentials. Each is a read-only
// bind mount of the operator's own copy.
const (
	// AWSConfigTarget holds the operator's ~/.aws (profiles, the SSO token
	// cache and any static keys).
	AWSConfigTarget = AgentHome + "/.aws"
	// GCloudConfigTarget holds the operator's ~/.config/gcloud (application
	// default credentials).
	GCloudConfigTarget = AgentHome + "/.config/gcloud"
	// GoogleCredentialsTarget holds the file GOOGLE_APPLICATION_CREDENTIALS
	// names, when the operator sets one.
	GoogleCredentialsTarget = "/run/jentic-provider/google-application-credentials.json" //nolint:gosec // G101: a mount path, not a credential
)

// providerTargets are the only targets a ProviderShare mount may have.
var providerTargets = []string{AWSConfigTarget, GCloudConfigTarget, GoogleCredentialsTarget}

const (
	envUseBedrock         = "CLAUDE_CODE_USE_BEDROCK"
	envUseVertex          = "CLAUDE_CODE_USE_VERTEX"
	envAnthropicAPIKey    = "ANTHROPIC_API_KEY"              //nolint:gosec // G101: a variable name, not a credential
	envGoogleCredentials  = "GOOGLE_APPLICATION_CREDENTIALS" //nolint:gosec // G101: a variable name, not a credential
	anthropicModelPrefix  = "ANTHROPIC_"
	anthropicModelMarker  = "MODEL"
	apiKeyApprovalSuffix  = 20
	providerEnvNameMaxLen = 128
)

// providerCommonEnv are passed for every provider: model selection and output
// limits.
var providerCommonEnv = []string{"CLAUDE_CODE_MAX_OUTPUT_TOKENS", "MAX_THINKING_TOKENS"}

// providerEnvByProvider are the provider-specific variables passed besides
// providerCommonEnv and the ANTHROPIC_*MODEL* variables.
var providerEnvByProvider = map[string][]string{
	ProviderBedrock: {envUseBedrock, "AWS_PROFILE", "AWS_REGION", "AWS_DEFAULT_REGION"},
	ProviderVertex:  {envUseVertex, "CLOUD_ML_REGION", "ANTHROPIC_VERTEX_PROJECT_ID"},
}

// ProviderEnv picks the operator's LLM provider and the variables a container
// session passes for it, from the env block of the operator's
// ~/.claude/settings.json (settingsEnv) and the process environment (environ,
// KEY=VALUE entries as os.Environ returns them). A settings value wins over
// the process's, as it does in Claude Code itself.
//
// Only provider selection, region, profile, model and output-limit variables
// are kept: CLAUDE_CODE_USE_BEDROCK, AWS_PROFILE, AWS_REGION and
// AWS_DEFAULT_REGION for Bedrock; CLAUDE_CODE_USE_VERTEX, CLOUD_ML_REGION and
// ANTHROPIC_VERTEX_PROJECT_ID for Vertex; the ANTHROPIC_*MODEL* variables,
// CLAUDE_CODE_MAX_OUTPUT_TOKENS and MAX_THINKING_TOKENS for all. For the
// Anthropic API, ANTHROPIC_API_KEY is taken from the process environment
// only, never from a file. GOOGLE_APPLICATION_CREDENTIALS is returned
// separately as googleCredentials (a host path, which only a mount makes
// meaningful in the container).
func ProviderEnv(settingsEnv map[string]string, environ []string) (provider string, env map[string]string, googleCredentials string) {
	process := map[string]string{}
	for _, kv := range environ {
		if k, v, ok := strings.Cut(kv, "="); ok && k != "" {
			process[k] = v
		}
	}
	get := func(k string) string {
		if v, ok := settingsEnv[k]; ok {
			return v
		}
		return process[k]
	}
	provider = ProviderAnthropic
	if truthy(get(envUseBedrock)) {
		provider = ProviderBedrock
	} else if truthy(get(envUseVertex)) {
		provider = ProviderVertex
	}

	env = map[string]string{}
	keep := func(k string) {
		if v := get(k); v != "" {
			env[k] = v
		}
	}
	for _, k := range append(append([]string{}, providerCommonEnv...), providerEnvByProvider[provider]...) {
		keep(k)
	}
	for _, src := range []map[string]string{settingsEnv, process} {
		for k := range src {
			if isModelEnv(k) {
				keep(k)
			}
		}
	}
	switch provider {
	case ProviderAnthropic:
		if v := process[envAnthropicAPIKey]; v != "" {
			env[envAnthropicAPIKey] = v
		}
	case ProviderVertex:
		googleCredentials = get(envGoogleCredentials)
	}
	return provider, env, googleCredentials
}

// isModelEnv reports whether k is an ANTHROPIC_*MODEL* variable (a model id
// or a model's region), never one carrying a key or token.
func isModelEnv(k string) bool {
	if len(k) > providerEnvNameMaxLen || !strings.HasPrefix(k, anthropicModelPrefix) ||
		!strings.Contains(strings.TrimPrefix(k, anthropicModelPrefix), anthropicModelMarker) {
		return false
	}
	for _, bad := range []string{"KEY", "TOKEN", "SECRET", "PASSWORD", "AUTH"} {
		if strings.Contains(k, bad) {
			return false
		}
	}
	return true
}

func truthy(v string) bool {
	switch strings.ToLower(strings.TrimSpace(v)) {
	case "1", "true", "yes", "on":
		return true
	}
	return false
}

// ProviderSources are the resolved host paths of the operator's provider
// credentials to share; an empty field shares nothing for it.
type ProviderSources struct {
	// AWSDir is the operator's ~/.aws.
	AWSDir string
	// GCloudDir is the operator's ~/.config/gcloud.
	GCloudDir string
	// GoogleCredentials is the file GOOGLE_APPLICATION_CREDENTIALS names.
	GoogleCredentials string
}

// ProviderShare is what a container session hands the agent of the
// operator's own LLM-provider login: the variables written into the agent's
// ~/.claude/settings.json env block, the read-only bind mounts of the
// provider's credential files, and the home directories the session's home
// archive creates so a mount below one does not leave it root-owned.
type ProviderShare struct {
	Provider string
	Env      map[string]string
	Mounts   []Mount
	// Dirs are home-relative directories to create in the home archive.
	Dirs []string
}

// NewProviderShare builds the share for provider from the filtered env
// (ProviderEnv) and the resolved sources. Only the sources provider uses are
// mounted: ~/.aws for Bedrock, ~/.config/gcloud and the
// GOOGLE_APPLICATION_CREDENTIALS file for Vertex (its variable is pointed at
// the mounted copy), none for the Anthropic API.
func NewProviderShare(provider string, env map[string]string, src ProviderSources) (ProviderShare, error) {
	share := ProviderShare{Provider: provider, Env: map[string]string{}}
	for k, v := range env {
		share.Env[k] = v
	}
	bind := func(source, target string) {
		share.Mounts = append(share.Mounts, Mount{Type: "bind", Source: source, Target: target, ReadOnly: true})
	}
	switch provider {
	case ProviderBedrock:
		if src.AWSDir != "" {
			bind(src.AWSDir, AWSConfigTarget)
		}
	case ProviderVertex:
		if src.GCloudDir != "" {
			bind(src.GCloudDir, GCloudConfigTarget)
			share.Dirs = append(share.Dirs, ".config")
		}
		if src.GoogleCredentials != "" {
			bind(src.GoogleCredentials, GoogleCredentialsTarget)
			share.Env[envGoogleCredentials] = GoogleCredentialsTarget
		}
	case ProviderAnthropic:
	default:
		return ProviderShare{}, fmt.Errorf("unknown LLM provider %q", provider)
	}
	if err := ValidateProviderMounts(share.Mounts); err != nil {
		return ProviderShare{}, err
	}
	return share, nil
}

// EnvNames are the share's variable names, sorted, for display: values are
// never printed.
func (s ProviderShare) EnvNames() []string {
	names := make([]string, 0, len(s.Env))
	for k := range s.Env {
		names = append(names, k)
	}
	sort.Strings(names)
	return names
}

// ValidateProviderMounts refuses a provider mount that is not a read-only bind
// of an absolute host path onto one of the provider targets.
func ValidateProviderMounts(mounts []Mount) error {
	seen := map[string]bool{}
	for _, m := range mounts {
		if m.Type != "bind" || !m.ReadOnly {
			return fmt.Errorf("provider mount %s must be a read-only bind mount", m.Target)
		}
		if !path.IsAbs(m.Source) || path.Clean(m.Source) != m.Source || m.Source == "/" {
			return fmt.Errorf("provider mount source %q must be a clean absolute path below /", m.Source)
		}
		ok := false
		for _, t := range providerTargets {
			ok = ok || m.Target == t
		}
		if !ok {
			return fmt.Errorf("provider mount target %q is not a provider credential path", m.Target)
		}
		if seen[m.Target] {
			return errors.New("provider mount target " + m.Target + " given twice")
		}
		seen[m.Target] = true
	}
	return nil
}

// apiKeyApproval is Claude Code's record that the operator approved the
// ANTHROPIC_API_KEY it finds in its environment: the key's last 20
// characters, so the agent does not stop at the custom-key prompt.
func apiKeyApproval(key string) map[string]any {
	tail := key
	if len(tail) > apiKeyApprovalSuffix {
		tail = tail[len(tail)-apiKeyApprovalSuffix:]
	}
	return map[string]any{"approved": []string{tail}, "rejected": []string{}}
}
