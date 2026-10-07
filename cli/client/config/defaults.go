package config

// Default control-plane and broker endpoints for a local Jentic install.
//
// These live in the public SDK config package (ARCH-3) — not internal/config —
// because they are part of the SDK's connection contract: a downstream importer
// constructing a client.Config, or resolving a broker target, needs the same
// canonical defaults the CLI uses, without reaching into a CLI-private package.
// internal/config re-exports these so existing CLI call sites are unchanged.
const (
	// DefaultBaseURL is the Jentic control-plane (auth surface) base URL used
	// for agent registration and token minting when no environment overrides it.
	// Loopback + http by design: the out-of-the-box target is a local install
	// (`jenticctl install`), and 127.0.0.1 (not localhost) so the token-exchange
	// audience matches the backend's canonical_base_url byte-for-byte.
	DefaultBaseURL = "http://127.0.0.1:8000"

	// DefaultBrokerScheme is the scheme of the broker target used by execute.
	DefaultBrokerScheme = "https"

	// DefaultBrokerHost is the host of the broker target used by execute. It is a
	// bare host[:port] with NO scheme — the scheme lives in DefaultBrokerScheme.
	// Callers assemble the URL as scheme + "://" + host, so embedding a scheme
	// here would double it.
	DefaultBrokerHost = "127.0.0.1:8100"
)
