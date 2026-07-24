package cmd

import (
	"bytes"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/jentic/jentic-one/cli/internal/install"
)

func TestReuseInstallSecretsFromLiveConfig(t *testing.T) {
	// The reinstall repro guard at the cmd layer: reuseInstallSecrets must
	// pre-seed the draft from an existing jentic-one.yaml so a subsequent
	// FillSecrets (fill-only-empty) leaves the encryption key alone.
	dir := t.TempDir()
	out := filepath.Join(dir, "jentic-one.yaml")

	src := install.NewDraft()
	if err := src.FillSecrets(); err != nil {
		t.Fatalf("FillSecrets: %v", err)
	}
	data, err := src.Render()
	if err != nil {
		t.Fatalf("Render: %v", err)
	}
	if err := os.WriteFile(out, data, 0o600); err != nil {
		t.Fatalf("write: %v", err)
	}

	buf := &bytes.Buffer{}
	app := &App{Out: buf, Err: &bytes.Buffer{}}
	draft := install.NewDraft()

	reuseInstallSecrets(app, draft, out)

	if draft.EncryptionKeyset == nil {
		t.Fatalf("expected encryption keyset to be reused")
	}
	if draft.AdminJWTSecret != src.AdminJWTSecret {
		t.Errorf("AdminJWTSecret not reused")
	}
	if !strings.Contains(buf.String(), "Reusing secrets") {
		t.Errorf("expected operator notice, got: %q", buf.String())
	}
}

func TestReuseInstallSecretsFallsBackToBackup(t *testing.T) {
	// After a `jenticctl uninstall` (which renames jentic-one.yaml to
	// jentic-one-old.yaml before wiping), the backup path is what makes the
	// preserved data volume readable on the next install. Verify the
	// fallback resolves it.
	dir := t.TempDir()
	out := filepath.Join(dir, "jentic-one.yaml")
	backup := filepath.Join(dir, "jentic-one-old.yaml")

	src := install.NewDraft()
	if err := src.FillSecrets(); err != nil {
		t.Fatalf("FillSecrets: %v", err)
	}
	data, err := src.Render()
	if err != nil {
		t.Fatalf("Render: %v", err)
	}
	if err := os.WriteFile(backup, data, 0o600); err != nil {
		t.Fatalf("write backup: %v", err)
	}
	// out itself does not exist — mirroring the state after uninstall.

	app := &App{Out: &bytes.Buffer{}, Err: &bytes.Buffer{}}
	draft := install.NewDraft()

	reuseInstallSecrets(app, draft, out)

	if draft.EncryptionKeyset == nil {
		t.Fatalf("expected encryption keyset to be reused from backup")
	}
	if draft.AdminJWTSecret != src.AdminJWTSecret {
		t.Errorf("AdminJWTSecret not reused from backup")
	}
}

func TestReuseInstallSecretsFreshBoxIsNoOp(t *testing.T) {
	// Fresh install (no config, no backup): reuse is silent and leaves the
	// draft alone so FillSecrets generates everything from scratch.
	dir := t.TempDir()
	out := filepath.Join(dir, "jentic-one.yaml")

	buf := &bytes.Buffer{}
	app := &App{Out: buf, Err: &bytes.Buffer{}}
	draft := install.NewDraft()

	reuseInstallSecrets(app, draft, out)

	if draft.EncryptionKeyset != nil || draft.AdminJWTSecret != "" {
		t.Errorf("draft mutated on a fresh box")
	}
	if strings.Contains(buf.String(), "Reusing secrets") {
		t.Errorf("did not expect reuse notice on a fresh box, got: %q", buf.String())
	}
}

func TestReuseInstallSecretsMalformedFileWarnsAndFallsThrough(t *testing.T) {
	// A half-written prior config must not block reinstall: the caller
	// warns and continues to fresh secrets. Whether the backup at the
	// candidate list's second slot succeeds is orthogonal.
	dir := t.TempDir()
	out := filepath.Join(dir, "jentic-one.yaml")
	if err := os.WriteFile(out, []byte(":\n\tnot yaml\n"), 0o600); err != nil {
		t.Fatalf("write: %v", err)
	}

	buf := &bytes.Buffer{}
	app := &App{Out: buf, Err: &bytes.Buffer{}}
	draft := install.NewDraft()

	reuseInstallSecrets(app, draft, out)

	if draft.EncryptionKeyset != nil {
		t.Errorf("draft mutated on malformed input")
	}
	if !strings.Contains(buf.String(), "could not read prior config") {
		t.Errorf("expected warning, got: %q", buf.String())
	}
}
