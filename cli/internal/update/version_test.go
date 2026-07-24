package update

import (
	"strings"
	"testing"
)

func TestParseSemver(t *testing.T) {
	cases := []struct {
		in   string
		want semver
		ok   bool
	}{
		{"0.15.3", semver{0, 15, 3}, true},
		{"v0.15.3", semver{0, 15, 3}, true},
		{"  v1.2.0 ", semver{1, 2, 0}, true},
		{"10.20.30", semver{10, 20, 30}, true},
		// Non-canonical / not three-part / non-numeric => not parseable.
		{"0.15", semver{}, false},
		{"1.2.3.4", semver{}, false},
		{"v1.0.0-rc1", semver{}, false},
		{"main", semver{}, false},
		{"dev", semver{}, false},
		{"", semver{}, false},
	}
	for _, tc := range cases {
		got, ok := parseSemver(tc.in)
		if ok != tc.ok || got != tc.want {
			t.Errorf("parseSemver(%q) = (%+v, %v), want (%+v, %v)", tc.in, got, ok, tc.want, tc.ok)
		}
	}
}

func TestCompareSemver(t *testing.T) {
	cases := []struct {
		a, b string
		want int
	}{
		{"1.0.0", "1.0.0", 0},
		{"1.0.1", "1.0.0", 1},
		{"1.0.0", "1.0.1", -1},
		{"1.1.0", "1.0.9", 1},
		{"2.0.0", "1.9.9", 1},
		{"0.9.0", "0.10.0", -1}, // numeric, not lexicographic
	}
	for _, tc := range cases {
		av, _ := parseSemver(tc.a)
		bv, _ := parseSemver(tc.b)
		if got := compareSemver(av, bv); got != tc.want {
			t.Errorf("compareSemver(%q, %q) = %d, want %d", tc.a, tc.b, got, tc.want)
		}
	}
}

func TestNewerAvailable(t *testing.T) {
	cases := []struct {
		installed, latest string
		want              bool
	}{
		{"0.15.2", "0.15.3", true},
		{"v0.15.2", "v0.15.3", true},
		{"0.15.3", "0.15.3", false},
		{"0.16.0", "0.15.3", false},
		// Unparseable installed (dev/branch/SHA) => offer the latest release.
		{"dev", "0.15.3", true},
		{"feat/cli", "v1.0.0", true},
		// Unparseable latest => nothing sensible to update to.
		{"0.15.3", "main", false},
		{"0.15.3", "", false},
	}
	for _, tc := range cases {
		if got := NewerAvailable(tc.installed, tc.latest); got != tc.want {
			t.Errorf("NewerAvailable(%q, %q) = %v, want %v", tc.installed, tc.latest, got, tc.want)
		}
	}
}

func TestHighestReleaseTag(t *testing.T) {
	// Mixed ls-remote --tags output: canonical releases, a noise tag, a
	// pre-release, and a peeled ("^{}") line. Only canonical vX.Y.Z count.
	out := strings.Join([]string{
		"aaaaaaa\trefs/tags/v0.15.0",
		"bbbbbbb\trefs/tags/v0.15.3",
		"ccccccc\trefs/tags/v0.15.3^{}",
		"ddddddd\trefs/tags/cli/v0.16.0",
		"eeeeeee\trefs/tags/v1.0.0-rc1",
		"fffffff\trefs/tags/v0.9.1",
	}, "\n")

	got, ok := highestReleaseTag(out)
	if !ok {
		t.Fatalf("highestReleaseTag returned ok=false, want a match")
	}
	if got != "v0.15.3" {
		t.Errorf("highestReleaseTag = %q, want v0.15.3", got)
	}
}

func TestHighestReleaseTagNoReleases(t *testing.T) {
	out := "aaaaaaa\trefs/tags/cli/v0.16.0\nbbbbbbb\trefs/tags/nightly\n"
	if got, ok := highestReleaseTag(out); ok {
		t.Errorf("highestReleaseTag = (%q, true), want no match", got)
	}
	if _, ok := highestReleaseTag(""); ok {
		t.Errorf("highestReleaseTag(\"\") matched, want no match")
	}
}
