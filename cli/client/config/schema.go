// Package config is the SDK-owned configuration layer: it resolves WHERE to talk
// (Control/Broker URLs) and AS WHOM (identity/environment), from either injected
// environment variables (the file-less path) or ~/.config/jentic/config.yaml.
//
// It is deliberately UX-free. The presentational Mode/Theme concepts are NOT part
// of this package — they live in the CLI's clictx wrapper, which embeds the
// ResolvedState produced here (impl/1.2 §3a, 7.0_public_sdk.md). This keeps
// client/config importable by third parties with no Cobra/UX baggage.
package config

// Config is the on-disk schema for ~/.config/jentic/config.yaml. Environments and
// Identities are decoupled and glued together by Contexts.
//
// Mode/Theme are retained here because they are PERSISTED; only the resolved,
// in-memory representation splits UX out (see ResolvedState). This package reads
// these strings but never interprets them.
type Config struct {
	ActiveContext string              `yaml:"active_context"`
	Contexts      map[string]Context  `yaml:"contexts"`
	Environments  map[string]Env      `yaml:"environments"`
	Identities    map[string]Identity `yaml:"identities"`
	// Theme is global (one presentation preference per machine), deliberately not
	// per-context: you don't want dark in one context and light in another.
	Theme string `yaml:"theme,omitempty"`
}

// Context binds an environment + identity + interaction mode. Mode lives here
// (not on Config) because it is a security/behavior property of the actor: a prod
// admin context is "human" while a CI agent context is "agent" and must stay
// fenced. context use flips the whole I/O + fencing posture atomically.
type Context struct {
	Environment string `yaml:"environment"`
	Identity    string `yaml:"identity"`
	Mode        string `yaml:"mode"` // canonical: "human", "agent", "service-account"
}

// Env is a deployment target. BaseURL and BrokerURL are separate, explicit fields
// because Control Plane and Broker frequently live on different domains in
// enterprise deployments — BrokerURL is NEVER derived from BaseURL.
type Env struct {
	BaseURL string `yaml:"base_url"` // Control Plane (registry, auth, executions)
	// BrokerURL is the Data Plane endpoint used by execute/history (Phase 5).
	// Optional here; NewBroker errors when a broker call is attempted without it.
	BrokerURL string `yaml:"broker_url,omitempty"`
	// CACertPath optionally points at a custom CA bundle for verifying TLS to
	// self-hosted deployments behind a private CA. It never mutates the OS trust
	// store.
	CACertPath string `yaml:"ca_cert_path,omitempty"`
}

// Identity is an actor (agent or user). Per-environment registration state lives
// under Environments, keyed by environment name.
type Identity struct {
	Type         string                 `yaml:"type"` // "agent", "user"
	Environments map[string]EnvRegState `yaml:"environments,omitempty"`
}

// EnvRegState is the registration record for one (identity, environment) pair.
type EnvRegState struct {
	ClientID string `yaml:"client_id"`
	Status   string `yaml:"status"` // "pending", "approved"
}
