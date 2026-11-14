package cmdcore

import "github.com/jentic/jentic-one/cli/client/auth"

// APIKeyPrefix = "jak_"
// (aliased from client/auth).
const APIKeyPrefix = auth.APIKeyPrefix // pragma: allowlist secret

// MaskAPIKey renders a key as its prefix plus the last 4 chars, hiding the body.
func MaskAPIKey(key string) string {
	if len(key) <= len(APIKeyPrefix)+4 {
		return APIKeyPrefix + "…"
	}
	return key[:len(APIKeyPrefix)] + "…" + key[len(key)-4:]
}
