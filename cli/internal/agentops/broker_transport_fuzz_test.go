package agentops

import (
	"encoding/json"
	"math"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func FuzzRetryAfterSecondsAt(f *testing.F) {
	raw, err := os.ReadFile(filepath.Join("..", "..", "..", "tests", "fixtures", "retry-after-parity.json"))
	if err != nil {
		f.Fatalf("read shared Retry-After parity fixture: %v", err)
	}
	var fixture struct {
		Cases []struct {
			Header *string `json:"header"`
		} `json:"cases"`
		HTTPDateNowEpoch float64 `json:"http_date_now_epoch"`
		HTTPDateCases    []struct {
			Header *string `json:"header"`
		} `json:"http_date_cases"`
	}
	if err := json.Unmarshal(raw, &fixture); err != nil {
		f.Fatalf("decode shared Retry-After parity fixture: %v", err)
	}
	for _, tc := range fixture.Cases {
		if tc.Header != nil {
			f.Add(*tc.Header)
		}
	}
	for _, tc := range fixture.HTTPDateCases {
		if tc.Header != nil {
			f.Add(*tc.Header)
		}
	}

	now := time.Unix(int64(fixture.HTTPDateNowEpoch), 0).UTC()
	f.Fuzz(func(t *testing.T, header string) {
		h := http.Header{}
		h.Set("Retry-After", header)
		secs, ok := retryAfterSecondsAt(h, now)
		if ok {
			if math.IsNaN(secs) || math.IsInf(secs, 0) || secs < 0 || secs > maxRetryAfterSeconds {
				t.Fatalf("retryAfterSecondsAt(%q) = (%v, true), out of [0, %d]", header, secs, maxRetryAfterSeconds)
			}
		} else if secs != 0 {
			t.Fatalf("retryAfterSecondsAt(%q) = (%v, false), want 0 when ok=false", header, secs)
		}

		trimmed := strings.TrimSpace(h.Get("Retry-After"))
		if delay, delayOK := parseDelaySeconds(trimmed); delayOK {
			if !ok || secs != float64(delay) {
				t.Fatalf("retryAfterSecondsAt(%q) = (%v, %v), want delay-seconds (%d, true)", header, secs, ok, delay)
			}
		}
	})
}
