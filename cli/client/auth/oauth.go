package auth

import (
	"bytes"
	"crypto/ed25519"
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"strings"
	"time"

	"github.com/go-jose/go-jose/v4"
	"github.com/go-jose/go-jose/v4/jwt"
)

// assertionTTL is the lifetime of a signed RFC 7523 assertion. The backend
// enforces a STRICT cap (assertion_max_ttl_seconds, default 300): it rejects
// exp > now + max_ttl, so an assertion minted for exactly 300s fails whenever the
// client clock runs even slightly ahead. 270s keeps a 30s margin under the cap
// while still tolerating real-world skew (the shipped CLI used 120s; impl/4.2 §4).
const assertionTTL = 270 * time.Second

// httpClient is the exchange's dedicated client: explicit timeout, never the
// zero-timeout http.DefaultClient (rules/05).
var httpClient = &http.Client{Timeout: 30 * time.Second}

// PendingError indicates the agent is registered but not yet approved: the token
// endpoint returns 400 invalid_grant while approval is pending. Callers can
// distinguish "retry later" (exit 3 with --wait) from a hard denial.
type PendingError struct {
	Detail string
}

func (e *PendingError) Error() string {
	if e.Detail == "" {
		return "agent not active yet (pending approval)"
	}
	return e.Detail
}

// AssertionInvalidError is a 400 invalid_grant whose detail signals that the
// signed assertion itself was rejected (bad audience/signature/expiry) rather
// than a pending approval. It exists so the register wait loop can STOP and
// surface an actionable fix (usually an audience mismatch — the CLI signed an
// aud the backend's canonical_base_url does not match) instead of polling
// forever as if the agent were merely unapproved (QA-9).
type AssertionInvalidError struct {
	Detail string
}

func (e *AssertionInvalidError) Error() string {
	if e.Detail == "" {
		return "token exchange rejected the assertion (likely an audience mismatch)"
	}
	return e.Detail
}

// ClaimContext carries the caller-side facts the token-exchange classifier needs
// beyond the error itself. ClaimOutstanding is true while a claim token has been
// issued but the human has not yet claimed+approved the agent in the console: in
// that window the backend rejects the exchange with the SAME ambiguous 400
// invalid_grant "Assertion is invalid" string it uses for a real audience
// mismatch (the approval-status gate runs before signature/audience). Callers
// that know a claim is outstanding pass true so the classifier treats that as
// pending rather than a hard failure.
type ClaimContext struct {
	ClaimOutstanding bool
}

// TokenExchangeOutcome is the classified meaning of a token-exchange failure,
// with the claim-vs-audience ambiguity already resolved.
type TokenExchangeOutcome int

const (
	// OutcomeOther is any failure that is neither pending nor a rejected
	// assertion (network, unexpected status, decode error, …).
	OutcomeOther TokenExchangeOutcome = iota
	// OutcomePending means the identity is registered but not active yet —
	// keep waiting / exit 3 with --wait.
	OutcomePending
	// OutcomeAssertionInvalid means the signed assertion was rejected (usually
	// an audience mismatch) — polling will never clear it, so stop with an
	// actionable fix.
	OutcomeAssertionInvalid
)

// ClassifyTokenExchange resolves a token-exchange error into one of the three
// caller-facing outcomes, folding in the claim-outstanding disambiguation so
// the register wait loop and the data-plane session path share ONE rule (QA-9/
// QA-24). A *PendingError is pending. A *AssertionInvalidError is a hard
// assertion failure UNLESS a claim is still outstanding, in which case the
// identical backend string means "not claimed/approved yet" and we treat it as
// pending. Anything else is OutcomeOther.
func ClassifyTokenExchange(err error, cc ClaimContext) TokenExchangeOutcome {
	if err == nil {
		return OutcomeOther
	}
	var pending *PendingError
	if errors.As(err, &pending) {
		return OutcomePending
	}
	var ai *AssertionInvalidError
	if errors.As(err, &ai) {
		if cc.ClaimOutstanding {
			return OutcomePending
		}
		return OutcomeAssertionInvalid
	}
	return OutcomeOther
}

// ErrNotRegistered indicates the identity has no client-id registration in the
// active environment — the exchange cannot even be attempted. Exposed as a
// sentinel so CLI layers can map it to the NOT_AUTHENTICATED error code rather
// than a generic internal error.
var ErrNotRegistered = errors.New("identity is not registered in this environment")

// requireSecureHost enforces the transport invariant shared by tokenEndpoint (F1)
// and the request editor (F3): https everywhere, except plain http for loopback.
// A single definition keeps the two call sites from drifting.
func requireSecureHost(u *url.URL) error {
	if u.Scheme == "https" {
		return nil
	}
	if u.Scheme == "http" && isLoopbackHost(u.Hostname()) {
		return nil
	}
	return fmt.Errorf("refusing to use insecure endpoint %q: https is required (http allowed only for localhost)", u.Redacted())
}

