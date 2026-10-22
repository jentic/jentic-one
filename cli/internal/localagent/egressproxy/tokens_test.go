package egressproxy

import (
	"bytes"
	"encoding/base64"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestTokenFileRoundTripAndReload(t *testing.T) {
	path := filepath.Join(t.TempDir(), "tokens.json")
	store := NewTokenStore(path)
	if store.Session("github") != "" {
		t.Fatal("no file means no session token")
	}
	if err := WriteTokenFile(path, strings.NewReader(`{"tokens":{"github":"`+sessionGitHubToken+`"}}`)); err != nil {
		t.Fatal(err)
	}
	info, _ := os.Stat(path)
	if info.Mode().Perm() != 0o600 {
		t.Errorf("token file mode = %v, want 0600", info.Mode().Perm())
	}
	if got := store.Session("github"); got != sessionGitHubToken {
		t.Errorf("Session(github) = %q", got)
	}
	// A rotated token is picked up; a bad write keeps the last good tokens.
	time.Sleep(10 * time.Millisecond)
	if err := WriteTokenFile(path, strings.NewReader(`{"tokens":{"github":"`+commandToken+`"}}`)); err != nil {
		t.Fatal(err)
	}
	if got := store.Session("github"); got != commandToken {
		t.Errorf("rotated Session(github) = %q", got)
	}
	_ = os.WriteFile(path, []byte("{garbage"), 0o600)
	if got := store.Session("github"); got != commandToken {
		t.Errorf("a corrupt file must keep the last good token, got %q", got)
	}
}

func TestWriteTokenFileValidates(t *testing.T) {
	path := filepath.Join(t.TempDir(), "tokens.json")
	for _, bad := range []string{
		`{"tokens":{"github":"at_not-an-invocation-token-0123456789abcdef"}}`,
		`{"tokens":{"gitlab":"` + sessionGitHubToken + `"}}`,
		`{"tokens":{},"extra":1}`,
		`not json`,
		`{"tokens":{"github":"` + strings.Repeat("a", maxTokenFile) + `"}}`,
	} {
		if err := WriteTokenFile(path, strings.NewReader(bad)); err == nil {
			t.Errorf("WriteTokenFile(%.40q) must fail", bad)
		}
	}
	if _, err := os.Stat(path); err == nil {
		t.Error("a refused token file must not be written")
	}
}

func TestProxyCredentialToken(t *testing.T) {
	enc := func(s string) string { return "Basic " + base64.StdEncoding.EncodeToString([]byte(s)) }
	for _, tc := range []struct {
		header         string
		token          string
		present, isErr bool
	}{
		{"", "", false, false},
		{enc(commandToken + ":x"), commandToken, true, false},
		{enc(commandToken), commandToken, true, false},
		{"basic " + base64.StdEncoding.EncodeToString([]byte(commandToken+":")), commandToken, true, false},
		{enc("alice:secret"), "", true, true},
		{"Bearer " + commandToken, "", true, true},
		{"Basic !!!", "", true, true},
	} {
		token, present, err := proxyCredentialToken(tc.header)
		if token != tc.token || present != tc.present || (err != nil) != tc.isErr {
			t.Errorf("proxyCredentialToken(%q) = %q %v %v", tc.header, token, present, err)
		}
	}
}

func TestPublishAndCheckReady(t *testing.T) {
	dir := t.TempDir()
	ca := testCA(t, "github.com")
	roots := []byte("-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n")
	nonce := strings.Repeat("ab", 16)
	if err := CheckReady(dir, nonce); err == nil {
		t.Fatal("nothing published yet must not be ready")
	}
	if err := Publish(dir, ca, roots, nonce); err != nil {
		t.Fatal(err)
	}
	if err := CheckReady(dir, nonce); err != nil {
		t.Fatalf("CheckReady after Publish: %v", err)
	}
	bundle, _ := os.ReadFile(filepath.Join(dir, BundleFile))
	if !bytes.HasPrefix(bundle, roots) || !bytes.HasSuffix(bundle, ca.CertPEM()) {
		t.Error("the bundle must be the system roots followed by the session CA")
	}
	entries, _ := os.ReadDir(dir)
	for _, e := range entries {
		data, _ := os.ReadFile(filepath.Join(dir, e.Name()))
		if bytes.Contains(data, []byte("PRIVATE KEY")) {
			t.Errorf("%s holds private key material", e.Name())
		}
		if info, _ := e.Info(); info.Mode().Perm() != 0o644 {
			t.Errorf("%s mode = %v, want 0644", e.Name(), info.Mode().Perm())
		}
	}
	if err := CheckReady(dir, strings.Repeat("cd", 16)); err == nil || !strings.Contains(err.Error(), "another session") {
		t.Errorf("a stale CA (another session's nonce) must be rejected, got %v", err)
	}
	// A CA swapped in after publication is rejected.
	other := testCA(t, "github.com")
	_ = os.WriteFile(filepath.Join(dir, CAFile), other.CertPEM(), 0o644)
	if err := CheckReady(dir, nonce); err == nil {
		t.Error("a CA file that does not match the ready file must be rejected")
	}
	if err := Publish(dir, nil, roots, nonce); err != nil {
		t.Fatal(err)
	}
	if err := CheckReady(dir, nonce); err != nil {
		t.Errorf("a session with no governed vendor publishes roots only: %v", err)
	}
	if _, err := os.Stat(filepath.Join(dir, CAFile)); err == nil {
		t.Error("no governed vendor means no CA file")
	}
	if err := Publish(dir, ca, []byte("nothing"), nonce); err == nil {
		t.Error("an empty system root bundle must be refused")
	}
}
