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
	for _, version := range []string{"main", "dev", "", "abc1234"} {
		if _, err := SwapImageTag("ghcr.io/jentic/jentic-one-app:0.43.0", version); err == nil {
			t.Errorf("SwapImageTag(_, %q) = nil error, want a refusal", version)
		}
	}
}
