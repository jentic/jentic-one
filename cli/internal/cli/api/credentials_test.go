package api

import (
	"bytes"
	"encoding/json"
	"errors"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"

	"github.com/jentic/jentic-one/cli/client/auth"
	"github.com/jentic/jentic-one/cli/internal/cli/clictx"
	"github.com/jentic/jentic-one/cli/internal/cli/ux"
)

// captureStreams redirects os.Stdout and os.Stderr around fn so command tests
// can inspect both the rendered payload and the human/agent error report.
func captureStreams(t *testing.T, fn func()) (stdout, stderr string) {
	t.Helper()
	oldOut, oldErr := os.Stdout, os.Stderr
	outR, outW, err := os.Pipe()
	if err != nil {
		t.Fatalf("stdout pipe: %v", err)
	}
	errR, errW, err := os.Pipe()
	if err != nil {
		t.Fatalf("stderr pipe: %v", err)
	}
	os.Stdout, os.Stderr = outW, errW
	defer func() {
		os.Stdout, os.Stderr = oldOut, oldErr
	}()

	var outBuf, errBuf bytes.Buffer
	outDone := make(chan struct{})
	errDone := make(chan struct{})
	go func() {
		_, _ = io.Copy(&outBuf, outR)
		close(outDone)
	}()
	go func() {
		_, _ = io.Copy(&errBuf, errR)
		close(errDone)
	}()

	fn()

	_ = outW.Close()
	_ = errW.Close()
	<-outDone
	<-errDone
	return outBuf.String(), errBuf.String()
}

// runCredsCmd runs the assembled `jentic` command tree in the requested mode
// ("human" or "agent") against baseURL and returns stdout, stderr, and the
// returned error.
func runCredsCmd(t *testing.T, baseURL, mode string, args ...string) (stdout, stderr string, err error) {
	t.Helper()
	withXDG(t)
	t.Setenv("JENTIC_BASE_URL", baseURL)
	t.Setenv("JENTIC_BEARER_TOKEN", "tok_test")
	t.Setenv("JENTIC_MODE", mode)
	t.Setenv("JENTIC_THEME", "no-color")

	a := testApp(t)
	root := newAPIRootCmd(a.App)
	root.SetOut(new(bytes.Buffer))
	root.SetErr(new(bytes.Buffer))
	root.SetArgs(args)

	stdout, stderr = captureStreams(t, func() {
		err = root.Execute()
	})
	return stdout, stderr, err
}

