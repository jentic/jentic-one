package install

import (
	"os"
	"strings"
	"testing"

	"gopkg.in/yaml.v3"
)

// withKeychainStubs points the GOOS seam at darwin and captures security(1)
// invocations instead of touching a real keychain.
func withKeychainStubs(t *testing.T) *[][]string {
	t.Helper()
	var calls [][]string
	prevGoos, prevRun := goos, runSecurity
	goos = "darwin"
	runSecurity = func(args ...string) error {
		calls = append(calls, args)
		return nil
	}
	t.Cleanup(func() { goos, runSecurity = prevGoos, prevRun })
	return &calls
}

func TestApplyKeychainFreshInstall(t *testing.T) {
	calls := withKeychainStubs(t)

	d := NewDraft()
	d.RuntimePath = RuntimeSource
	d.EncryptionKey = "b64-material"

	if err := d.ApplyKeychain(); err != nil {
		t.Fatalf("ApplyKeychain: %v", err)
	}
	if d.EncryptionKey != "" {
		t.Errorf("EncryptionKey not cleared from the draft: %q", d.EncryptionKey)
	}
	if want := "jentic-one-credentials-encryption-v1"; d.EncryptionKeychain != want {
		t.Errorf("EncryptionKeychain = %q, want %q", d.EncryptionKeychain, want)
	}
	if len(*calls) != 1 {
		t.Fatalf("security called %d times, want 1", len(*calls))
	}
	got := strings.Join((*calls)[0], " ")
	want := "add-generic-password -U -s jentic-one-credentials-encryption-v1 -a jentic-one -w b64-material"
	if got != want {
		t.Errorf("security args = %q, want %q", got, want)
	}
}

func TestApplyKeychainRefusesDocker(t *testing.T) {
	withKeychainStubs(t)

	d := NewDraft() // NewDraft defaults to the Docker runtime path
	if err := d.ApplyKeychain(); err == nil || !strings.Contains(err.Error(), "Docker") {
		t.Errorf("expected Docker refusal, got %v", err)
	}
}

func TestApplyKeychainRefusesNonDarwin(t *testing.T) {
	calls := withKeychainStubs(t)
	goos = "linux"

	d := NewDraft()
	d.RuntimePath = RuntimeSource
	d.EncryptionKey = "b64-material"
	if err := d.ApplyKeychain(); err == nil || !strings.Contains(err.Error(), "macOS") {
		t.Errorf("expected macOS refusal, got %v", err)
	}
	if len(*calls) != 0 {
		t.Errorf("security must not be called on non-darwin, got %d calls", len(*calls))
	}
}

func TestApplyKeychainMigratesReusedKeyset(t *testing.T) {
	calls := withKeychainStubs(t)

	d := NewDraft()
	d.RuntimePath = RuntimeSource
	d.EncryptionKeyset = &encryptionOut{
		ActiveID: "v2",
		Entries: []encryptionEntryOut{
			{ID: "v1", MaterialKeychain: "jentic-one-credentials-encryption-v1"}, // already migrated
			{ID: "v2", Material: "inline-v2"},
		},
	}

	if err := d.ApplyKeychain(); err != nil {
		t.Fatalf("ApplyKeychain: %v", err)
	}
	if len(*calls) != 1 {
		t.Fatalf("security called %d times, want 1 (only the inline entry)", len(*calls))
	}
	e := d.EncryptionKeyset.Entries[1]
	if e.Material != "" || e.MaterialKeychain != "jentic-one-credentials-encryption-v2" {
		t.Errorf("v2 entry not migrated: %+v", e)
	}
}

func TestRenderEmitsKeychainReference(t *testing.T) {
	d := NewDraft()
	d.RuntimePath = RuntimeSource
	d.EncryptionKeychain = "jentic-one-credentials-encryption-v1"

	data, err := d.Render()
	if err != nil {
		t.Fatalf("Render: %v", err)
	}
	var cfg struct {
		Credentials struct {
			Encryption encryptionOut `yaml:"encryption"`
		} `yaml:"credentials"`
	}
	if err := yaml.Unmarshal(data, &cfg); err != nil {
		t.Fatalf("unmarshal rendered config: %v", err)
	}
	entries := cfg.Credentials.Encryption.Entries
	if len(entries) != 1 {
		t.Fatalf("keyset entries = %d, want 1", len(entries))
	}
	if entries[0].MaterialKeychain != d.EncryptionKeychain {
		t.Errorf("material_keychain = %q, want %q", entries[0].MaterialKeychain, d.EncryptionKeychain)
	}
	if entries[0].Material != "" {
		t.Errorf("inline material leaked into the rendered config: %q", entries[0].Material)
	}
}

func TestReuseSecretsCarriesKeychainKeyset(t *testing.T) {
	dir := t.TempDir()
	path := dir + "/jentic-one.yaml"
	prior := `
credentials:
  encryption:
    active_id: v1
    entries:
      - id: v1
        material_keychain: jentic-one-credentials-encryption-v1
`
	if err := os.WriteFile(path, []byte(prior), 0o600); err != nil {
		t.Fatalf("write prior config: %v", err)
	}

	d := NewDraft()
	reused, err := ReuseSecrets(d, path)
	if err != nil {
		t.Fatalf("ReuseSecrets: %v", err)
	}
	if !reused {
		t.Fatal("keychain-reference keyset was not treated as reusable")
	}
	if d.EncryptionKeyset == nil ||
		d.EncryptionKeyset.Entries[0].MaterialKeychain != "jentic-one-credentials-encryption-v1" {
		t.Errorf("keyset not carried over verbatim: %+v", d.EncryptionKeyset)
	}
}
