package install

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// These cover the schema probe that stops `start` bringing the stack up on an
// unmigrated database (#951). The docker binary is stubbed so the probe's
// contract — how it reads a verdict, and what it does when it can't get one —
// is pinned without needing a real daemon.

// stubDocker installs a fake `docker` on PATH that prints stdout and exits with
// code. args of the last invocation are recorded to argsFile.
func stubDocker(t *testing.T, stdout string, exit int, argsFile string) {
	t.Helper()
	dir := t.TempDir()
	script := "#!/bin/sh\n" +
		"printf '%s\\n' \"$*\" >> '" + argsFile + "'\n" +
		"cat <<'STUBEOF'\n" + stdout + "\nSTUBEOF\n" +
		"exit " + itoa(exit) + "\n"
	path := filepath.Join(dir, "docker")
	if err := os.WriteFile(path, []byte(script), 0o755); err != nil {
		t.Fatalf("write docker stub: %v", err)
	}
	t.Setenv("PATH", dir+string(os.PathListSeparator)+os.Getenv("PATH"))
}

func itoa(n int) string {
	if n == 0 {
		return "0"
	}
	digits := ""
	for n > 0 {
		digits = string(rune('0'+n%10)) + digits
		n /= 10
	}
	return digits
}

// TestComposeSchemaStateReadsVerdict pins the mapping from the runner's OVERALL
// line to a state. `--check` exits 3 by design for the two non-current states,
// so the verdict must be read even though the command "failed".
func TestComposeSchemaStateReadsVerdict(t *testing.T) {
	cases := []struct {
		name   string
		stdout string
		exit   int
		want   SchemaState
	}{
		{
			name:   "uninitialized after a wiped volume",
			stdout: "STATUS admin uninitialized current=- head=abc\nOVERALL uninitialized",
			exit:   3,
			want:   SchemaUninitialized,
		},
		{
			name:   "pending when behind head",
			stdout: "STATUS admin pending current=aaa head=bbb\nOVERALL pending",
			exit:   3,
			want:   SchemaPending,
		},
		{
			name:   "current when at head",
			stdout: "STATUS admin current current=bbb head=bbb\nOVERALL current",
			exit:   0,
			want:   SchemaCurrent,
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			stubDocker(t, tc.stdout, tc.exit, filepath.Join(t.TempDir(), "args"))
			if got := ComposeSchemaState("/tmp/compose.yaml"); got != tc.want {
				t.Errorf("state = %v, want %v", got, tc.want)
			}
		})
	}
}

// TestComposeSchemaStateUnknownWhenUndeterminable is the safety property: a
// probe that cannot answer must NOT be reported as a schema problem. An app
// image predating --check exits 2 from argparse; a dead daemon prints noise.
// Blocking a start on either would be a worse regression than the bug the check
// exists to catch.
func TestComposeSchemaStateUnknownWhenUndeterminable(t *testing.T) {
	cases := []struct {
		name   string
		stdout string
		exit   int
	}{
		{"old image without --check", "unrecognized arguments: --check", 2},
		{"daemon unreachable", "Cannot connect to the Docker daemon", 1},
		{"garbled output", "OVERALL", 0},
		{"unrecognised verdict word", "OVERALL sideways", 0},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			stubDocker(t, tc.stdout, tc.exit, filepath.Join(t.TempDir(), "args"))
			if got := ComposeSchemaState("/tmp/compose.yaml"); got != SchemaUnknown {
				t.Errorf("state = %v, want SchemaUnknown", got)
			}
		})
	}
}

// TestComposeSchemaStateProbeIsReadOnly pins that the probe runs the migration
// entrypoint with --check appended. Without that flag this same argv would
// *apply* migrations, so the flag's presence is a correctness property, not a
// formatting detail.
func TestComposeSchemaStateProbeIsReadOnly(t *testing.T) {
	argsFile := filepath.Join(t.TempDir(), "args")
	stubDocker(t, "OVERALL current", 0, argsFile)

	ComposeSchemaState("/tmp/compose.yaml")
	raw, err := os.ReadFile(argsFile)
	if err != nil {
		t.Fatalf("read recorded args: %v", err)
	}
	got := string(raw)
	if !strings.Contains(got, "--check") {
		t.Errorf("probe must pass --check or it would apply migrations:\n%s", got)
	}
	if !strings.Contains(got, "jentic_one.migrations.run") {
		t.Errorf("probe must use the migration entrypoint:\n%s", got)
	}
	// One-shot container, removed after, non-interactive — same as the migrate run.
	for _, want := range []string{"run", "--rm", "-T"} {
		if !strings.Contains(got, want) {
			t.Errorf("probe args missing %q:\n%s", want, got)
		}
	}
}

// TestComposeSchemaStateUnknownWithoutDocker: no docker at all is undeterminable
// rather than an error, so a local-mode or docker-less environment is unaffected.
func TestComposeSchemaStateUnknownWithoutDocker(t *testing.T) {
	t.Setenv("PATH", t.TempDir())
	if got := ComposeSchemaState("/tmp/compose.yaml"); got != SchemaUnknown {
		t.Errorf("state = %v, want SchemaUnknown", got)
	}
}

// TestParseSchemaVerdictIgnoresStatusLines makes sure the per-database STATUS
// lines (which contain the same words) never get mistaken for the overall
// verdict — only the explicit OVERALL line counts.
func TestParseSchemaVerdictIgnoresStatusLines(t *testing.T) {
	out := "STATUS registry current current=x head=x\n" +
		"STATUS control current current=y head=y\n" +
		"STATUS admin pending current=a head=b\n" +
		"OVERALL pending"
	got, ok := parseSchemaVerdict(out)
	if !ok {
		t.Fatal("expected a verdict")
	}
	if got != SchemaPending {
		t.Errorf("state = %v, want SchemaPending (the OVERALL line, not the first STATUS)", got)
	}

	if _, ok := parseSchemaVerdict("STATUS admin current current=x head=x"); ok {
		t.Error("STATUS lines alone must not yield a verdict")
	}
}
