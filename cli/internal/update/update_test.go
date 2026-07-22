package update

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestFirstSHA(t *testing.T) {
	out := "4ee3bd3c0ffee1234567890abcdef1234567890a\trefs/heads/feat/cli\n"
	if got := firstSHA(out); got != "4ee3bd3c0ffee1234567890abcdef1234567890a" {
		t.Errorf("firstSHA = %q", got)
	}
	if got := firstSHA("\n  \n"); got != "" {
		t.Errorf("firstSHA(blank) = %q, want empty", got)
	}
}

func TestShortTruncates(t *testing.T) {
	if got := short("4ee3bd3c0ffee"); got != "4ee3bd3" {
		t.Errorf("short = %q, want 4ee3bd3", got)
	}
	if got := short("abc"); got != "abc" {
		t.Errorf("short(abc) = %q, want abc", got)
	}
}

func TestSameCommitPrefixMatch(t *testing.T) {
	if !SameCommit("4ee3bd3", "4ee3bd3c0ffee") {
		t.Errorf("SameCommit should match on common prefix")
	}
	if SameCommit("4ee3bd3", "deadbee") {
		t.Errorf("SameCommit should not match differing SHAs")
	}
	if SameCommit("", "4ee3bd3") || SameCommit("4ee3bd3", "") {
		t.Errorf("SameCommit with empty input should be false")
	}
}

func TestReplaceBinaryBacksUpAndSwaps(t *testing.T) {
	dir := t.TempDir()
	stageDir := t.TempDir() // separate dir to exercise the copy-then-rename path

	target := filepath.Join(dir, "jentic")
	staged := filepath.Join(stageDir, "jentic")
	if err := os.WriteFile(target, []byte("OLD"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(staged, []byte("NEW"), 0o755); err != nil {
		t.Fatal(err)
	}

	backup, err := ReplaceBinary(target, staged)
	if err != nil {
		t.Fatalf("ReplaceBinary: %v", err)
	}
	if backup != target+".bak" {
		t.Errorf("backup = %q, want %q", backup, target+".bak")
	}
	if got, _ := os.ReadFile(target); string(got) != "NEW" {
		t.Errorf("target content = %q, want NEW", got)
	}
	if got, _ := os.ReadFile(backup); string(got) != "OLD" {
		t.Errorf("backup content = %q, want OLD", got)
	}
}

func TestReplaceBinaryNoBackupWhenTargetMissing(t *testing.T) {
	dir := t.TempDir()
	target := filepath.Join(dir, "jentic")
	staged := filepath.Join(dir, "staged")
	if err := os.WriteFile(staged, []byte("NEW"), 0o755); err != nil {
		t.Fatal(err)
	}

	backup, err := ReplaceBinary(target, staged)
	if err != nil {
		t.Fatalf("ReplaceBinary: %v", err)
	}
	if backup != "" {
		t.Errorf("backup = %q, want empty when target did not exist", backup)
	}
	if got, _ := os.ReadFile(target); string(got) != "NEW" {
		t.Errorf("target content = %q, want NEW", got)
	}
}

func TestAuthArgs(t *testing.T) {
	if got := authArgs(""); got != nil {
		t.Errorf("authArgs(\"\") = %v, want nil", got)
	}
	got := authArgs("tok123")
	if len(got) != 2 || got[0] != "-c" || !strings.HasPrefix(got[1], "http.extraheader=Authorization: Basic ") {
		t.Errorf("authArgs(token) = %v, want -c http.extraheader basic auth", got)
	}
}

func TestCandidateRefs(t *testing.T) {
	cases := []struct {
		ref  string
		want []string
	}{
		// Bare semver also tries the fully-qualified release tag.
		{"0.15.0", []string{"0.15.0", "refs/tags/v0.15.0"}},
		{"1.0", []string{"1.0", "refs/tags/v1.0"}},
		// Branches, already-`v`-prefixed tags, and SHA-like refs are used as-is.
		{"main", []string{"main"}},
		{"v0.15.0", []string{"v0.15.0"}},
		{"abc1234", []string{"abc1234"}},
	}
	for _, tc := range cases {
		got := candidateRefs(tc.ref)
		if !equalStrings(got, tc.want) {
			t.Errorf("candidateRefs(%q) = %v, want %v", tc.ref, got, tc.want)
		}
	}
}

func equalStrings(a, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}
