package ctlcmd

import (
	"bytes"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/jentic/jentic-one/cli/internal/cli/cmdcore"
	"github.com/jentic/jentic-one/cli/internal/config"
	"github.com/jentic/jentic-one/cli/internal/install"
)

// TestResolveStackBuildRef pins the ref-resolution chain for --build-local
// managed-clone builds: explicit --ref (pinned) > the ref this CLI was
// installed from (manifest) > defaultRef(version). Falling through to the
// remote's default branch is exactly the bug this exists to prevent — a stack
// image from a different generation than the CLI's compose/migrations.
func TestResolveStackBuildRef(t *testing.T) {
	t.Run("explicit ref wins and is pinned", func(t *testing.T) {
		a := testApp(t)
		if err := (&config.Manifest{Ref: "cli-v2-release"}).Save(a.Paths); err != nil {
			t.Fatal(err)
		}
		ref, pinned := a.resolveStackBuildRef("my-branch")
		if ref != "my-branch" || !pinned {
			t.Errorf("got (%q, %v), want (%q, true)", ref, pinned, "my-branch")
		}
	})

	t.Run("falls back to the manifest ref, unpinned", func(t *testing.T) {
		a := testApp(t)
		if err := (&config.Manifest{Ref: "cli-v2-release"}).Save(a.Paths); err != nil {
			t.Fatal(err)
		}
		ref, pinned := a.resolveStackBuildRef("")
		if ref != "cli-v2-release" || pinned {
			t.Errorf("got (%q, %v), want (%q, false)", ref, pinned, "cli-v2-release")
		}
	})

	t.Run("no manifest falls back to the CLI version ref", func(t *testing.T) {
		a := testApp(t)
		ref, pinned := a.resolveStackBuildRef("")
		if want := defaultRef(version); ref != want || pinned {
			t.Errorf("got (%q, %v), want (%q, false)", ref, pinned, want)
		}
	})
}

// TestRecordManifestStackRef verifies that a build-local install records the
// ref the stack was really built from (Draft.StackRef), while other paths keep
// recording the CLI version. Without this, the first successful install would
// clobber the installer-written branch ref and the next `install`/`update`
// would snap back to a version that may not even exist as a git ref.
func TestRecordManifestStackRef(t *testing.T) {
	t.Run("managed-clone build records the built ref", func(t *testing.T) {
		a := testApp(t)
		draft := install.NewDraft()
		draft.StackRef = "cli-v2-release"

		a.recordManifest(draft)

		m, _, err := config.LoadManifest(a.Paths)
		if err != nil {
			t.Fatal(err)
		}
		if m.StackRef != "cli-v2-release" || m.Ref != "cli-v2-release" {
			t.Errorf("StackRef=%q Ref=%q, want both %q", m.StackRef, m.Ref, "cli-v2-release")
		}
	})

	t.Run("without a built ref the CLI version is recorded", func(t *testing.T) {
		a := testApp(t)
		draft := install.NewDraft()

		a.recordManifest(draft)

		m, _, err := config.LoadManifest(a.Paths)
		if err != nil {
			t.Fatal(err)
		}
		if want := firstNonEmpty(version, ""); m.StackRef != want {
			t.Errorf("StackRef=%q, want the CLI version %q", m.StackRef, want)
		}
	})
}

func TestStampTelemetryDecisionKeepsReusedInstanceID(t *testing.T) {
	// The stability contract: an instance id carried over from a prior config
	// by reuseInstallSecrets must survive the consent stamp — re-consenting
	// on reinstall keeps the same telemetry identity instead of churning it.
	draft := install.NewDraft()
	draft.TelemetryInstanceID = "inst-reused-id"

	stampTelemetryDecision(draft, true)

	if !draft.TelemetryEnabled {
		t.Errorf("TelemetryEnabled = false, want true")
	}
	if draft.TelemetryInstanceID != "inst-reused-id" {
		t.Errorf("TelemetryInstanceID = %q, want the reused id", draft.TelemetryInstanceID)
	}
}

func TestStampTelemetryDecisionGeneratesFreshIDWhenEmpty(t *testing.T) {
	draft := install.NewDraft()

	stampTelemetryDecision(draft, true)

	if draft.TelemetryInstanceID == "" {
		t.Errorf("expected a fresh instance id for a first opt-in")
	}
}

func TestStampTelemetryDecisionOptOutGeneratesNoID(t *testing.T) {
	draft := install.NewDraft()

	stampTelemetryDecision(draft, false)

	if draft.TelemetryEnabled {
		t.Errorf("TelemetryEnabled = true, want false")
	}
	if draft.TelemetryInstanceID != "" {
		t.Errorf("TelemetryInstanceID = %q, want empty on opt-out", draft.TelemetryInstanceID)
	}
}

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
	app := &app{App: &cmdcore.App{Out: buf, Err: &bytes.Buffer{}}}
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

	app := &app{App: &cmdcore.App{Out: &bytes.Buffer{}, Err: &bytes.Buffer{}}}
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
	app := &app{App: &cmdcore.App{Out: buf, Err: &bytes.Buffer{}}}
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
	app := &app{App: &cmdcore.App{Out: buf, Err: &bytes.Buffer{}}}
	draft := install.NewDraft()

	reuseInstallSecrets(app, draft, out)

	if draft.EncryptionKeyset != nil {
		t.Errorf("draft mutated on malformed input")
	}
	if !strings.Contains(buf.String(), "could not read prior config") {
		t.Errorf("expected warning, got: %q", buf.String())
	}
}