// TestCredentialsShowAndList_SuccessPreservesRedactedOutput pins #1429: 200 OK
// responses for `creds show` and `creds list` (both full walk and single-page
// `--limit`/`--cursor`) render the server-masked payload unchanged across human
// and agent modes.
func TestCredentialsShowAndList_SuccessPreservesRedactedOutput(t *testing.T) {
	const cred1JSON = `{"credential_id":"cred_1","name":"GitHub Prod","active":true,"provider":"oauth","type":"oauth2","api":{"vendor":"github.com","name":"github","version":"v3"},"created_at":"2026-01-01T00:00:00Z"}`
	const cred2JSON = `{"credential_id":"cred_2","name":"Stripe Key","active":true,"provider":"static","type":"api_key","api":{"vendor":"stripe.com","name":"stripe","version":"v1"},"created_at":"2026-01-02T00:00:00Z"}`

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		switch {
		case r.Method == http.MethodGet && r.URL.Path == "/credentials/cred_1":
			_, _ = w.Write([]byte(cred1JSON))
		case r.Method == http.MethodGet && r.URL.Path == "/credentials":
			switch r.URL.Query().Get("cursor") {
			case "":
				if r.URL.Query().Get("limit") == "1" {
					_, _ = w.Write([]byte(`{"data":[` + cred1JSON + `],"has_more":true,"next_cursor":"cur_2"}`))
					return
				}
				_, _ = w.Write([]byte(`{"data":[` + cred1JSON + `],"has_more":true,"next_cursor":"cur_2"}`))
			case "cur_2":
				_, _ = w.Write([]byte(`{"data":[` + cred2JSON + `],"has_more":false,"next_cursor":null}`))
			default:
				w.WriteHeader(http.StatusBadRequest)
				_, _ = w.Write([]byte(`{"detail":"Invalid pagination cursor"}`))
			}
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	defer srv.Close()

	for _, mode := range []string{"agent", "human"} {
		t.Run("show_"+mode, func(t *testing.T) {
			stdout, stderr, err := runCredsCmd(t, srv.URL, mode, "creds", "show", "cred_1")
			if err != nil {
				t.Fatalf("creds show (%s): unexpected error %v (stderr=%q)", mode, err, stderr)
			}
			if stderr != "" {
				t.Errorf("stderr = %q, want empty", stderr)
			}
			var got map[string]any
			if err := json.Unmarshal([]byte(stdout), &got); err != nil {
				t.Fatalf("decode stdout (%s): %v\n%s", mode, err, stdout)
			}
			if got["credential_id"] != "cred_1" || got["name"] != "GitHub Prod" {
				t.Errorf("got = %v, want cred_1 payload", got)
			}
		})

		t.Run("list_walk_"+mode, func(t *testing.T) {
			stdout, stderr, err := runCredsCmd(t, srv.URL, mode, "creds", "list")
			if err != nil {
				t.Fatalf("creds list (%s): unexpected error %v (stderr=%q)", mode, err, stderr)
			}
			if stderr != "" {
				t.Errorf("stderr = %q, want empty", stderr)
			}
			if !strings.Contains(stdout, "cred_1") || !strings.Contains(stdout, "cred_2") {
				t.Errorf("stdout missing walked items (%s):\n%s", mode, stdout)
			}
			if mode == "agent" {
				var page struct {
					SchemaVersion string           `json:"schema_version"`
					Items         []map[string]any `json:"items"`
					NextToken     string           `json:"next_token"`
				}
				if err := json.Unmarshal([]byte(stdout), &page); err != nil {
					t.Fatalf("decode agent page: %v\n%s", err, stdout)
				}
				if page.SchemaVersion == "" || len(page.Items) != 2 || page.NextToken != "" {
					t.Errorf("agent page = %+v, want schema_version + 2 items + empty next_token", page)
				}
			}
		})

		t.Run("list_single_page_"+mode, func(t *testing.T) {
			stdout, stderr, err := runCredsCmd(t, srv.URL, mode, "creds", "list", "--limit", "1")
			if err != nil {
				t.Fatalf("creds list --limit 1 (%s): unexpected error %v (stderr=%q)", mode, err, stderr)
			}
			if stderr != "" {
				t.Errorf("stderr = %q, want empty", stderr)
			}
			if !strings.Contains(stdout, "cred_1") || !strings.Contains(stdout, "cur_2") {
				t.Errorf("single-page stdout missing cred_1 or next cursor cur_2 (%s):\n%s", mode, stdout)
			}
		})
	}
}

// TestCredentialsShow_ErrorClassification pins #1429: `creds show` classifies
// 404 as RESOLVE_FAILED (not found), 401/403 as NOT_AUTHENTICATED with a
// recovery hint, 400/422 (string and array detail) as MISSING_ARGUMENT, and
// 5xx via HTTPError / INTERNAL_ERROR while retaining server problem detail.
func TestCredentialsShow_ErrorClassification(t *testing.T) {
	cases := []struct {
		name           string
		status         int
		contentType    string
		body           string
		wantCode       string
		wantExit       int
		wantMsgSubstr  string
		forbidSubstr   string
		wantStepSubstr string
	}{
		{
			name:           "404 not found with problem detail",
			status:         http.StatusNotFound,
			contentType:    "application/problem+json",
			body:           `{"type":"credential_not_found","title":"Not Found","detail":"Credential 'cred_missing' was not found","status":404}`,
			wantCode:       ux.CodeResolveFailed,
			wantExit:       ux.ExitDenied,
			wantMsgSubstr:  "not found",
			wantStepSubstr: "jentic whoami",
		},
		{
			name:           "404 not found empty body",
			status:         http.StatusNotFound,
			contentType:    "application/problem+json",
			body:           ``,
			wantCode:       ux.CodeResolveFailed,
			wantExit:       ux.ExitDenied,
			wantMsgSubstr:  `credential "cred_missing" not found (status 404)`,
			wantStepSubstr: "jentic credentials list",
		},
		{
			name:           "401 unauthorized is NOT_AUTHENTICATED not not-found",
			status:         http.StatusUnauthorized,
			contentType:    "application/problem+json",
			body:           `{"type":"unauthorized","title":"Unauthorized","detail":"Invalid or expired token","status":401}`,
			wantCode:       ux.CodeNotAuthenticated,
			wantExit:       ux.ExitError,
			wantMsgSubstr:  "Invalid or expired token",
			forbidSubstr:   "not found",
			wantStepSubstr: "jentic register",
		},
		{
			name:           "403 forbidden is NOT_AUTHENTICATED not not-found",
			status:         http.StatusForbidden,
			contentType:    "application/problem+json",
			body:           `{"type":"forbidden","title":"Forbidden","detail":"requires one of: credentials:read, owner:credentials:read","status":403}`,
			wantCode:       ux.CodeNotAuthenticated,
			wantExit:       ux.ExitError,
			wantMsgSubstr:  "credentials:read",
			forbidSubstr:   "not found",
			wantStepSubstr: "jentic whoami",
		},
		{
			name:           "400 bad request classifies as MISSING_ARGUMENT",
			status:         http.StatusBadRequest,
			contentType:    "application/problem+json",
			body:           `{"type":"bad_request","title":"Bad Request","detail":"Malformed credential identifier","status":400}`,
			wantCode:       ux.CodeMissingArgument,
			wantExit:       ux.ExitError,
			wantMsgSubstr:  "Malformed credential identifier",
			forbidSubstr:   "not found",
			wantStepSubstr: "jentic credentials list",
		},
		{
			name:           "422 string detail classifies as MISSING_ARGUMENT",
			status:         http.StatusUnprocessableEntity,
			contentType:    "application/problem+json",
			body:           `{"type":"validation_error","title":"Unprocessable Entity","detail":"credential_id must not be blank","status":422}`,
			wantCode:       ux.CodeMissingArgument,
			wantExit:       ux.ExitError,
			wantMsgSubstr:  "credential_id must not be blank",
			forbidSubstr:   "not found",
			wantStepSubstr: "jentic credentials list",
		},
		{
			name:           "422 FastAPI array detail classifies as MISSING_ARGUMENT without unmarshal failure",
			status:         http.StatusUnprocessableEntity,
			contentType:    "application/json",
			body:           `{"detail":[{"type":"string_too_short","loc":["path","credential_id"],"msg":"String should have at least 3 characters"}]}`,
			wantCode:       ux.CodeMissingArgument,
			wantExit:       ux.ExitError,
			wantMsgSubstr:  "path.credential_id: String should have at least 3 characters",
			forbidSubstr:   "cannot unmarshal",
			wantStepSubstr: "jentic credentials list",
		},
		{
			name:          "500 internal server error retains status and problem detail",
			status:        http.StatusInternalServerError,
			contentType:   "application/problem+json",
			body:          `{"type":"internal_error","title":"Internal Server Error","detail":"credential store unavailable","status":500}`,
			wantCode:      ux.CodeInternalError,
			wantExit:      ux.ExitError,
			wantMsgSubstr: "http 500: credential store unavailable",
			forbidSubstr:  "not found",
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
				if tc.contentType != "" {
					w.Header().Set("Content-Type", tc.contentType)
				}
				w.WriteHeader(tc.status)
				_, _ = w.Write([]byte(tc.body))
			}))
			defer srv.Close()

			for _, mode := range []string{"agent", "human"} {
				t.Run(mode, func(t *testing.T) {
					stdout, stderr, err := runCredsCmd(t, srv.URL, mode, "creds", "show", "cred_missing")
					if err == nil {
						t.Fatalf("expected error for status %d, got nil", tc.status)
					}
					if stdout != "" {
						t.Errorf("stdout = %q, want empty on error", stdout)
					}

					var coded *ux.CodedError
					if !errors.As(err, &coded) {
						t.Fatalf("err = %T (%v), want *ux.CodedError", err, err)
					}
					if coded.Code != tc.wantCode {
						t.Errorf("code = %q, want %q (msg=%q)", coded.Code, tc.wantCode, coded.Msg)
					}
					if coded.ExitCode() != tc.wantExit {
						t.Errorf("exit = %d, want %d", coded.ExitCode(), tc.wantExit)
					}
					if !strings.Contains(coded.Msg, tc.wantMsgSubstr) {
						t.Errorf("msg = %q, want substring %q", coded.Msg, tc.wantMsgSubstr)
					}
					if tc.forbidSubstr != "" && strings.Contains(strings.ToLower(coded.Msg), strings.ToLower(tc.forbidSubstr)) {
						t.Errorf("msg = %q, must not contain %q", coded.Msg, tc.forbidSubstr)
					}
					if tc.wantStepSubstr != "" && !strings.Contains(coded.Actionable, tc.wantStepSubstr) {
						t.Errorf("actionable = %q, want substring %q", coded.Actionable, tc.wantStepSubstr)
					}

					if mode == "agent" {
						var env ux.AgentError
						if err := json.Unmarshal([]byte(stderr), &env); err != nil {
							t.Fatalf("decode agent error envelope: %v\n%s", err, stderr)
						}
						if env.ErrorCode != tc.wantCode {
							t.Errorf("envelope error_code = %q, want %q", env.ErrorCode, tc.wantCode)
						}
						if !strings.Contains(env.Error, tc.wantMsgSubstr) {
							t.Errorf("envelope error = %q, want substring %q", env.Error, tc.wantMsgSubstr)
						}
						if tc.wantStepSubstr != "" && !strings.Contains(env.Actionable, tc.wantStepSubstr) {
							t.Errorf("envelope actionable_step = %q, want substring %q", env.Actionable, tc.wantStepSubstr)
						}
					} else {
						if !strings.Contains(stderr, "Error:") || !strings.Contains(stderr, tc.wantMsgSubstr) {
							t.Errorf("human stderr = %q, want styled error with %q", stderr, tc.wantMsgSubstr)
						}
						if tc.wantStepSubstr != "" && !strings.Contains(stderr, tc.wantStepSubstr) {
							t.Errorf("human stderr = %q, want Next Step containing %q", stderr, tc.wantStepSubstr)
						}
					}
				})
			}
		})
	}
}

