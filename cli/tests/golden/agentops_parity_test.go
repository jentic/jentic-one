package golden

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/jentic/jentic-one/cli/internal/agentops"
	"github.com/jentic/jentic-one/cli/internal/cli/cmdcore"
	"github.com/jentic/jentic-one/cli/internal/cli/ux"
)

// TestAgentopsReproducesGoldens is the extraction-parity half of the execute
// contract (phase-0 §0.2): it drives the extracted UX-free core
// (agentops.ResolveOperation → Execute → Classify → Envelope, plus the ux
// directive renderer) DIRECTLY — no cobra, no command tree — against the same
// mock broker responses TestGolden_ExecuteContract recorded, and proves the
// core reproduces the frozen bytes. TestGolden_ExecuteContract keeps proving
// the CLI wiring end-to-end; this test proves the seam extraction itself lost
// nothing, and documents exactly which golden bytes each layer owns (envelope
// + recovery text: agentops/ux; the coded stderr envelope + exit code: the
// cobra/Audience layer).
func TestAgentopsReproducesGoldens(t *testing.T) {
	ctx := context.Background()

	t.Run("execute_ok_json envelope", func(t *testing.T) {
		want := goldenSection(t, "execute_ok_json", "stdout")

		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if r.URL.Path == "/inspect" {
				w.Header()["Date"] = nil
				w.Header().Set("Content-Type", "application/json")
				_, _ = w.Write([]byte(`{"method":"GET","url":"https://upstream.example/v1/pets"}`))
				return
			}
			w.Header()["Date"] = nil
			w.Header().Set("Content-Type", "application/json")
			w.Header().Set("Jentic-Execution-Id", "exec-123")
			w.WriteHeader(http.StatusOK)
			_, _ = w.Write([]byte(`[{"id":1,"name":"Fido"}]`))
		}))
		t.Cleanup(srv.Close)

		op, err := agentops.ResolveOperation(ctx, rawInspector{srv.URL}, "listPets", "")
		if err != nil {
			t.Fatalf("ResolveOperation: %v", err)
		}
		res, err := agentops.Execute(ctx, agentops.ExecuteRequest{
			Method:       op.Method,
			URL:          op.URL,
			BrokerScheme: "http",
			BrokerHost:   srv.Listener.Addr().String(),
			Token:        "tok_abc",
		})
		if err != nil {
			t.Fatalf("Execute: %v", err)
		}
		if d := agentops.Classify(res); d != nil {
			t.Fatalf("Classify(2xx) = %+v, want nil (exit 0 stays caller-side)", d)
		}

		// Serialize through the same legacy path the CLI uses (WriteJSON:
		// indent-2 + redaction backstop) so the comparison is byte-exact.
		var got bytes.Buffer
		if err := cmdcore.WriteJSON(&got, res.Envelope()); err != nil {
			t.Fatalf("WriteJSON: %v", err)
		}
		if got.String() != want {
			t.Errorf("agentops envelope diverged from the golden.\n--- want ---\n%s\n--- got ---\n%s", want, got.String())
		}
	})

	t.Run("execute_broker_denial_directive_json classification and recovery", func(t *testing.T) {
		wantStdout := goldenSection(t, "execute_broker_denial_directive_json", "stdout")
		wantStderr := goldenSection(t, "execute_broker_denial_directive_json", "stderr")

		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
			w.Header()["Date"] = nil
			w.Header().Set("Content-Type", "application/problem+json")
			w.Header().Set("Jentic-Error-Origin", "broker")
			w.WriteHeader(http.StatusForbidden)
			_, _ = w.Write([]byte(`{
			"type": "no_toolkit_binding",
			"title": "No toolkit binding for this API",
			"status": 403,
			"error_origin": "broker",
			"agent_directive": {
				"strategy": "wait",
				"parameters": {
					"suggested_command": "jentic access request --toolkit acme/pets --wait",
					"provisioning_url": "https://console.example/connect/acme",
					"candidates": ["acme/pets", "acme/pets-admin"],
					"retry_after_seconds": 30
				},
				"human_readable_instruction": "You are not bound to a toolkit for this API."
			}
		}`))
		}))
		t.Cleanup(srv.Close)

		// GET:/v1/pets is the broker-relative short-circuit: no Inspector call.
		op, err := agentops.ResolveOperation(ctx, nil, "GET:/v1/pets", "")
		if err != nil {
			t.Fatalf("ResolveOperation: %v", err)
		}
		res, err := agentops.Execute(ctx, agentops.ExecuteRequest{
			Method:       op.Method,
			Path:         op.Path,
			BrokerScheme: "http",
			BrokerHost:   srv.Listener.Addr().String(),
			Token:        "tok_abc",
		})
		if err != nil {
			t.Fatalf("Execute: %v", err)
		}

		var gotStdout bytes.Buffer
		if err := cmdcore.WriteJSON(&gotStdout, res.Envelope()); err != nil {
			t.Fatalf("WriteJSON: %v", err)
		}
		if gotStdout.String() != wantStdout {
			t.Errorf("denial envelope diverged from the golden.\n--- want ---\n%s\n--- got ---\n%s", wantStdout, gotStdout.String())
		}

		denial := agentops.Classify(res)
		if denial == nil || denial.Status != http.StatusForbidden || denial.Directive == nil {
			t.Fatalf("Classify = %+v, want 403 denial with directive", denial)
		}

		// The stderr golden is (recovery text rendered by ux) + (the coded error
		// envelope the Audience layer emits from denial.Err()). The renderer must
		// reproduce the recovery block byte-for-byte; the trailing envelope line
		// is the Audience's, so here we assert denial.Err() carries exactly the
		// fields frozen in it.
		var recovery bytes.Buffer
		ux.RenderDirective(ctx, &recovery, *denial.Directive)
		if !strings.HasPrefix(wantStderr, recovery.String()) {
			t.Errorf("ux.RenderDirective diverged from the golden recovery block.\n--- want prefix of ---\n%s\n--- got ---\n%s",
				wantStderr, recovery.String())
		}
		coded := denial.Err()
		if coded.Code != ux.CodeBrokerDenied {
			t.Errorf("denial.Err().Code = %q, want BROKER_DENIED", coded.Code)
		}
		rest := strings.TrimPrefix(wantStderr, recovery.String())
		for _, frozen := range []string{`"error_code":"BROKER_DENIED"`, `"http_status":403`, jsonField(t, "error", coded.Msg)} {
			if !strings.Contains(rest, frozen) {
				t.Errorf("golden stderr envelope %q does not carry %s from denial.Err()", strings.TrimSpace(rest), frozen)
			}
		}
	})

	t.Run("execute_resolve_not_found_json coded error", func(t *testing.T) {
		wantStderr := goldenSection(t, "execute_resolve_not_found_json", "stderr")

		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
			w.Header()["Date"] = nil
			w.Header().Set("Content-Type", "application/problem+json")
			w.WriteHeader(http.StatusNotFound)
			_, _ = w.Write([]byte(`{"detail":"operation not found"}`))
		}))
		t.Cleanup(srv.Close)

		_, err := agentops.ResolveOperation(ctx, rawInspector{srv.URL}, "nonexistentOp", "")
		var coded *ux.CodedError
		if !errors.As(err, &coded) {
			t.Fatalf("ResolveOperation 404 returned %T (%v), want *ux.CodedError", err, err)
		}
		if coded.Code != ux.CodeResolveFailed {
			t.Errorf("code = %q, want RESOLVE_FAILED", coded.Code)
		}
		// The golden stderr envelope is the Audience's serialization of exactly
		// this coded error — its frozen fields must all come from it.
		for _, frozen := range []string{
			`"error_code":"RESOLVE_FAILED"`,
			jsonField(t, "error", coded.Msg),
			jsonField(t, "actionable_step", coded.Actionable),
		} {
			if !strings.Contains(wantStderr, frozen) {
				t.Errorf("golden stderr %q does not carry %s from the agentops coded error", strings.TrimSpace(wantStderr), frozen)
			}
		}
	})
}

