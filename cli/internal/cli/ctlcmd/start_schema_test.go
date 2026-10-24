package ctlcmd

import (
	"bytes"
	"errors"
	"io"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"

	"github.com/jentic/jentic-one/cli/internal/cli/cmdcore"
)

// stubCheck installs a fake `docker` on PATH whose `--check` run prints stdout
// and exits with code, so ensureDockerSchema reads a chosen verdict.
func stubCheck(t *testing.T, stdout, code string) {
	t.Helper()
	if runtime.GOOS == "windows" {
		t.Skip("the docker stub is a POSIX shell script")
	}
	dir := t.TempDir()
	script := "#!/bin/sh\ncat <<'STUBEOF'\n" + stdout + "\nSTUBEOF\nexit " + code + "\n"
	if err := os.WriteFile(filepath.Join(dir, "docker"), []byte(script), 0o755); err != nil {
		t.Fatalf("write docker stub: %v", err)
	}
	t.Setenv("PATH", dir+string(os.PathListSeparator)+os.Getenv("PATH"))
}

func TestEnsureDockerSchemaWording(t *testing.T) {
	cases := []struct {
		name    string
		stdout  string
		code    string
		wantErr string
		notErr  string
	}{
		{
			name: "upgrade step pending",
			stdout: "STATUS admin current current=b head=b\n" +
				"STATUS upgrade-step:rule_sets_mark_curated pending\nOVERALL pending",
			code:    "3",
			wantErr: "an upgrade step has not run",
			notErr:  "Back up",
		},
		{
			name:    "schema behind head",
			stdout:  "STATUS admin pending current=a head=b\nOVERALL pending",
			code:    "3",
			wantErr: "database schema is behind this build",
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			stubCheck(t, tc.stdout, tc.code)
			a := &app{App: &cmdcore.App{Out: &bytes.Buffer{}, Err: &bytes.Buffer{}}}
			err := a.ensureDockerSchema("/tmp/compose.yaml")
			if err == nil {
				t.Fatal("expected start to be refused")
			}
			if !strings.Contains(err.Error(), tc.wantErr) {
				t.Errorf("error = %q, want it to contain %q", err, tc.wantErr)
			}
			if !strings.Contains(err.Error(), "jenticctl update --stack-only") {
				t.Errorf("error = %q, want it to name jenticctl update --stack-only", err)
			}
			if tc.notErr != "" && strings.Contains(err.Error(), tc.notErr) {
				t.Errorf("error = %q, must not contain %q", err, tc.notErr)
			}
		})
	}
}

// TestEnsureDockerSchemaContinuesOnUnknown: `OVERALL unknown` (exit 5, the
// runner could not read the upgrade-step ledger) never blocks a start.
func TestEnsureDockerSchemaContinuesOnUnknown(t *testing.T) {
	stubCheck(t, "STATUS admin current current=b head=b\nOVERALL unknown", "5")
	a := &app{App: &cmdcore.App{Out: &bytes.Buffer{}, Err: &bytes.Buffer{}}}
	if err := a.ensureDockerSchema("/tmp/compose.yaml"); err != nil {
		t.Fatalf("ensureDockerSchema = %v, want nil", err)
	}
}

// TestEnsureDockerSchemaCreatesAMissingSchema: `OVERALL uninitialized` (no
// schema, so no data to lose) runs the compose migrations through the shared
// runComposeMigrations seam before the start, and a failure aborts it.
func TestEnsureDockerSchemaCreatesAMissingSchema(t *testing.T) {
	stubCheck(t, "STATUS admin uninitialized current=- head=abc\nOVERALL uninitialized", "3")
	orig := runComposeMigrations
	t.Cleanup(func() { runComposeMigrations = orig })

	var gotPath string
	runComposeMigrations = func(_ io.Writer, composePath string) error {
		gotPath = composePath
		return nil
	}
	a := &app{App: &cmdcore.App{Out: &bytes.Buffer{}, Err: &bytes.Buffer{}}}
	if err := a.ensureDockerSchema("/tmp/compose.yaml"); err != nil {
		t.Fatalf("ensureDockerSchema = %v, want nil", err)
	}
	if gotPath != "/tmp/compose.yaml" {
		t.Errorf("migrations ran on %q, want /tmp/compose.yaml", gotPath)
	}

	runComposeMigrations = func(io.Writer, string) error { return errors.New("exit status 1") }
	err := a.ensureDockerSchema("/tmp/compose.yaml")
	if err == nil || !strings.Contains(err.Error(), "could not initialize the database schema") {
		t.Errorf("ensureDockerSchema = %v, want the schema initialization failure", err)
	}
}
