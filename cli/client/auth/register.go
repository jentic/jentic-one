package auth

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
)

// RegistrationResult is the RFC 7591 Dynamic Client Registration response.
// registration_access_token is intentionally NOT retained: V2 re-derives all auth
// from the environment-scoped key, so a stored management token would be dead
// weight and a needless long-lived secret (same posture as the dropped refresh
// token — see tokens.go §1).
type RegistrationResult struct {
	ClientID string `json:"client_id"`
	Status   string `json:"status"`
}

// Register performs RFC 7591 Dynamic Client Registration against the control
// plane's /register endpoint. It is a hand-rolled POST (not a generated client
// call) because /register and /oauth/token are auth-server routes that are NOT
// part of the documented control-plane OpenAPI surface — the shipped V1 CLI
// (internal/authclient) speaks to them directly and this preserves that contract.
//
// SECURITY (ref F1): the base URL is attacker-influenceable, and registration
// publishes the agent's PUBLIC key (not a secret) — but it still creates
// server-side state and returns a client_id we will sign assertions as, so we
// hold it to the same TLS/loopback invariant as tokenEndpoint. requireSecureHost
// keeps the two call sites from drifting.
func Register(baseURL, clientName string, jwks JWKS) (*RegistrationResult, error) {
	u, err := url.Parse(strings.TrimRight(baseURL, "/"))
	if err != nil {
		return nil, fmt.Errorf("invalid base URL %q: %w", baseURL, err)
	}
	if err := requireSecureHost(u); err != nil {
		return nil, err
	}
	u.Path += "/register"

	// RFC 7591 field names: client_name + jwks. The shipped endpoint takes a JSON
	// body (matching the token endpoint's deviation from RFC 6749 form-encoding).
	reqBody, err := json.Marshal(map[string]any{
		"client_name": clientName,
		"jwks":        jwks,
	})
	if err != nil {
		return nil, fmt.Errorf("encoding registration request: %w", err)
	}

	resp, err := httpClient.Post(u.String(), "application/json", bytes.NewReader(reqBody))
	if err != nil {
		return nil, fmt.Errorf("registration request failed: %w", err)
	}
	defer func() { _ = resp.Body.Close() }()

	if resp.StatusCode != http.StatusCreated {
		data, _ := io.ReadAll(io.LimitReader(resp.Body, 64<<10))
		var problem struct {
			Detail string `json:"detail"`
			Title  string `json:"title"`
		}
		_ = json.Unmarshal(data, &problem)
		detail := problem.Detail
		if detail == "" {
			detail = problem.Title
		}
		if detail != "" {
			return nil, fmt.Errorf("registration failed (status %d): %s", resp.StatusCode, detail)
		}
		return nil, fmt.Errorf("registration failed (status %d)", resp.StatusCode)
	}

	var out RegistrationResult
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
		return nil, fmt.Errorf("decoding registration response: %w", err)
	}
	return &out, nil
}

// RevokeToken revokes a token (RFC 7009) at the base URL's /oauth/revoke
// endpoint, authenticated by accessToken. Revocation is best-effort by RFC
// (the server treats unknown tokens as success), but transport and non-2xx
// failures are returned so callers can warn. Same TLS/loopback invariant as
// the other auth-server routes (F1).
func RevokeToken(baseURL, accessToken, token string) error {
	u, err := url.Parse(strings.TrimRight(baseURL, "/"))
	if err != nil {
		return fmt.Errorf("invalid base URL %q: %w", baseURL, err)
	}
	if err := requireSecureHost(u); err != nil {
		return err
	}
	u.Path += "/oauth/revoke"

	reqBody, err := json.Marshal(map[string]string{"token": token})
	if err != nil {
		return fmt.Errorf("encoding revoke request: %w", err)
	}
	req, err := http.NewRequest(http.MethodPost, u.String(), bytes.NewReader(reqBody))
	if err != nil {
		return fmt.Errorf("building revoke request: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+accessToken)

	resp, err := httpClient.Do(req)
	if err != nil {
		return fmt.Errorf("revoke request failed: %w", err)
	}
	defer func() { _ = resp.Body.Close() }()
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return fmt.Errorf("revoke failed (status %d)", resp.StatusCode)
	}
	return nil
}
