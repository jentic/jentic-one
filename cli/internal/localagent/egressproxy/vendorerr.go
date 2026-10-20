package egressproxy

import (
	"encoding/json"
	"net/http"
	"strconv"
	"strings"
)

// The broker answers a relayed call's errors in the vendor's own shape
// (keyed on the upstream host), so the proxy passes those through untouched.
// writeVendorError covers the errors the proxy itself raises inside a
// governed TLS session (the broker is unreachable, a request it will not
// relay), in the same shapes, so the vendor CLI prints the message instead of
// failing to parse a body it does not expect.
func writeVendorError(w http.ResponseWriter, vendor string, status int, message string) {
	message = "Jentic: " + message
	var body any
	switch vendor {
	case "github":
		body = map[string]string{"message": message, "status": strconv.Itoa(status)}
	case "cloudflare":
		body = map[string]any{
			"success":  false,
			"errors":   []map[string]any{{"code": status, "message": message}},
			"messages": []any{},
			"result":   nil,
		}
	default:
		body = map[string]string{"__type": "JenticEgressProxyError", "message": message}
	}
	data, _ := json.Marshal(body)
	h := w.Header()
	h.Set("Content-Type", "application/json; charset=utf-8")
	h.Set("Content-Length", strconv.Itoa(len(data)))
	w.WriteHeader(status)
	_, _ = w.Write(data)
}

// gitUnsupportedMessage is what git prints (as "remote: …" lines) when it
// reaches a governed host's smart-HTTP endpoints.
const gitUnsupportedMessage = "Jentic: git fetch, clone and push over HTTPS through Jentic are not supported yet.\n" +
	"This session cannot reach the remote's git endpoints; vendor API calls (gh, curl to the API) still work.\n"

// isGitSmartHTTP reports whether a request path and query are a git HTTP
// exchange: ref discovery (`…/info/refs`, with or without
// `?service=git-…`) or a pack RPC (`…/git-upload-pack`, `…/git-receive-pack`,
// `…/git-upload-archive`).
func isGitSmartHTTP(path string) bool {
	switch {
	case strings.HasSuffix(path, "/info/refs"):
		return true
	case strings.HasSuffix(path, "/git-upload-pack"),
		strings.HasSuffix(path, "/git-receive-pack"),
		strings.HasSuffix(path, "/git-upload-archive"):
		return true
	}
	return false
}

// writeGitUnsupported answers a git smart-HTTP request with 403 and a
// text/plain body, which git shows to the user before it gives up.
func writeGitUnsupported(w http.ResponseWriter) {
	h := w.Header()
	h.Set("Content-Type", "text/plain; charset=utf-8")
	h.Set("Content-Length", strconv.Itoa(len(gitUnsupportedMessage)))
	h.Set("Cache-Control", "no-cache")
	w.WriteHeader(http.StatusForbidden)
	_, _ = w.Write([]byte(gitUnsupportedMessage))
}
