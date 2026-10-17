// Package serverinfo probes a running jentic-one server for liveness and
// version, used to decorate the help screen and install wizard with the
// server's version alongside the CLI's own, and compares server versions for
// the commands that need a minimum server release.
package serverinfo

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"strings"
	"time"
)

// DefaultTimeout bounds the health probe so interactive surfaces (help screen,
// install wizard) never block noticeably when the server is down or slow.
const DefaultTimeout = 400 * time.Millisecond

// Info is the result of probing the server's health endpoint.
type Info struct {
	// Running reports whether GET {baseURL}/health returned 200.
	Running bool
	// Version is the server's reported version (empty if the server is down or
	// does not report one).
	Version string
}

// healthResponse mirrors the fields we care about from GET /health. The server
// reports its version here (in addition to status/surface).
type healthResponse struct {
	Status  string `json:"status"`
	Version string `json:"version"`
}

// Probe queries {baseURL}/health and reports whether the server is reachable
// and its version. It is best-effort: any error (down, timeout, bad payload)
// yields a zero Info with Running=false.
func Probe(baseURL string, timeout time.Duration) Info {
	base := strings.TrimRight(strings.TrimSpace(baseURL), "/")
	if base == "" {
		return Info{}
	}

	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()

	req, err := http.NewRequestWithContext(ctx, http.MethodGet, base+"/health", nil)
	if err != nil {
		return Info{}
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return Info{}
	}
	defer func() { _ = resp.Body.Close() }()

	if resp.StatusCode != http.StatusOK {
		return Info{}
	}

	var body healthResponse
	// Cap the read so a misbehaving endpoint can't stream unbounded data.
	_ = json.NewDecoder(io.LimitReader(resp.Body, 1<<16)).Decode(&body)
	return Info{Running: true, Version: body.Version}
}

// AtLeast reports whether a server-reported version is at or above minimum.
// Both are compared on their leading MAJOR.MINOR.PATCH numbers; a suffix
// (a PEP 440 pre-release such as "0.44.0rc1", a local "+…" segment) is
// ignored, so a pre-release of the minimum release qualifies. An empty or
// unparsable version is never at least anything: callers gating on a server
// capability must fail closed when the server doesn't say what it runs.
func AtLeast(version, minimum string) bool {
	got, ok := parseRelease(version)
	if !ok {
		return false
	}
	want, ok := parseRelease(minimum)
	if !ok {
		return false
	}
	for i := range got {
		if got[i] != want[i] {
			return got[i] > want[i]
		}
	}
	return true
}

// parseRelease reads the leading MAJOR.MINOR.PATCH of v (a "v" prefix
// allowed; a missing PATCH reads as 0). Each component is its leading digits.
func parseRelease(v string) ([3]int, bool) {
	var out [3]int
	v = strings.TrimPrefix(strings.TrimSpace(v), "v")
	if v == "" {
		return out, false
	}
	parts := strings.SplitN(v, ".", 3)
	if len(parts) < 2 {
		return out, false
	}
	for i, p := range parts {
		n, digits := 0, 0
		for _, r := range p {
			if r < '0' || r > '9' {
				break
			}
			n = n*10 + int(r-'0')
			digits++
			if digits > 9 {
				return out, false
			}
		}
		if digits == 0 {
			return out, false
		}
		out[i] = n
	}
	return out, true
}
