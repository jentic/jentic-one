package profile

import (
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/jentic/jentic-one/cli/internal/config"
)

func TestMetaAndTokensRoundTrip(t *testing.T) {
	paths := config.Paths{Root: t.TempDir()}

	p, err := Open(paths, "acme")
	if err != nil {
		t.Fatalf("Open: %v", err)
	}

	meta := &Meta{
		BaseURL:                 "http://127.0.0.1:8000",
		AgentID:                 "agnt_123",
		AgentName:               "acme-agent",
		KID:                     "kid-1",
		RegistrationAccessToken: "rat_abc",
	}
	if err := p.SaveMeta(meta); err != nil {
		t.Fatalf("SaveMeta: %v", err)
	}
	gotMeta, err := p.LoadMeta()
	if err != nil {
		t.Fatalf("LoadMeta: %v", err)
	}
	if *gotMeta != *meta {
		t.Fatalf("meta mismatch: got %+v want %+v", gotMeta, meta)
	}

	tokens := &Tokens{
		AccessToken:     "at_xyz",
		RefreshToken:    "rt_xyz",
		AccessExpiresAt: time.Now().Add(time.Hour).Truncate(time.Second),
	}
	if err := p.SaveTokens(tokens); err != nil {
		t.Fatalf("SaveTokens: %v", err)
	}
	gotTokens, err := p.LoadTokens()
	if err != nil {
		t.Fatalf("LoadTokens: %v", err)
	}
	if gotTokens.AccessToken != tokens.AccessToken || gotTokens.RefreshToken != tokens.RefreshToken {
		t.Fatalf("tokens mismatch: got %+v want %+v", gotTokens, tokens)
	}
}

func TestSecretFilePerms(t *testing.T) {
	paths := config.Paths{Root: t.TempDir()}
	p, err := Open(paths, "perm")
	if err != nil {
		t.Fatalf("Open: %v", err)
	}
	if err := p.SaveMeta(&Meta{AgentID: "agnt_1"}); err != nil {
		t.Fatalf("SaveMeta: %v", err)
	}
	if err := p.SaveTokens(&Tokens{AccessToken: "at_1"}); err != nil {
		t.Fatalf("SaveTokens: %v", err)
	}

	for _, name := range []string{profileFileName, tokensFileName} {
		info, err := os.Stat(filepath.Join(p.Dir(), name))
		if err != nil {
			t.Fatalf("stat %s: %v", name, err)
		}
		if perm := info.Mode().Perm(); perm != 0o600 {
			t.Fatalf("%s perms = %o, want 600", name, perm)
		}
	}
}

func TestAPIKeyRoundTripAndPerms(t *testing.T) {
	paths := config.Paths{Root: t.TempDir()}
	p, err := Open(paths, "keyed")
	if err != nil {
		t.Fatalf("Open: %v", err)
	}

	meta := &Meta{BaseURL: "http://127.0.0.1:8000", AuthMode: AuthModeAPIKey}
	if err := p.SaveMeta(meta); err != nil {
		t.Fatalf("SaveMeta: %v", err)
	}
	gotMeta, err := p.LoadMeta()
	if err != nil {
		t.Fatalf("LoadMeta: %v", err)
	}
	if !gotMeta.IsAPIKey() {
		t.Fatalf("AuthMode not persisted: %+v", gotMeta)
	}

	if err := p.SaveAPIKey("jak_secretvalue"); err != nil {
		t.Fatalf("SaveAPIKey: %v", err)
	}
	got, err := p.LoadAPIKey()
	if err != nil {
		t.Fatalf("LoadAPIKey: %v", err)
	}
	if got != "jak_secretvalue" {
		t.Fatalf("LoadAPIKey = %q, want jak_secretvalue", got)
	}

	info, err := os.Stat(filepath.Join(p.Dir(), apiKeyFileName))
	if err != nil {
		t.Fatalf("stat apikey: %v", err)
	}
	if perm := info.Mode().Perm(); perm != 0o600 {
		t.Fatalf("apikey perms = %o, want 600", perm)
	}
}

func TestDelete(t *testing.T) {
	paths := config.Paths{Root: t.TempDir()}
	p, err := Open(paths, "gone")
	if err != nil {
		t.Fatalf("Open: %v", err)
	}
	if err := p.SaveMeta(&Meta{AgentID: "agnt_1"}); err != nil {
		t.Fatalf("SaveMeta: %v", err)
	}
	if err := p.SaveTokens(&Tokens{AccessToken: "at_1"}); err != nil {
		t.Fatalf("SaveTokens: %v", err)
	}

	if err := p.Delete(); err != nil {
		t.Fatalf("Delete: %v", err)
	}
	if _, err := os.Stat(p.Dir()); !os.IsNotExist(err) {
		t.Fatalf("profile dir still present after Delete: %v", err)
	}
	// It no longer appears in List.
	names, err := List(paths)
	if err != nil {
		t.Fatalf("List: %v", err)
	}
	for _, n := range names {
		if n == "gone" {
			t.Fatalf("deleted profile still listed: %v", names)
		}
	}
	// Deleting again (already gone) is not an error.
	if err := p.Delete(); err != nil {
		t.Fatalf("Delete (idempotent): %v", err)
	}
}

func TestLoadAPIKeyAbsent(t *testing.T) {
	paths := config.Paths{Root: t.TempDir()}
	p, err := Open(paths, "nokey")
	if err != nil {
		t.Fatalf("Open: %v", err)
	}
	got, err := p.LoadAPIKey()
	if err != nil {
		t.Fatalf("LoadAPIKey: %v", err)
	}
	if got != "" {
		t.Fatalf("LoadAPIKey = %q, want empty", got)
	}
}

func TestTokensExpired(t *testing.T) {
	cases := []struct {
		name string
		tok  *Tokens
		want bool
	}{
		{"nil", nil, true},
		{"empty", &Tokens{}, true},
		{"no expiry", &Tokens{AccessToken: "at_x"}, false},
		{"future", &Tokens{AccessToken: "at_x", AccessExpiresAt: time.Now().Add(time.Hour)}, false},
		{"past", &Tokens{AccessToken: "at_x", AccessExpiresAt: time.Now().Add(-time.Hour)}, true},
		{"within skew", &Tokens{AccessToken: "at_x", AccessExpiresAt: time.Now().Add(30 * time.Second)}, true},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := tc.tok.Expired(60 * time.Second); got != tc.want {
				t.Fatalf("Expired = %v, want %v", got, tc.want)
			}
		})
	}
}

func TestList(t *testing.T) {
	paths := config.Paths{Root: t.TempDir()}
	for _, name := range []string{"a", "b"} {
		if _, err := Open(paths, name); err != nil {
			t.Fatalf("Open %s: %v", name, err)
		}
	}
	names, err := List(paths)
	if err != nil {
		t.Fatalf("List: %v", err)
	}
	if len(names) != 2 {
		t.Fatalf("List = %v, want 2 entries", names)
	}
}
