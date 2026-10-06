package api

import (
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/jentic/jentic-one/cli/internal/cli/ux"
)

// TestCredentialsCheck pins `jentic credentials check` (#630): it POSTs the
// check, prints the verdict, and maps it onto the exit-code contract (fix the
// credential: exit 2; unreachable, worth a retry: exit 1; ok/untested: exit 0).
func TestCredentialsCheck(t *testing.T) {
	cases := []struct {
		status   string
		wantCode string // "" = no error
	}{
		{"ok", ""},
		{"untested", ""},
		{"bad_key", ux.CodeResolveFailed},
		{"expired", ux.CodeResolveFailed},
		{"missing_scope", ux.CodeResolveFailed},
		{"wrong_base_url", ux.CodeResolveFailed},
		{"unreachable", ux.CodeTransportError},
	}
	for _, tc := range cases {
		t.Run(tc.status, func(t *testing.T) {
			withXDG(t)
			var gotMethod, gotPath string
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				gotMethod, gotPath = r.Method, r.URL.Path
				w.Header().Set("Content-Type", "application/json")
				_, _ = fmt.Fprintf(w, `{"status":%q,"reason":"api.example.com said so.","probe":"GET https://api.example.com/v1/me","upstream_status":401}`, tc.status)
			}))
			defer srv.Close()

			out, err := runConnectTree(t, srv.URL, "credentials", "check", "cred_1")

			if gotMethod != http.MethodPost || gotPath != "/credentials/cred_1:check" {
				t.Fatalf("request = %s %s, want POST /credentials/cred_1:check", gotMethod, gotPath)
			}
			if !strings.Contains(out, tc.status) || !strings.Contains(out, "GET https://api.example.com/v1/me") {
				t.Errorf("verdict not rendered:\n%s", out)
			}
			var coded *ux.CodedError
			switch {
			case tc.wantCode == "" && err != nil:
				t.Fatalf("want success, got %v", err)
			case tc.wantCode != "" && (!errors.As(err, &coded) || coded.Code != tc.wantCode):
				t.Fatalf("want %s, got %v", tc.wantCode, err)
			case coded != nil && !strings.Contains(coded.Msg, "said so"):
				t.Errorf("error should carry the reason, got %q", coded.Msg)
			}
		})
	}
}

func TestCredentialsCheckUnknownCredential(t *testing.T) {
	withXDG(t)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/problem+json")
		w.WriteHeader(http.StatusNotFound)
		_, _ = w.Write([]byte(`{"type":"credential_not_found","title":"Not Found","status":404}`))
	}))
	defer srv.Close()

	_, err := runConnectTree(t, srv.URL, "credentials", "check", "cred_nope")
	var coded *ux.CodedError
	if !errors.As(err, &coded) || coded.Code != ux.CodeResolveFailed || coded.ExitCode() != ux.ExitDenied {
		t.Fatalf("want RESOLVE_FAILED exit 2, got %v", err)
	}
}
