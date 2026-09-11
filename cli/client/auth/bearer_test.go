package auth

import (
	"errors"
	"io"
	"net/http"
	"strings"
	"testing"
	"time"
)

// BearerToken is the exported credential-resolution half of AttachAuth, used by
// the CLI's context-first bridge (hand-rolled clients must present exactly the
// credential the SDK request editor would). These tests pin its resolution
// order: injected token > jak_* API key > cached disk token, and that
// RefreshBearerToken really drops the cache instead of returning it.

func TestBearerToken_InjectedTokenWins(t *testing.T) {
	withConfigDir(t)
	ref := IdentityRef{Identity: "a1", Environment: "e1"}
	if err := SaveAPIKey(ref, "jak_stored"); err != nil {
		t.Fatal(err)
	}
	creds := Credentials{
		IdentityName: "a1", EnvironmentName: "e1",
		InjectedBearerToken: "tok_injected",
	}
	got, err := BearerToken(creds)
	if err != nil {
		t.Fatalf("BearerToken: %v", err)
	}
	if got != "tok_injected" {
		t.Errorf("BearerToken = %q, want the injected token over the stored key", got)
	}
}

func TestBearerToken_APIKeyBeatsCachedToken(t *testing.T) {
	withConfigDir(t)
	ref := IdentityRef{Identity: "a1", Environment: "e1"}
	if err := SaveAPIKey(ref, "jak_stored"); err != nil {
		t.Fatal(err)
	}
	if err := SaveTokens(ref, &TokenSet{AccessToken: "tok_cached", ExpiresAt: time.Now().Add(time.Hour)}); err != nil {
		t.Fatal(err)
	}
	got, err := BearerToken(Credentials{IdentityName: "a1", EnvironmentName: "e1"})
	if err != nil {
		t.Fatalf("BearerToken: %v", err)
	}
	if got != "jak_stored" {
		t.Errorf("BearerToken = %q, want the API key (matches AttachAuth order)", got)
	}
}

func TestBearerToken_ReturnsFreshCachedToken(t *testing.T) {
	withConfigDir(t)
	ref := IdentityRef{Identity: "a1", Environment: "e1"}
	if err := SaveTokens(ref, &TokenSet{AccessToken: "tok_cached", ExpiresAt: time.Now().Add(time.Hour)}); err != nil {
		t.Fatal(err)
	}
	got, err := BearerToken(Credentials{IdentityName: "a1", EnvironmentName: "e1"})
	if err != nil {
		t.Fatalf("BearerToken: %v", err)
	}
	if got != "tok_cached" {
		t.Errorf("BearerToken = %q, want the fresh cached token (no exchange)", got)
	}
}

// TestBearerToken_UnregisteredFailsWithErrNotRegistered: no injected token, no
// API key, no cached token → the exchange path runs and, with no client_id on
// disk, must surface ErrNotRegistered (which the CLI maps to the
// `jentic identity register` remediation) without any network I/O.
func TestBearerToken_UnregisteredFailsWithErrNotRegistered(t *testing.T) {
	withConfigDir(t)
	_, err := BearerToken(Credentials{
		BaseURL: "http://127.0.0.1:0", IdentityName: "a1", EnvironmentName: "e1",
	})
	if err == nil {
		t.Fatal("expected BearerToken to fail for an unregistered identity")
	}
	if !errors.Is(err, ErrNotRegistered) {
		t.Errorf("error = %v, want ErrNotRegistered in the chain", err)
	}
}

// TestRefreshBearerToken_DropsCache: a fresh cached token must NOT satisfy a
// refresh — the point of the call is to re-read server-side grants, so the
// cache is invalidated first and (here, unregistered) the exchange fails
// rather than returning the stale token.
func TestRefreshBearerToken_DropsCache(t *testing.T) {
	withConfigDir(t)
	ref := IdentityRef{Identity: "a1", Environment: "e1"}
	if err := SaveTokens(ref, &TokenSet{AccessToken: "tok_stale", ExpiresAt: time.Now().Add(time.Hour)}); err != nil {
		t.Fatal(err)
	}
	_, err := RefreshBearerToken(Credentials{
		BaseURL: "http://127.0.0.1:0", IdentityName: "a1", EnvironmentName: "e1",
	})
	if err == nil {
		t.Fatal("expected refresh to force an exchange (and fail unregistered), not return the cached token")
	}
	if tokens, rerr := ReadTokens(ref); rerr == nil && tokens != nil && tokens.AccessToken == "tok_stale" {
		t.Error("stale token survived RefreshBearerToken")
	}
}

