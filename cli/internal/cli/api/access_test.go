package api

import (
	"bytes"
	"errors"
	"strings"
	"testing"

	"github.com/jentic/jentic-one/cli/client/generated/control"
	"github.com/jentic/jentic-one/cli/internal/cli/ux"
)

// TestTerminalAccessError pins QA-20: the decided-request status → exit-code
// contract documented in the `access request` help. A scripted agent relies on
// these codes, so they are covered without a live backend via the pure mapper.
func TestTerminalAccessError(t *testing.T) {
	cases := []struct {
		status   string
		wantCode string
		wantExit int
	}{
		{statusPartiallyApproved, ux.CodePartialApproval, ux.ExitPartial},
		{statusDenied, ux.CodeBrokerDenied, ux.ExitDenied},
		{statusExpired, ux.CodeBrokerDenied, ux.ExitDenied},
		{statusWithdrawn, ux.CodeBrokerDenied, ux.ExitDenied},
	}
	for _, tc := range cases {
		t.Run(tc.status, func(t *testing.T) {
			req := &control.AccessRequestResponse{Id: "acr_1", Status: tc.status}
			err := terminalAccessError(req)
			var ce *ux.CodedError
			if !errors.As(err, &ce) {
				t.Fatalf("status %q err = %v, want *ux.CodedError", tc.status, err)
			}
			if ce.Code != tc.wantCode {
				t.Errorf("status %q code = %q, want %q", tc.status, ce.Code, tc.wantCode)
			}
			if ce.ExitCode() != tc.wantExit {
				t.Errorf("status %q exit = %d, want %d", tc.status, ce.ExitCode(), tc.wantExit)
			}
			if ce.Actionable == "" {
				t.Errorf("status %q: expected an actionable status hint", tc.status)
			}
		})
	}

	// Fully approved (and any non-terminal-failure status) is success: no error,
	// so the command exits 0 and a scripted agent proceeds.
	if err := terminalAccessError(&control.AccessRequestResponse{Id: "acr_2", Status: statusApproved}); err != nil {
		t.Errorf("approved should be nil (exit 0), got %v", err)
	}
}

// TestWhoamiFooterDropsContextView pins onboarding-review F2: the whoami footer
// must not promise directories via `context view` (which shows only
// environment/identity/mode). It now points at `jentic doctor` for local setup.
func TestWhoamiFooterDropsContextView(t *testing.T) {
	a := testApp(t)
	a.printMe(&control.MeAgent{Id: "agnt_1", Status: "active"})
	out := a.Out.(*bytes.Buffer).String()
	if strings.Contains(out, "context view") {
		t.Errorf("whoami footer must not reference `context view`:\n%s", out)
	}
	if strings.Contains(strings.ToLower(out), "directories you can access") {
		t.Errorf("whoami footer must not promise directory access:\n%s", out)
	}
	if !strings.Contains(out, "jentic doctor") {
		t.Errorf("whoami footer should point at `jentic doctor`:\n%s", out)
	}
}