// TestCredentialsList_ErrorClassification pins #1429: `creds list` classifies
// 401/403 as NOT_AUTHENTICATED with recovery hints, invalid-cursor 400 and
// invalid-limit 422 (both string and FastAPI/RFC 9457 array detail) as
// MISSING_ARGUMENT with server detail preserved, 404 as RESOLVE_FAILED, and
// 5xx via HTTPError / INTERNAL_ERROR with status and detail intact.
func TestCredentialsList_ErrorClassification(t *testing.T) {
	cases := []struct {
		name           string
		args           []string
		status         int
		contentType    string
		body           string
		wantCode       string
		wantExit       int
		wantMsgSubstr  string
		forbidSubstr   string
		wantStepSubstr string
	}{
		{
			name:           "400 invalid cursor",
			args:           []string{"creds", "list", "--cursor", "garbage"},
			status:         http.StatusBadRequest,
			contentType:    "application/problem+json",
			body:           `{"type":"invalid_cursor","title":"Bad Request","detail":"Invalid pagination cursor","status":400}`,
			wantCode:       ux.CodeMissingArgument,
			wantExit:       ux.ExitError,
			wantMsgSubstr:  "Invalid pagination cursor",
			forbidSubstr:   "unexpected backend response",
			wantStepSubstr: "--cursor",
		},
		{
			name:           "422 invalid limit string detail",
			args:           []string{"creds", "list", "--limit", "1000"},
			status:         http.StatusUnprocessableEntity,
			contentType:    "application/problem+json",
			body:           `{"type":"validation_error","title":"Unprocessable Entity","detail":"limit must be less than or equal to 200","status":422}`,
			wantCode:       ux.CodeMissingArgument,
			wantExit:       ux.ExitError,
			wantMsgSubstr:  "limit must be less than or equal to 200",
			forbidSubstr:   "unexpected backend response",
			wantStepSubstr: "--limit",
		},
		{
			name:           "422 invalid limit FastAPI array detail",
			args:           []string{"creds", "list", "--limit", "1000"},
			status:         http.StatusUnprocessableEntity,
			contentType:    "application/json",
			body:           `{"detail":[{"type":"less_than_equal","loc":["query","limit"],"msg":"Input should be less than or equal to 200"}]}`,
			wantCode:       ux.CodeMissingArgument,
			wantExit:       ux.ExitError,
			wantMsgSubstr:  "query.limit: Input should be less than or equal to 200",
			forbidSubstr:   "cannot unmarshal",
			wantStepSubstr: "--limit",
		},
		{
			name:           "422 array detail with title keeps validation message",
			args:           []string{"creds", "list", "--limit", "1000"},
			status:         http.StatusUnprocessableEntity,
			contentType:    "application/problem+json",
			body:           `{"type":"validation_error","title":"Unprocessable Entity","status":422,"detail":[{"msg":"ensure this value is less than or equal to 200"}]}`,
			wantCode:       ux.CodeMissingArgument,
			wantExit:       ux.ExitError,
			wantMsgSubstr:  "ensure this value is less than or equal to 200",
			forbidSubstr:   "cannot unmarshal",
			wantStepSubstr: "--limit",
		},
		{
			name:           "422 RFC 9457 errors array detail",
			args:           []string{"creds", "list", "--limit", "1000"},
			status:         http.StatusUnprocessableEntity,
			contentType:    "application/problem+json",
			body:           `{"type":"validation_error","title":"Unprocessable Entity","detail":"Request validation failed","errors":[{"parameter":"limit","detail":"must be at most 200"}],"status":422}`,
			wantCode:       ux.CodeMissingArgument,
			wantExit:       ux.ExitError,
			wantMsgSubstr:  "limit: must be at most 200",
			wantStepSubstr: "--limit",
		},
		{
			name:           "401 unauthorized full walk",
			args:           []string{"creds", "list"},
			status:         http.StatusUnauthorized,
			contentType:    "application/problem+json",
			body:           `{"type":"unauthorized","title":"Unauthorized","detail":"Token has expired","status":401}`,
			wantCode:       ux.CodeNotAuthenticated,
			wantExit:       ux.ExitError,
			wantMsgSubstr:  "Token has expired",
			forbidSubstr:   "unexpected backend response",
			wantStepSubstr: "jentic register",
		},
		{
			name:           "403 forbidden single page",
			args:           []string{"creds", "list", "--limit", "10"},
			status:         http.StatusForbidden,
			contentType:    "application/problem+json",
			body:           `{"type":"forbidden","title":"Forbidden","detail":"requires one of: credentials:read, owner:credentials:read","status":403}`,
			wantCode:       ux.CodeNotAuthenticated,
			wantExit:       ux.ExitError,
			wantMsgSubstr:  "credentials:read",
			forbidSubstr:   "unexpected backend response",
			wantStepSubstr: "jentic whoami",
		},
		{
			name:           "404 missing endpoint",
			args:           []string{"creds", "list"},
			status:         http.StatusNotFound,
			contentType:    "application/problem+json",
			body:           `{"type":"not_found","title":"Not Found","detail":"Route not found","status":404}`,
			wantCode:       ux.CodeResolveFailed,
			wantExit:       ux.ExitDenied,
			wantMsgSubstr:  "Route not found",
			wantStepSubstr: "jentic doctor",
		},
		{
			name:          "503 service unavailable retains status and detail via HTTPError",
			args:          []string{"creds", "list"},
			status:        http.StatusServiceUnavailable,
			contentType:   "application/problem+json",
			body:          `{"type":"unavailable","title":"Service Unavailable","detail":"control plane maintenance","status":503}`,
			wantCode:      ux.CodeInternalError,
			wantExit:      ux.ExitError,
			wantMsgSubstr: "http 503: control plane maintenance",
			forbidSubstr:  "unexpected backend response",
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
				if tc.contentType != "" {
					w.Header().Set("Content-Type", tc.contentType)
				}
				w.WriteHeader(tc.status)
				_, _ = w.Write([]byte(tc.body))
			}))
			defer srv.Close()

			for _, mode := range []string{"agent", "human"} {
				t.Run(mode, func(t *testing.T) {
					stdout, stderr, err := runCredsCmd(t, srv.URL, mode, tc.args...)
					if err == nil {
						t.Fatalf("expected error for status %d, got nil", tc.status)
					}
					if stdout != "" {
						t.Errorf("stdout = %q, want empty on error", stdout)
					}

					var coded *ux.CodedError
					if !errors.As(err, &coded) {
						t.Fatalf("err = %T (%v), want *ux.CodedError", err, err)
					}
					if coded.Code != tc.wantCode {
						t.Errorf("code = %q, want %q (msg=%q)", coded.Code, tc.wantCode, coded.Msg)
					}
					if coded.ExitCode() != tc.wantExit {
						t.Errorf("exit = %d, want %d", coded.ExitCode(), tc.wantExit)
					}
					if !strings.Contains(coded.Msg, tc.wantMsgSubstr) {
						t.Errorf("msg = %q, want substring %q", coded.Msg, tc.wantMsgSubstr)
					}
					if tc.forbidSubstr != "" && strings.Contains(strings.ToLower(coded.Msg), strings.ToLower(tc.forbidSubstr)) {
						t.Errorf("msg = %q, must not contain %q", coded.Msg, tc.forbidSubstr)
					}
					if tc.wantStepSubstr != "" && !strings.Contains(coded.Actionable, tc.wantStepSubstr) {
						t.Errorf("actionable = %q, want substring %q", coded.Actionable, tc.wantStepSubstr)
					}

					if mode == "agent" {
						var env ux.AgentError
						if err := json.Unmarshal([]byte(stderr), &env); err != nil {
							t.Fatalf("decode agent error envelope: %v\n%s", err, stderr)
						}
						if env.ErrorCode != tc.wantCode {
							t.Errorf("envelope error_code = %q, want %q", env.ErrorCode, tc.wantCode)
						}
						if !strings.Contains(env.Error, tc.wantMsgSubstr) {
							t.Errorf("envelope error = %q, want substring %q", env.Error, tc.wantMsgSubstr)
						}
					} else if !strings.Contains(stderr, tc.wantMsgSubstr) {
						t.Errorf("human stderr = %q, want %q", stderr, tc.wantMsgSubstr)
					}
				})
			}
		})
	}
}