func TestRefreshBearerToken_StaticCredentialsPassThrough(t *testing.T) {
	withConfigDir(t)
	ref := IdentityRef{Identity: "a1", Environment: "e1"}
	if err := SaveAPIKey(ref, "jak_stored"); err != nil {
		t.Fatal(err)
	}
	got, err := RefreshBearerToken(Credentials{IdentityName: "a1", EnvironmentName: "e1"})
	if err != nil {
		t.Fatalf("RefreshBearerToken: %v", err)
	}
	if got != "jak_stored" {
		t.Errorf("RefreshBearerToken = %q, want the static API key as-is", got)
	}
}

// TestClassifyTokenError_PendingShapes pins that BOTH wire shapes of a pending
// 400 classify as *PendingError: the RFC 6749 OAuth shape the shipped backend's
// token endpoint now emits ({"error": "invalid_grant"} — the #1252 §5.2
// dialect reshaping), and the RFC 7807 problem-details shape older backends
// emitted ({"type": "invalid_grant"} — auth/web/errors.py). The V2 port
// originally read only the OAuth key, so a real pending agent surfaced as a
// hard "token exchange failed (status 400)" instead of the approval wait.
func TestClassifyTokenError_PendingShapes(t *testing.T) {
	cases := map[string]string{
		"problem-details": `{"type":"invalid_grant","status":400,"detail":"agent pending approval","instance":"/oauth/token"}`,
		"oauth":           `{"error":"invalid_grant","error_description":"agent pending approval"}`,
	}
	for name, body := range cases {
		resp := &http.Response{
			StatusCode: http.StatusBadRequest,
			Body:       io.NopCloser(strings.NewReader(body)),
		}
		err := classifyTokenError(resp)
		var pending *PendingError
		if !errors.As(err, &pending) {
			t.Errorf("%s: classified as %v, want *PendingError", name, err)
			continue
		}
		if pending.Detail != "agent pending approval" {
			t.Errorf("%s: detail = %q", name, pending.Detail)
		}
	}

	// A NON-pending 400 must stay a hard error carrying the code.
	resp := &http.Response{
		StatusCode: http.StatusBadRequest,
		Body:       io.NopCloser(strings.NewReader(`{"type":"actor_not_found","detail":"gone"}`)),
	}
	err := classifyTokenError(resp)
	var pending *PendingError
	if errors.As(err, &pending) {
		t.Fatalf("actor_not_found classified as pending")
	}
	if !strings.Contains(err.Error(), "actor_not_found") {
		t.Errorf("error should carry the code: %v", err)
	}
}

// TestClassifyTokenError_Rfc6749DistinctCodes pins the #1252 backend dialect:
// the token endpoint now names conditions with their proper RFC 6749 §5.2 /
// RFC 8628 codes (invalid_client, unsupported_grant_type, slow_down) instead
// of a blanket invalid_grant. None of these are a pending approval — they
// must classify as hard errors carrying the code AND the description, so the
// register wait loop never polls a condition that can't clear.
func TestClassifyTokenError_Rfc6749DistinctCodes(t *testing.T) {
	cases := []struct {
		name   string
		status int
		body   string
		code   string
		desc   string
	}{
		{
			name:   "invalid_client",
			status: http.StatusBadRequest,
			body:   `{"error":"invalid_client","error_description":"client authentication failed"}`,
			code:   "invalid_client",
			desc:   "client authentication failed",
		},
		{
			name:   "unsupported_grant_type",
			status: http.StatusBadRequest,
			body:   `{"error":"unsupported_grant_type","error_description":"unsupported grant_type: password"}`,
			code:   "unsupported_grant_type",
			desc:   "unsupported grant_type: password",
		},
		{
			name:   "slow_down",
			status: http.StatusTooManyRequests,
			body:   `{"error":"slow_down","error_description":"rate limit exceeded, retry later"}`,
			code:   "slow_down",
			desc:   "rate limit exceeded, retry later",
		},
	}
	for _, tc := range cases {
		resp := &http.Response{
			StatusCode: tc.status,
			Body:       io.NopCloser(strings.NewReader(tc.body)),
		}
		err := classifyTokenError(resp)
		var pending *PendingError
		if errors.As(err, &pending) {
			t.Errorf("%s: classified as pending — only 400 invalid_grant may pend", tc.name)
			continue
		}
		if !strings.Contains(err.Error(), tc.code) || !strings.Contains(err.Error(), tc.desc) {
			t.Errorf("%s: error should carry code %q and description %q: %v", tc.name, tc.code, tc.desc, err)
		}
	}
}

