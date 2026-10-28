package install

import (
	"strings"
	"testing"
)

// Refreshing a deployed stack keeps the registry and repository it already
// names and moves only the tag. Re-deriving the repository would march a
// mirrored or forked install onto the published default — silently where that
// registry is reachable, fatally where it is not.
func TestSwapImageTagKeepsTheRegistryAndRepository(t *testing.T) {
	// Set so a leaked environment cannot be what makes these pass.
	t.Setenv(AppImageTagEnv, "")
	t.Setenv(AppImageRepoEnv, "")

	for _, tc := range []struct{ name, current, version, want string }{
		{"published default", "ghcr.io/jentic/jentic-one-app:0.43.0", "v0.44.0", "ghcr.io/jentic/jentic-one-app:0.44.0"},
		{"corporate mirror", "registry.corp/jentic-one-app:0.43.0", "v0.44.0", "registry.corp/jentic-one-app:0.44.0"},
		{"fork's repository", "ghcr.io/acme/jentic-one-app:0.43.0", "0.44.0", "ghcr.io/acme/jentic-one-app:0.44.0"},
		{"registry with a port", "localhost:5000/jentic-one-app:0.43.0", "v0.44.0", "localhost:5000/jentic-one-app:0.44.0"},
		{"locally loaded tag", "mycorp/jentic-one-app:0.43.0", "v0.44.0", "mycorp/jentic-one-app:0.44.0"},
		{"no registry host", "jentic-one-app:0.43.0", "v0.44.0", "jentic-one-app:0.44.0"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			got, err := SwapImageTag(tc.current, tc.version)
			if err != nil {
				t.Fatalf("SwapImageTag(%q, %q): %v", tc.current, tc.version, err)
			}
			if got != tc.want {
				t.Errorf("SwapImageTag(%q, %q) = %q, want %q", tc.current, tc.version, got, tc.want)
			}
		})
	}
}

// A digest pin names an exact image and there is no way to learn the new
// digest, so it is refused rather than quietly downgraded to a mutable tag.
func TestSwapImageTagRefusesADigestPin(t *testing.T) {
	pinned := "ghcr.io/jentic/jentic-one-app@sha256:" + strings.Repeat("ab", 32)
	_, err := SwapImageTag(pinned, "v0.44.0")
	if err == nil {
		t.Fatal("SwapImageTag = nil error, want a refusal for a digest-pinned image")
	}
	if !strings.Contains(err.Error(), "digest") {
		t.Errorf("error does not explain the digest pin: %v", err)
	}
}

func TestSwapImageTagRefusesANonReleaseVersion(t *testing.T) {
	// "0.44.0+ci.7" is a legal semver but not a legal Docker tag ('+'), so it
	// must be refused here rather than resolving to a reference that only fails
	// deep inside `docker pull`.
	for _, version := range []string{"main", "dev", "", "abc1234", "v0.44.0+ci.7"} {
		if _, err := SwapImageTag("ghcr.io/jentic/jentic-one-app:0.43.0", version); err == nil {
			t.Errorf("SwapImageTag(_, %q) = nil error, want a refusal", version)
		}
	}
}

// A tag the operator chose themselves is still moved to the target release —
// refusing would leave such a stack unable to update at all — but it is
// reported as a pin so the caller can warn.
func TestIsOperatorTagPin(t *testing.T) {
	for _, tc := range []struct {
		ref  string
		want bool
	}{
		{"ghcr.io/jentic/jentic-one-app:canary", true},
		{"ghcr.io/jentic/jentic-one-app:2026-10-28", true},
		{"ghcr.io/jentic/jentic-one-app:0.43.0", false},
		{"ghcr.io/jentic/jentic-one-app:latest", false},
		{"localhost:5000/jentic-one-app:canary", true},
		{"ghcr.io/jentic/jentic-one-app@sha256:abc", false},
	} {
		if got := IsOperatorTagPin(tc.ref); got != tc.want {
			t.Errorf("IsOperatorTagPin(%q) = %v, want %v", tc.ref, got, tc.want)
		}
	}
}

// Every tag the tool can read moves, including an operator's own.
func TestSwapImageTagMovesAnOperatorsTagPin(t *testing.T) {
	got, err := SwapImageTag("ghcr.io/jentic/jentic-one-app:canary", "v0.44.0")
	if err != nil {
		t.Fatalf("SwapImageTag: %v", err)
	}
	if got != "ghcr.io/jentic/jentic-one-app:0.44.0" {
		t.Errorf("SwapImageTag = %q", got)
	}
}

// The tags this tool writes itself DO move: a release tag from a release
// install, and the "latest" fallback from a non-release one.
func TestSwapImageTagMovesTheTagsTheInstallerWrites(t *testing.T) {
	for _, current := range []string{"0.43.0", "latest"} {
		got, err := SwapImageTag("ghcr.io/jentic/jentic-one-app:"+current, "v0.44.0")
		if err != nil {
			t.Fatalf("SwapImageTag(:%s, _): %v", current, err)
		}
		if got != "ghcr.io/jentic/jentic-one-app:0.44.0" {
			t.Errorf("SwapImageTag(:%s, _) = %q", current, got)
		}
	}
}