// TestCredentialsRead_TransportAndNonHTTPFailuresPassThrough pins #1429:
// transport failures without an HTTP response (and SDK auth/config sentinels)
// pass through credentialReadErr untouched so existing asCoded handling holds.
func TestCredentialsRead_TransportAndNonHTTPFailuresPassThrough(t *testing.T) {
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("listen: %v", err)
	}
	closedURL := "http://" + ln.Addr().String()
	_ = ln.Close()

	for _, args := range [][]string{
		{"creds", "list"},
		{"creds", "show", "cred_1"},
	} {
		t.Run(strings.Join(args, "_"), func(t *testing.T) {
			stdout, stderr, runErr := runCredsCmd(t, closedURL, "agent", args...)
			if runErr == nil {
				t.Fatal("expected error against closed port, got nil")
			}
			if stdout != "" {
				t.Errorf("stdout = %q, want empty", stdout)
			}
			var coded *ux.CodedError
			if !errors.As(runErr, &coded) || coded.Code != ux.CodeInternalError {
				t.Errorf("err = %T (%v), want INTERNAL_ERROR via existing asCoded fallback", runErr, runErr)
			}
			if strings.Contains(stderr, "not found") || strings.Contains(stderr, "unexpected backend response") {
				t.Errorf("stderr = %q, transport error must not fabricate an HTTP status error", stderr)
			}
		})
	}

	// Non-HTTP SDK sentinels pass through credentialReadErr untouched so asCoded
	// continues to classify them as NOT_AUTHENTICATED / RESOLVE_FAILED.
	if got := asCoded(credentialReadErr(auth.ErrNotRegistered, "cred_1")); got.Code != ux.CodeNotAuthenticated {
		t.Errorf("ErrNotRegistered code = %q, want %q", got.Code, ux.CodeNotAuthenticated)
	}
	if got := asCoded(credentialReadErr(clictx.ErrNoConfig, "")); got.Code != ux.CodeResolveFailed {
		t.Errorf("ErrNoConfig code = %q, want %q", got.Code, ux.CodeResolveFailed)
	}
}