// TestClassifyTokenError_AssertionInvalid pins QA-9: a 400 invalid_grant whose
// detail signals a rejected assertion (audience/signature) must NOT be a
// PendingError — otherwise the register wait loop polls forever. It becomes an
// *AssertionInvalidError so the caller can stop with an actionable hint. A
// genuinely-pending detail must remain a PendingError.
func TestClassifyTokenError_AssertionInvalid(t *testing.T) {
	invalid := []string{
		`{"type":"invalid_grant","detail":"Assertion is invalid"}`,
		`{"error":"invalid_grant","error_description":"invalid audience"}`,
		`{"type":"invalid_grant","detail":"signature verification failed"}`,
	}
	for _, body := range invalid {
		resp := &http.Response{StatusCode: http.StatusBadRequest, Body: io.NopCloser(strings.NewReader(body))}
		err := classifyTokenError(resp)
		var ai *AssertionInvalidError
		if !errors.As(err, &ai) {
			t.Errorf("body %q classified as %T, want *AssertionInvalidError", body, err)
		}
		var pending *PendingError
		if errors.As(err, &pending) {
			t.Errorf("body %q must not be pending", body)
		}
	}

	// A pending-approval detail stays pending.
	resp := &http.Response{
		StatusCode: http.StatusBadRequest,
		Body:       io.NopCloser(strings.NewReader(`{"type":"invalid_grant","detail":"agent pending approval"}`)),
	}
	var ai *AssertionInvalidError
	if errors.As(classifyTokenError(resp), &ai) {
		t.Errorf("pending detail must not be AssertionInvalidError")
	}
}

// TestClassifyTokenError_ShippedPendingDetail pins the exact detail the shipped
// backend emits for a signature-verified pending agent
// ("Agent is not active yet (pending approval)" — auth/services/assertion_service.py).
// It MUST classify as *PendingError: this is the signal that lets the
// register/setup/wizard approval wait poll on self-hosted backends, which mint
// no claim tokens. If a phrase added to isAssertionInvalidDetail ever matches
// it, the approval flow dies on first contact again.
func TestClassifyTokenError_ShippedPendingDetail(t *testing.T) {
	resp := &http.Response{
		StatusCode: http.StatusBadRequest,
		Body: io.NopCloser(strings.NewReader(
			`{"type":"invalid_grant","status":400,"detail":"Agent is not active yet (pending approval)","instance":"/oauth/token"}`)),
	}
	err := classifyTokenError(resp)
	var pending *PendingError
	if !errors.As(err, &pending) {
		t.Fatalf("shipped pending detail classified as %v, want *PendingError", err)
	}
	if ClassifyTokenExchange(err, ClaimContext{}) != OutcomePending {
		t.Errorf("shipped pending detail must classify as OutcomePending without a claim outstanding")
	}
}

// TestClassifyTokenExchange pins the shared claim-vs-audience disambiguation
// (AR2-9): a pending error is always pending; an assertion-invalid error is a
// hard failure UNLESS a claim is still outstanding, in which case the identical
// backend string means "not claimed/approved yet" and must be treated as pending
// so the register wait loop keeps polling instead of aborting.
func TestClassifyTokenExchange(t *testing.T) {
	cases := []struct {
		name string
		err  error
		cc   ClaimContext
		want TokenExchangeOutcome
	}{
		{"nil", nil, ClaimContext{}, OutcomeOther},
		{"pending", &PendingError{Detail: "pending"}, ClaimContext{}, OutcomePending},
		{"pending with claim", &PendingError{}, ClaimContext{ClaimOutstanding: true}, OutcomePending},
		{"assertion invalid, no claim", &AssertionInvalidError{Detail: "Assertion is invalid"}, ClaimContext{}, OutcomeAssertionInvalid},
		{"assertion invalid, claim outstanding", &AssertionInvalidError{Detail: "Assertion is invalid"}, ClaimContext{ClaimOutstanding: true}, OutcomePending},
		{"other", errors.New("network down"), ClaimContext{}, OutcomeOther},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			if got := ClassifyTokenExchange(c.err, c.cc); got != c.want {
				t.Errorf("ClassifyTokenExchange = %v, want %v", got, c.want)
			}
		})
	}
}