// RequireSecureURL is the exported transport guard: it parses rawurl and applies
// the same https-or-loopback invariant the SDK enforces before attaching a
// bearer (requireSecureHost). It exists so callers that hold the agent token
// outside the generated SDK transport — the broker POST in `jentic execute` —
// obey the same rule: the token must never traverse plaintext http to a
// non-loopback host (SEC-1). A malformed URL is rejected fail-closed.
func RequireSecureURL(rawurl string) error {
	u, err := url.Parse(rawurl)
	if err != nil {
		return fmt.Errorf("invalid URL %q: %w", rawurl, err)
	}
	return requireSecureHost(u)
}

// isLoopbackHost reports whether host is a loopback address or "localhost".
func isLoopbackHost(host string) bool {
	if host == "localhost" {
		return true
	}
	if ip := net.ParseIP(host); ip != nil {
		return ip.IsLoopback()
	}
	return false
}

// tokenEndpoint derives the OAuth token URL from the Control Plane base URL,
// refusing any non-TLS, non-loopback target BEFORE we sign an assertion for it
// (ref F1): an attacker-influenced base URL must not be able to harvest a
// freshly-signed assertion (assertion relay). The backend additionally validates
// the assertion `aud` against its own canonical token URL, so even a relayed
// assertion cannot be replayed against the real server.
func tokenEndpoint(baseURL string) (string, error) {
	u, err := url.Parse(strings.TrimRight(baseURL, "/"))
	if err != nil {
		return "", fmt.Errorf("invalid base URL %q: %w", baseURL, err)
	}
	if err := requireSecureHost(u); err != nil {
		return "", err
	}
	u.Path += "/oauth/token"
	return u.String(), nil
}

// performOAuthExchange proves possession of the env-scoped Ed25519 key by signing
// a short-lived RFC 7523 assertion and trading it for an access token. No static
// client secret ever touches disk.
func performOAuthExchange(creds Credentials) (*TokenSet, error) {
	ref := creds.IdentityRef()
	privKey, err := GetOrGenerateKey(ref)
	if err != nil {
		return nil, err
	}
	clientID, err := clientIDFor(ref)
	if err != nil {
		return nil, fmt.Errorf("%w; run 'jentic identity register' first: %w", ErrNotRegistered, err)
	}

	// tokenEndpoint enforces the TLS/loopback invariant (F1) before we ever sign.
	endpoint, err := tokenEndpoint(creds.BaseURL)
	if err != nil {
		return nil, err
	}
	assertion, err := signJWTAssertion(privKey, clientID, endpoint)
	if err != nil {
		return nil, err
	}

	// The Jentic token endpoint takes a JSON body (not RFC 6749 form-encoding);
	// the shipped V1 client already speaks JSON and the port preserves that.
	reqBody, err := json.Marshal(map[string]string{
		"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
		"assertion":  assertion,
	})
	if err != nil {
		return nil, fmt.Errorf("encoding token request: %w", err)
	}
	resp, err := httpClient.Post(endpoint, "application/json", bytes.NewReader(reqBody))
	if err != nil {
		return nil, fmt.Errorf("token exchange request failed: %w", err)
	}
	defer func() { _ = resp.Body.Close() }()

	if resp.StatusCode != http.StatusOK {
		// Pending approval surfaces as 400 invalid_grant (the backend maps
		// InvalidGrantError -> 400). classifyTokenError returns *PendingError for
		// that case and a wrapped error otherwise.
		return nil, classifyTokenError(resp)
	}

	var body struct {
		AccessToken string `json:"access_token"`
		ExpiresIn   int    `json:"expires_in"`
		// refresh_token intentionally ignored — see the TokenSet note.
	}
	if err := json.NewDecoder(resp.Body).Decode(&body); err != nil {
		return nil, fmt.Errorf("decoding token response: %w", err)
	}
	return &TokenSet{
		AccessToken: body.AccessToken,
		ExpiresAt:   renewalDeadline(time.Now(), body.ExpiresIn),
	}, nil
}

