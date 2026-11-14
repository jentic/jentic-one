// Package gitauth provides URL-scoped git configuration environment variables
// and child-process environment scrubbing for installer and updater commands.
package gitauth

import (
	"encoding/base64"
	"os"
	"strings"
)

// githubScopedExtraHeaderKey scopes the git HTTP Authorization header to
// https://github.com/ so git redirects or submodules on other hosts do not
// receive the GitHub token.
const githubScopedExtraHeaderKey = "http.https://github.com/.extraheader"

// Env returns git environment variables (requiring git >= 2.31) that configure
// a URL-scoped HTTP Basic Authorization header for token without placing the
// credential in process argv. It returns nil when token is empty.
func Env(token string) []string {
	if token == "" {
		return nil
	}
	basic := base64.StdEncoding.EncodeToString([]byte("x-access-token:" + token))
	return []string{
		"GIT_CONFIG_COUNT=1",
		"GIT_CONFIG_KEY_0=" + githubScopedExtraHeaderKey,
		"GIT_CONFIG_VALUE_0=Authorization: Basic " + basic,
	}
}

// ScrubbedEnv returns a copy of os.Environ() with secret-bearing variables
// removed unless explicitly named in keep. It drops GITHUB_TOKEN,
// JENTIC_BEARER_TOKEN, JENTIC_MCP_BEARER, JENTIC_SESSION_ID, and any variable
// whose name ends in _TOKEN, _SECRET, or _PASSWORD.
func ScrubbedEnv(keep ...string) []string {
	return scrubEnviron(os.Environ(), keep...)
}

func scrubEnviron(environ []string, keep ...string) []string {
	allowed := make(map[string]bool, len(keep))
	for _, k := range keep {
		allowed[strings.ToUpper(k)] = true
	}
	out := make([]string, 0, len(environ))
	for _, entry := range environ {
		key, _, _ := strings.Cut(entry, "=")
		upper := strings.ToUpper(key)
		if allowed[upper] || !isScrubbedKey(upper) {
			out = append(out, entry)
		}
	}
	return out
}

func isScrubbedKey(upper string) bool {
	switch upper {
	case "GITHUB_TOKEN", "JENTIC_BEARER_TOKEN", "JENTIC_MCP_BEARER", "JENTIC_SESSION_ID":
		return true
	}
	return strings.HasSuffix(upper, "_TOKEN") ||
		strings.HasSuffix(upper, "_SECRET") ||
		strings.HasSuffix(upper, "_PASSWORD")
}
