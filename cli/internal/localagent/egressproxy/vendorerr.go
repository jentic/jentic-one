package egressproxy

import (
	"encoding/json"
	"net/http"
	"strconv"
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