// classifyTokenError decodes the error body of a non-200 token response.
// A 400 invalid_grant means the identity is unapproved -> *PendingError; anything
// else is wrapped with the status and error code. The shipped backend emits
// RFC 7807 problem-details ({"type": "invalid_grant", "detail": ...} — see
// auth/web/errors.py), which is what the V1 client parsed; the RFC 6749 OAuth
// shape ({"error": ..., "error_description": ...}) is also accepted so an
// RFC-compliant server classifies identically.
func classifyTokenError(resp *http.Response) error {
	data, _ := io.ReadAll(io.LimitReader(resp.Body, 64<<10))
	var body struct {
		Type        string `json:"type"`  // RFC 7807 problem details (the shipped backend)
		Error       string `json:"error"` // RFC 6749 OAuth error shape
		Description string `json:"error_description"`
		Detail      string `json:"detail"`
	}
	_ = json.Unmarshal(data, &body)

	code := body.Type
	if code == "" {
		code = body.Error
	}
	detail := body.Detail
	if detail == "" {
		detail = body.Description
	}
	if resp.StatusCode == http.StatusBadRequest && code == "invalid_grant" {
		// An assertion-validation failure (audience/signature/expiry) also comes
		// back as 400 invalid_grant, but it is NOT a pending approval — polling
		// will never clear it. Distinguish on the backend's detail text so the
		// register wait loop stops with an actionable audience-mismatch hint
		// instead of hanging (QA-9). The shipped backend emits a distinct
		// "Agent is not active yet (pending approval)" for a signature-verified
		// pending agent (auth/services/assertion_service.py), which contains
		// none of the assertion-failure phrases and so lands on PendingError.
		if isAssertionInvalidDetail(detail) {
			return &AssertionInvalidError{Detail: detail}
		}
		return &PendingError{Detail: detail}
	}
	if code != "" {
		return fmt.Errorf("token exchange failed (status %d, %s): %s", resp.StatusCode, code, detail)
	}
	return fmt.Errorf("token exchange failed (status %d)", resp.StatusCode)
}

// isAssertionInvalidDetail reports whether a 400 invalid_grant detail describes a
// rejected assertion (audience/signature/expiry) rather than a pending approval.
// The shipped backend returns "Assertion is invalid" for these; we also match
// the common audience/signature phrasings defensively. Kept deliberately narrow
// so a genuinely-pending agent is never mistaken for a hard failure.
func isAssertionInvalidDetail(detail string) bool {
	d := strings.ToLower(detail)
	if d == "" {
		return false
	}
	for _, phrase := range []string{"assertion is invalid", "invalid assertion", "audience", "aud claim", "signature", "expired"} {
		if strings.Contains(d, phrase) {
			return true
		}
	}
	return false
}

// renewalDeadline computes when a token should be treated as expired, renewing
// slightly early so we never use a token that lapses mid-request.
//
// SECURITY/ROBUSTNESS (ref F4): a flat skew subtraction is unsafe. If the backend
// issues a short-lived token (expires_in <= skew, or a missing/zero value), a flat
// subtraction yields a deadline at-or-before now, so EVERY request sees an
// "expired" token and fires a fresh exchange — a self-inflicted exchange storm.
// So we treat missing/zero/negative expires_in as a short safe default and clamp
// the early-renewal skew to min(30s, lifetime/2).
func renewalDeadline(now time.Time, expiresIn int) time.Time {
	const defaultLifetime = 60 * time.Second
	lifetime := time.Duration(expiresIn) * time.Second
	if lifetime <= 0 {
		lifetime = defaultLifetime
	}
	skew := 30 * time.Second
	if half := lifetime / 2; half < skew {
		skew = half
	}
	return now.Add(lifetime - skew)
}

// signJWTAssertion builds a compact JWS (EdDSA / Ed25519) per RFC 7523.
//
// SECURITY (ref F2): the most security-critical signing path in the CLI, so we do
// NOT hand-roll the JWS — we use go-jose and propagate every error. The `aud`
// claim is the canonical token endpoint; the backend MUST validate it so a relayed
// assertion (F1) cannot be replayed.
func signJWTAssertion(priv ed25519.PrivateKey, clientID, audience string) (string, error) {
	signer, err := jose.NewSigner(
		jose.SigningKey{Algorithm: jose.EdDSA, Key: priv},
		(&jose.SignerOptions{}).WithType("JWT"),
	)
	if err != nil {
		return "", fmt.Errorf("building JWT signer: %w", err)
	}
	now := time.Now()
	claims := jwt.Claims{
		Issuer:   clientID,
		Subject:  clientID,
		Audience: jwt.Audience{audience},
		IssuedAt: jwt.NewNumericDate(now),
		Expiry:   jwt.NewNumericDate(now.Add(assertionTTL)),
		ID:       randomJTI(), // replay protection; the backend's jti cache already ships
	}
	signed, err := jwt.Signed(signer).Claims(claims).Serialize()
	if err != nil {
		return "", fmt.Errorf("signing JWT assertion: %w", err)
	}
	return signed, nil
}

// randomJTI returns a fresh 128-bit random jti. A fresh jti per assertion is
// mandatory: the backend's jti cache turns a reused value into a lockout.
func randomJTI() string {
	buf := make([]byte, 16)
	if _, err := rand.Read(buf); err != nil {
		// crypto/rand failure is catastrophic and essentially never happens; a
		// panic is preferable to emitting a predictable (replayable) jti.
		panic(fmt.Sprintf("crypto/rand failed: %v", err))
	}
	return base64.RawURLEncoding.EncodeToString(buf)
}
