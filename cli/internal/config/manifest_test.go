package config

import "testing"

func TestLoadManifestMissingIsNotFound(t *testing.T) {
	paths := Paths{Root: t.TempDir()}
	m, found, err := LoadManifest(paths)
	if err != nil {
		t.Fatalf("LoadManifest error: %v", err)
	}
	if found {
		t.Errorf("found = true for missing manifest, want false")
	}
	if m.ResolvedRepo() != DefaultRepo {
		t.Errorf("ResolvedRepo = %q, want default %q", m.ResolvedRepo(), DefaultRepo)
	}
}

func TestManifestSaveAndLoadRoundTrip(t *testing.T) {
	paths := Paths{Root: t.TempDir()}
	want := &Manifest{
		Repo:       "jentic/jentic-one",
		Ref:        "feat/cli",
		Commit:     "abc1234",
		CLIVersion: "feat/cli",
		BinaryPath: "/tmp/jentic",
	}
	if err := want.Save(paths); err != nil {
		t.Fatalf("Save error: %v", err)
	}
	if want.InstalledAt == "" {
		t.Errorf("Save did not stamp InstalledAt")
	}

	got, found, err := LoadManifest(paths)
	if err != nil || !found {
		t.Fatalf("LoadManifest err=%v found=%v", err, found)
	}
	if got.Ref != "feat/cli" || got.Commit != "abc1234" || got.BinaryPath != "/tmp/jentic" {
		t.Errorf("round-trip mismatch: %+v", got)
	}
}

func TestMergeStackPreservesCLIFields(t *testing.T) {
	paths := Paths{Root: t.TempDir()}
	// Simulate the installer having written the CLI fields first.
	base := &Manifest{Repo: "jentic/jentic-one", Ref: "feat/cli", Commit: "abc1234", BinaryPath: "/tmp/jentic"}
	if err := base.Save(paths); err != nil {
		t.Fatalf("Save base: %v", err)
	}

	m, _, err := LoadManifest(paths)
	if err != nil {
		t.Fatalf("LoadManifest: %v", err)
	}
	if err := m.MergeStack(paths, ModeDocker, "postgres", "8100", "feat/cli", "def5678", "feat/cli"); err != nil {
		t.Fatalf("MergeStack: %v", err)
	}

	got, _, err := LoadManifest(paths)
	if err != nil {
		t.Fatalf("reload: %v", err)
	}
	if got.Mode != ModeDocker || got.DB != "postgres" {
		t.Errorf("stack fields not merged: mode=%q db=%q", got.Mode, got.DB)
	}
	if got.BrokerPort != "8100" {
		t.Errorf("BrokerPort = %q, want 8100", got.BrokerPort)
	}
	if got.Commit != "def5678" {
		t.Errorf("commit = %q, want refreshed def5678", got.Commit)
	}
	if got.BinaryPath != "/tmp/jentic" {
		t.Errorf("BinaryPath = %q, want preserved /tmp/jentic", got.BinaryPath)
	}
}

// TestMergeStackRecordsStackRef: `install` builds the stack, so it establishes
// the StackRef baseline the next update compares against.
func TestMergeStackRecordsStackRef(t *testing.T) {
	paths := Paths{Root: t.TempDir()}
	m := &Manifest{Repo: "jentic/jentic-one"}
	if err := m.MergeStack(paths, ModeDocker, "postgres", "8100", "v0.25.0", "abc1234", "v0.25.0"); err != nil {
		t.Fatalf("MergeStack: %v", err)
	}
	got, _, err := LoadManifest(paths)
	if err != nil {
		t.Fatalf("reload: %v", err)
	}
	if got.StackRef != "v0.25.0" {
		t.Errorf("StackRef = %q, want v0.25.0", got.StackRef)
	}
}

// TestResolvedStackRefFallsBackToRef: installs written before stack_ref existed
// have no value, and must keep behaving as they did (gated on Ref) rather than
// reporting an empty stack version.
func TestResolvedStackRefFallsBackToRef(t *testing.T) {
	tests := []struct {
		name     string
		manifest Manifest
		want     string
	}{
		{"stack_ref set wins", Manifest{Ref: "v0.26.0", StackRef: "v0.25.0"}, "v0.25.0"},
		{"legacy manifest falls back to ref", Manifest{Ref: "v0.25.0"}, "v0.25.0"},
		{"empty manifest is empty", Manifest{}, ""},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := tt.manifest.ResolvedStackRef(); got != tt.want {
				t.Errorf("ResolvedStackRef() = %q, want %q", got, tt.want)
			}
		})
	}
}

// TestRecordStackBuildPersistsRef: a successful stack rebuild advances StackRef
// without disturbing the CLI's own Ref (the two halves move independently).
func TestRecordStackBuildPersistsRef(t *testing.T) {
	paths := Paths{Root: t.TempDir()}
	m := &Manifest{Repo: "jentic/jentic-one", Ref: "v0.26.0", StackRef: "v0.25.0", CLIVersion: "v0.26.0"}
	if err := m.Save(paths); err != nil {
		t.Fatalf("Save: %v", err)
	}
	if err := m.RecordStackBuild(paths, "v0.26.0"); err != nil {
		t.Fatalf("RecordStackBuild: %v", err)
	}

	got, _, err := LoadManifest(paths)
	if err != nil {
		t.Fatalf("reload: %v", err)
	}
	if got.StackRef != "v0.26.0" {
		t.Errorf("StackRef = %q, want v0.26.0", got.StackRef)
	}
	if got.Ref != "v0.26.0" || got.CLIVersion != "v0.26.0" {
		t.Errorf("CLI fields disturbed: ref=%q cli_version=%q", got.Ref, got.CLIVersion)
	}
}

// TestRecordStackBuildIgnoresEmptyRef: never blank out a known-good StackRef
// when the caller could not resolve a ref.
func TestRecordStackBuildIgnoresEmptyRef(t *testing.T) {
	paths := Paths{Root: t.TempDir()}
	m := &Manifest{StackRef: "v0.25.0"}
	if err := m.RecordStackBuild(paths, ""); err != nil {
		t.Fatalf("RecordStackBuild: %v", err)
	}
	if m.StackRef != "v0.25.0" {
		t.Errorf("StackRef = %q, want preserved v0.25.0", m.StackRef)
	}
}