// jsonField renders `"key":<json(value)>` exactly as encoding/json would emit
// it inside the Audience's error envelope (incl. its HTML-safe \u003c escapes),
// so a Contains check compares like for like.
func jsonField(t *testing.T, key string, value any) string {
	t.Helper()
	b, err := json.Marshal(value)
	if err != nil {
		t.Fatalf("marshal %s: %v", key, err)
	}
	return fmt.Sprintf("%q:%s", key, b)
}

// rawInspector is the minimal agentops.Inspector for parity tests: it fetches
// the mock control plane's /inspect and mirrors the api layer's error mapping
// (non-2xx → *agentops.HTTPError), which is all ResolveOperation depends on.
type rawInspector struct{ baseURL string }

func (i rawInspector) Inspect(ctx context.Context, _, _, _ string) ([]byte, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, i.baseURL+"/inspect", nil)
	if err != nil {
		return nil, err
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, err
	}
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return nil, &agentops.HTTPError{StatusCode: resp.StatusCode, Body: strings.TrimSpace(string(body))}
	}
	return body, nil
}

// goldenSection extracts one recorded stream from a golden file ("stdout" or
// "stderr"), trailing content up to the next marker (or EOF) inclusive of its
// final newline — the exact bytes formatResult wrote for that stream.
func goldenSection(t *testing.T, name, section string) string {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join("testdata", "golden", "v2", name+".txt"))
	if err != nil {
		t.Fatalf("read golden %s: %v", name, err)
	}
	content := string(raw)
	_, afterStdout, ok := strings.Cut(content, "--- stdout ---\n")
	if !ok {
		t.Fatalf("golden %s has no stdout marker", name)
	}
	stdout, stderr, ok := strings.Cut(afterStdout, "--- stderr ---\n")
	if !ok {
		t.Fatalf("golden %s has no stderr marker", name)
	}
	switch section {
	case "stdout":
		return stdout
	case "stderr":
		return stderr
	default:
		t.Fatalf("unknown golden section %q", section)
		return ""
	}
}
