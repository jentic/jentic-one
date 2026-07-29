package localagent

import (
	"strings"
	"testing"
)

func TestSandboxProfileTargetedHomeDeny(t *testing.T) {
	home := "/Users/alice"
	grants := []string{"/Users/alice/projects/api"}
	p := SandboxProfile(home, grants)

	mustContain := []string{
		"(version 1)",
		"(allow default)",
		`(deny file* (subpath "/Users/alice"))`,
		// full access to the granted subtree (wins by last-match over the home deny)
		`(allow file* (subpath "/Users/alice/projects/api"))`,
		// metadata traversal on the ancestor chain, home-first, via literal (not subpath)
		`(allow file-read-metadata (literal "/Users/alice"))`,
		`(allow file-read-metadata (literal "/Users/alice/projects"))`,
	}
	for _, want := range mustContain {
		if !strings.Contains(p, want) {
			t.Errorf("profile missing %q\n---\n%s", want, p)
		}
	}

	// Last-match-wins: every re-allow must appear AFTER the home deny.
	denyAt := strings.Index(p, `(deny file* (subpath "/Users/alice"))`)
	allowAt := strings.Index(p, `(allow file* (subpath "/Users/alice/projects/api"))`)
	if denyAt < 0 || allowAt < 0 || allowAt < denyAt {
		t.Errorf("grant re-allow must come after the home deny (deny@%d allow@%d)", denyAt, allowAt)
	}

	// The ancestor grant must NOT be a subpath — a subpath literal would re-expose
	// the ancestor's other children (the sibling leak we exist to close).
	if strings.Contains(p, `(allow file* (subpath "/Users/alice/projects"))`) {
		t.Error("ancestor must get metadata-only, not a subpath allow")
	}
}

func TestSandboxProfileIgnoresGrantsOutsideHome(t *testing.T) {
	p := SandboxProfile("/Users/alice", []string{"/Users/Shared/work", "/opt/data"})
	// Grants outside the home are already covered by (allow default); the profile
	// must not emit rules for them.
	for _, outside := range []string{"/Users/Shared/work", "/opt/data"} {
		if strings.Contains(p, outside) {
			t.Errorf("profile should not mention out-of-home grant %q\n%s", outside, p)
		}
	}
}

func TestSandboxProfileNoHome(t *testing.T) {
	p := SandboxProfile("", []string{"/whatever"})
	if strings.Contains(p, "deny") {
		t.Errorf("with no operator home there is nothing to deny:\n%s", p)
	}
	if !strings.Contains(p, "(allow default)") {
		t.Errorf("expected the permissive base:\n%s", p)
	}
}

func TestSandboxProfileDedupesSharedAncestors(t *testing.T) {
	// Two grants under the same parent must not duplicate the ancestor metadata rule.
	p := SandboxProfile("/Users/alice", []string{
		"/Users/alice/projects/api",
		"/Users/alice/projects/web",
	})
	if got := strings.Count(p, `(allow file-read-metadata (literal "/Users/alice/projects"))`); got != 1 {
		t.Errorf("shared ancestor metadata rule emitted %d times, want 1\n%s", got, p)
	}
}

func TestSbplPathEscaping(t *testing.T) {
	got := sbplPath(`/tmp/a"b\c`)
	want := `"/tmp/a\"b\\c"`
	if got != want {
		t.Errorf("sbplPath escaping: got %s want %s", got, want)
	}
}

func TestBwrapArgsHidesHomeAndRebindsGrants(t *testing.T) {
	// Each returned token is individually shell-quoted; unquote for structural
	// assertions on the command shape.
	quoted := bwrapArgs("/usr/bin/claude", "/Users/alice/projects/api", "/Users/alice",
		[]string{"/Users/alice/projects/api", "/opt/outside"})
	args := make([]string, len(quoted))
	for i, q := range quoted {
		args[i] = strings.Trim(q, "'")
	}
	joined := strings.Join(args, " ")

	for _, want := range []string{
		"bwrap --die-with-parent",
		// hide the home behind a tmpfs...
		"--tmpfs /Users/alice",
		// ...then re-bind only the in-home grant over it
		"--bind /Users/alice/projects/api /Users/alice/projects/api",
		"--chdir /Users/alice/projects/api",
	} {
		if !strings.Contains(joined, want) {
			t.Errorf("bwrap args missing %q\ngot: %s", want, joined)
		}
	}
	if args[len(args)-1] != "/usr/bin/claude" {
		t.Errorf("binary must be the final arg, got %q", args[len(args)-1])
	}
	// The out-of-home grant needs no bind — it stays visible through the root bind.
	if strings.Contains(joined, "/opt/outside /opt/outside") {
		t.Errorf("out-of-home grant should not be re-bound:\n%s", joined)
	}
	// tmpfs must come before the re-bind so the bind lands on top.
	if strings.Index(joined, "--tmpfs") > strings.Index(joined, "--bind /Users/alice/projects/api") {
		t.Errorf("tmpfs must precede the grant re-bind:\n%s", joined)
	}
}
