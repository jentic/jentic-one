package localagent

import (
	"strings"
	"testing"
)

func TestSandboxProfileDeniesHumanHomesAndReopensGrants(t *testing.T) {
	agentHome := "/Users/Shared/alice-local-agent"
	grants := []string{"/Users/alice/projects/api"}
	p := SandboxProfile(agentHome, grants)

	mustContain := []string{
		"(version 1)",
		"(allow default)",
		// every human-home root is denied by default, not just the operator's
		`(deny file* (subpath "/Users"))`,
		`(deny file* (subpath "/home"))`,
		// the agent's own home (under /Users/Shared) is re-opened
		`(allow file* (subpath "/Users/Shared/alice-local-agent"))`,
		// the granted subtree is re-opened (wins by last-match over the /Users deny)
		`(allow file* (subpath "/Users/alice/projects/api"))`,
		// metadata traversal on the ancestor chain, root-first, via literal (not subpath)
		`(allow file-read-metadata (literal "/Users/alice"))`,
		`(allow file-read-metadata (literal "/Users/alice/projects"))`,
	}
	for _, want := range mustContain {
		if !strings.Contains(p, want) {
			t.Errorf("profile missing %q\n---\n%s", want, p)
		}
	}

	// Last-match-wins: the grant re-allow must appear AFTER the /Users deny.
	denyAt := strings.Index(p, `(deny file* (subpath "/Users"))`)
	allowAt := strings.Index(p, `(allow file* (subpath "/Users/alice/projects/api"))`)
	if denyAt < 0 || allowAt < 0 || allowAt < denyAt {
		t.Errorf("grant re-allow must come after the /Users deny (deny@%d allow@%d)", denyAt, allowAt)
	}

	// The ancestor grant must NOT be a subpath — that would re-expose the
	// ancestor's other children (the sibling leak we exist to close).
	if strings.Contains(p, `(allow file* (subpath "/Users/alice/projects"))`) {
		t.Error("ancestor must get metadata-only, not a subpath allow")
	}
}

func TestSandboxProfileMarksExecRoutesReadOnly(t *testing.T) {
	// /usr/bin exists on every macOS/Linux box and is a sanctioned exec route, so
	// the profile must deny writes to it — the non-negotiable self-escape boundary.
	p := SandboxProfile("/Users/Shared/agent", nil)
	if !strings.Contains(p, `(deny file-write* (subpath "/usr/bin"))`) {
		t.Errorf("exec routes must be write-denied\n%s", p)
	}
	// The write-deny must come LAST (after any re-allow) so it is authoritative.
	woAt := strings.Index(p, "(deny file-write*")
	reallowAt := strings.LastIndex(p, "(allow file*")
	if woAt < 0 {
		t.Fatalf("no exec-route write-deny found\n%s", p)
	}
	if reallowAt >= 0 && woAt < reallowAt {
		t.Errorf("exec-route write-deny must come after re-allows (deny@%d allow@%d)", woAt, reallowAt)
	}
}

func TestSandboxProfileIgnoresGrantsOutsideHome(t *testing.T) {
	p := SandboxProfile("/Users/Shared/agent", []string{"/opt/data", "/srv/things"})
	// Grants outside every denied home root are already covered by (allow
	// default); the profile must not emit re-allow rules for them.
	for _, outside := range []string{
		`(allow file* (subpath "/opt/data"))`,
		`(allow file* (subpath "/srv/things"))`,
	} {
		if strings.Contains(p, outside) {
			t.Errorf("profile should not re-allow out-of-home grant %q\n%s", outside, p)
		}
	}
}

func TestSandboxProfileReopensAgentHomeUnderUsers(t *testing.T) {
	// The agent home lives under /Users/Shared, inside the blanket /Users deny —
	// the profile MUST re-open it or the agent can't reach its own workspace.
	p := SandboxProfile("/Users/Shared/bob-local-agent", nil)
	if !strings.Contains(p, `(allow file* (subpath "/Users/Shared/bob-local-agent"))`) {
		t.Errorf("agent home under /Users must be re-opened\n%s", p)
	}
	// And its metadata-traversal ancestors so path resolution reaches it.
	if !strings.Contains(p, `(allow file-read-metadata (literal "/Users/Shared"))`) {
		t.Errorf("agent home ancestors must get metadata traversal\n%s", p)
	}
}

func TestSandboxProfileNoAgentHome(t *testing.T) {
	// With no agent home the human-home roots are still denied (the default
	// boundary does not depend on knowing the agent home).
	p := SandboxProfile("", nil)
	if !strings.Contains(p, `(deny file* (subpath "/Users"))`) {
		t.Errorf("human-home roots must be denied even without an agent home:\n%s", p)
	}
	if !strings.Contains(p, "(allow default)") {
		t.Errorf("expected the permissive base:\n%s", p)
	}
}

func TestSandboxProfileDedupesSharedAncestors(t *testing.T) {
	// Two grants under the same parent must not duplicate the ancestor metadata rule.
	p := SandboxProfile("/Users/Shared/agent", []string{
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

func TestBwrapArgsHidesHomesRebindsGrantsAndReadOnlyExec(t *testing.T) {
	// Each returned token is individually shell-quoted; unquote for structural
	// assertions on the command shape.
	quoted := bwrapArgs("/usr/bin/claude", "/Users/alice/projects/api",
		"/Users/Shared/alice-local-agent",
		[]string{"/Users/alice/projects/api", "/opt/outside"})
	args := make([]string, len(quoted))
	for i, q := range quoted {
		args[i] = strings.Trim(q, "'")
	}
	joined := strings.Join(args, " ")

	for _, want := range []string{
		"bwrap --die-with-parent",
		// hide every human-home root behind a tmpfs...
		"--tmpfs /Users",
		// ...then re-bind the agent home and the in-home grant over it
		"--bind /Users/Shared/alice-local-agent /Users/Shared/alice-local-agent",
		"--bind /Users/alice/projects/api /Users/alice/projects/api",
		// exec routes re-mounted read-only (/usr/bin exists everywhere)
		"--ro-bind /usr/bin /usr/bin",
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
	if strings.Contains(joined, "--bind /opt/outside /opt/outside") {
		t.Errorf("out-of-home grant should not be re-bound:\n%s", joined)
	}
	// tmpfs must come before the re-bind so the bind lands on top; the read-only
	// exec bind must come after the grant re-bind.
	if strings.Index(joined, "--tmpfs /Users") > strings.Index(joined, "--bind /Users/alice/projects/api") {
		t.Errorf("tmpfs must precede the grant re-bind:\n%s", joined)
	}
	if strings.Index(joined, "--bind /Users/alice/projects/api") > strings.Index(joined, "--ro-bind /usr/bin") {
		t.Errorf("read-only exec bind must come after grant re-binds:\n%s", joined)
	}
}
