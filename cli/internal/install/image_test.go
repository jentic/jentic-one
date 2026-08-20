package install

import (
	"strings"
	"testing"
)

func TestResolveAppImage(t *testing.T) {
	// Isolate from a developer shell that may export the override envs.
	t.Setenv(AppImageRepoEnv, "")
	t.Setenv(AppImageTagEnv, "")

	tests := []struct {
		name     string
		version  string
		override string
		want     string
	}{
		{"released version maps to :X.Y.Z", "v0.31.0", "", DefaultAppImageRepo + ":0.31.0"},
		{"released version without v prefix", "0.31.0", "", DefaultAppImageRepo + ":0.31.0"},
		{"prerelease semver keeps its tag", "v0.32.0-rc1", "", DefaultAppImageRepo + ":0.32.0-rc1"},
		{"dev falls back to :latest", "dev", "", DefaultAppImageRepo + ":latest"},
		{"empty version falls back to :latest", "", "", DefaultAppImageRepo + ":latest"},
		{"main ref falls back to :latest (no :main image is published)", "main", "", DefaultAppImageRepo + ":latest"},
		{"branch name falls back to :latest", "cli-v2-release", "", DefaultAppImageRepo + ":latest"},
		{"commit sha falls back to :latest", "9a858218", "", DefaultAppImageRepo + ":latest"},
		{"non-semver version falls back to :latest", "0.31", "", DefaultAppImageRepo + ":latest"},
		{"bare-tag override wins over version", "v0.31.0", "edge", DefaultAppImageRepo + ":edge"},
		{"digest override applied to default repo", "v0.31.0", "@sha256:abc123", DefaultAppImageRepo + "@sha256:abc123"},
		{"digest override without @ prefix", "v0.31.0", "sha256:abc123", DefaultAppImageRepo + "@sha256:abc123"},
		{"full ref override passes through", "v0.31.0", "ghcr.io/jentic/jentic-one-app@sha256:deadbeef", "ghcr.io/jentic/jentic-one-app@sha256:deadbeef"},
		{"full ref with tag passes through", "v0.31.0", "my.registry/jentic-one-app:pinned", "my.registry/jentic-one-app:pinned"},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := ResolveAppImage(tt.version, tt.override); got != tt.want {
				t.Errorf("ResolveAppImage(%q, %q) = %q, want %q", tt.version, tt.override, got, tt.want)
			}
		})
	}
}

func TestResolveAppImage_RepoOverrideEnv(t *testing.T) {
	t.Setenv(AppImageTagEnv, "")
	t.Setenv(AppImageRepoEnv, "my.registry/mirror/jentic-one-app")
	if got := ResolveAppImage("v0.31.0", ""); got != "my.registry/mirror/jentic-one-app:0.31.0" {
		t.Errorf("repo override not honored: %q", got)
	}
}

func TestResolveAppImage_TagEnvOverride(t *testing.T) {
	t.Setenv(AppImageRepoEnv, "")
	t.Setenv(AppImageTagEnv, "canary")
	// Env tag override beats the version, but an explicit flag override still wins.
	if got := ResolveAppImage("v0.31.0", ""); got != DefaultAppImageRepo+":canary" {
		t.Errorf("$%s override not honored: %q", AppImageTagEnv, got)
	}
	if got := ResolveAppImage("v0.31.0", "flagwins"); got != DefaultAppImageRepo+":flagwins" {
		t.Errorf("flag override should beat env: %q", got)
	}
}

// TestRenderComposeUsesResolvedAppImage pins A2: a pulled image threads into the
// compose file for the app+broker services; an empty AppImage falls back to the
// local-build tag so the from-source path is unchanged.
func TestRenderComposeUsesResolvedAppImage(t *testing.T) {
	d := NewDraft()
	d.DBBackend = BackendSQLite
	d.RuntimePath = RuntimeDocker

	// Pulled image: compose references the resolved ref, not the local tag.
	cfg := composeConfigFor("/home/u/.jentic")
	cfg.AppImage = "ghcr.io/jentic/jentic-one-app:0.31.0"
	data, err := RenderCompose(d, cfg)
	if err != nil {
		t.Fatalf("RenderCompose: %v", err)
	}
	out := string(data)
	assertValidComposeYAML(t, data)
	if !strings.Contains(out, "image: ghcr.io/jentic/jentic-one-app:0.31.0") {
		t.Errorf("compose should reference the pulled image:\n%s", out)
	}
	if strings.Contains(out, "image: "+AppImageTag) {
		t.Errorf("compose must not fall back to the local tag when an image is set:\n%s", out)
	}

	// Empty AppImage: local-build fallback keeps the historical tag.
	cfg2 := composeConfigFor("/home/u/.jentic")
	data2, err := RenderCompose(d, cfg2)
	if err != nil {
		t.Fatalf("RenderCompose (local): %v", err)
	}
	if !strings.Contains(string(data2), "image: "+AppImageTag) {
		t.Errorf("local-build compose should use %q:\n%s", AppImageTag, data2)
	}
}
