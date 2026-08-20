package cmdcore

// APIKeyPrefix is the prefix the control-plane assigns to agent API keys.
const APIKeyPrefix = "jak_"

// MaskAPIKey renders a key as its prefix plus the last 4 chars, hiding the body.
func MaskAPIKey(key string) string {
	if len(key) <= len(APIKeyPrefix)+4 {
		return APIKeyPrefix + "…"
	}
	return key[:len(APIKeyPrefix)] + "…" + key[len(key)-4:]
}

// APIKeyLabel masks a stored key, or reports its absence.
func APIKeyLabel(key string) string {
	if key == "" {
		return "missing"
	}
	return MaskAPIKey(key)
}
