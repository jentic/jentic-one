package serverinfo

import (
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func TestProbeReportsVersionWhenHealthy(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/health" {
			http.NotFound(w, r)
			return
		}
		_, _ = w.Write([]byte(`{"status":"ok","surface":"control","version":"9.9.9"}`))
	}))
	defer srv.Close()

	got := Probe(srv.URL, DefaultTimeout)
	if !got.Running {
		t.Fatalf("Probe.Running = false, want true")
	}
	if got.Version != "9.9.9" {
		t.Errorf("Probe.Version = %q, want 9.9.9", got.Version)
	}
}

func TestProbeRunningWithoutVersion(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		_, _ = w.Write([]byte(`{"status":"ok","surface":"control"}`))
	}))
	defer srv.Close()

	got := Probe(srv.URL, DefaultTimeout)
	if !got.Running || got.Version != "" {
		t.Errorf("Probe = %+v, want running with empty version", got)
	}
}

func TestProbeNotRunningOn500(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
	}))
	defer srv.Close()

	if got := Probe(srv.URL, DefaultTimeout); got.Running {
		t.Errorf("Probe.Running = true on 500, want false")
	}
}

func TestProbeNotRunningWhenUnreachable(t *testing.T) {
	// Closed server: connection refused should yield Running=false quickly.
	srv := httptest.NewServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {}))
	url := srv.URL
	srv.Close()

	if got := Probe(url, 200*time.Millisecond); got.Running {
		t.Errorf("Probe.Running = true for closed server, want false")
	}
}

func TestProbeEmptyBaseURL(t *testing.T) {
	if got := Probe("", DefaultTimeout); got.Running {
		t.Errorf("Probe.Running = true for empty base URL, want false")
	}
}

func TestAtLeast(t *testing.T) {
	for _, tc := range []struct {
		version, minimum string
		want             bool
	}{
		{"0.44.0", "0.44.0", true},
		{"v0.44.0", "0.44.0", true},
		{"0.44.1", "0.44.0", true},
		{"0.45.0", "0.44.0", true},
		{"1.0.0", "0.44.0", true},
		{"0.44.0rc1", "0.44.0", true},
		{"0.44.0+local.1", "0.44.0", true},
		{"0.44", "0.44.0", true},
		{"0.43.9", "0.44.0", false},
		{"0.9.0", "0.44.0", false},
		{"", "0.44.0", false},
		{"dev", "0.44.0", false},
		{"0", "0.44.0", false},
		{"0.x.0", "0.44.0", false},
		{"0.44.0", "", false},
		{"9999999999.0.0", "0.44.0", false},
	} {
		if got := AtLeast(tc.version, tc.minimum); got != tc.want {
			t.Errorf("AtLeast(%q, %q) = %v, want %v", tc.version, tc.minimum, got, tc.want)
		}
	}
}

func TestParses(t *testing.T) {
	for v, want := range map[string]bool{
		"0.44.0": true, "v0.44.0": true, "0.44": true, "0.44.0rc1": true, "0.44.0-rc.1": true,
		"": false, "dev": false, "main": false, "0": false, "v": false,
	} {
		if got := Parses(v); got != want {
			t.Errorf("Parses(%q) = %v, want %v", v, got, want)
		}
	}
}
