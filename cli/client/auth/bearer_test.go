package auth

import (
	"errors"
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
