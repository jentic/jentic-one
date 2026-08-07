package ctlcmd

import (
	"bytes"
	"context"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/jentic/jentic-one/cli/internal/cli/cmdcore"
	"github.com/jentic/jentic-one/cli/internal/config"
)

func newTestApp(t *testing.T) (*app, *bytes.Buffer) {
	t.Helper()
	out := &bytes.Buffer{}
	app := &app{App: &cmdcore.App{Paths: config.Paths{Root: t.TempDir()}, Out: out, Err: out}}
	return app, out
}

func TestLastLines(t *testing.T) {
	in := "a\nb\nc\nd\ne\n"
	got, err := lastLines(strings.NewReader(in), 2)
	if err != nil {
		t.Fatalf("lastLines: %v", err)
	}
	if len(got) != 2 || got[0] != "d" || got[1] != "e" {
		t.Errorf("lastLines = %v, want [d e]", got)
	}
}

func TestLastLinesFewerThanN(t *testing.T) {
	got, err := lastLines(strings.NewReader("only\n"), 10)
	if err != nil {
		t.Fatalf("lastLines: %v", err)
	}
	if len(got) != 1 || got[0] != "only" {
		t.Errorf("lastLines = %v, want [only]", got)
	}
}

func TestPrintLastLines(t *testing.T) {
	path := filepath.Join(t.TempDir(), "log")
	if err := os.WriteFile(path, []byte("1\n2\n3\n4\n"), 0o600); err != nil {
		t.Fatalf("write: %v", err)
	}
	var buf bytes.Buffer
	if err := printLastLines(&buf, path, 2); err != nil {
		t.Fatalf("printLastLines: %v", err)
	}
	if buf.String() != "3\n4\n" {
		t.Errorf("printLastLines = %q, want \"3\\n4\\n\"", buf.String())
	}
}

func TestResolveLogPathDefault(t *testing.T) {
	app, _ := newTestApp(t)
	got := app.resolveLogPath(false)
	want := filepath.Join(app.Paths.LogsDir(), "app.log")
	if got != want {
		t.Errorf("resolveLogPath(false) = %q, want %q", got, want)
	}
}

func TestResolveLogPathJSONFallback(t *testing.T) {
	app, _ := newTestApp(t)
	// No install config present → conventional default.
	got := app.resolveLogPath(true)
	want := filepath.Join(app.Paths.LogsDir(), "app.jsonl")
	if got != want {
		t.Errorf("resolveLogPath(true) fallback = %q, want %q", got, want)
	}
}

func TestResolveLogPathJSONFromConfig(t *testing.T) {
	app, _ := newTestApp(t)
	cfg := "logging:\n  file_enabled: true\n  file_dir: /var/log/jentic\n  file_name: structured.jsonl\n"
	if err := os.WriteFile(app.Paths.InstallConfigPath(), []byte(cfg), 0o600); err != nil {
		t.Fatalf("write config: %v", err)
	}
	got := app.resolveLogPath(true)
	if got != "/var/log/jentic/structured.jsonl" {
		t.Errorf("resolveLogPath from config = %q", got)
	}
}

func TestReadLoggingConfigMissing(t *testing.T) {
	if _, _, ok := readLoggingConfig(filepath.Join(t.TempDir(), "nope.yaml")); ok {
		t.Errorf("missing config should report ok=false")
	}
}

func TestLogsErrorsWhenNoFile(t *testing.T) {
	app, _ := newTestApp(t)
	err := app.logsE(context.Background(), &logsOptions{lines: 10})
	if err == nil || !strings.Contains(err.Error(), "no log file") {
		t.Fatalf("expected 'no log file' error, got %v", err)
	}
}

func TestLogsPathFlag(t *testing.T) {
	app, out := newTestApp(t)
	if err := app.logsE(context.Background(), &logsOptions{path: true}); err != nil {
		t.Fatalf("logsE --path: %v", err)
	}
	want := filepath.Join(app.Paths.LogsDir(), "app.log")
	if strings.TrimSpace(out.String()) != want {
		t.Errorf("--path printed %q, want %q", out.String(), want)
	}
}

// safeBuffer is a goroutine-safe sink for the follow test.
type safeBuffer struct {
	mu  sync.Mutex
	buf bytes.Buffer
}

func (s *safeBuffer) Write(p []byte) (int, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.buf.Write(p)
}

func (s *safeBuffer) String() string {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.buf.String()
}

func TestFollowFileStreamsAppends(t *testing.T) {
	path := filepath.Join(t.TempDir(), "log")
	if err := os.WriteFile(path, []byte("old line\n"), 0o600); err != nil {
		t.Fatalf("write: %v", err)
	}

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	sink := &safeBuffer{}
	done := make(chan error, 1)
	go func() { done <- followFile(ctx, sink, path) }()

	// Append after follow has started and seeked to end.
	time.Sleep(150 * time.Millisecond)
	f, err := os.OpenFile(path, os.O_APPEND|os.O_WRONLY, 0o600)
	if err != nil {
		t.Fatalf("open append: %v", err)
	}
	_, _ = f.WriteString("new line\n")
	_ = f.Close()

	deadline := time.After(3 * time.Second)
	for !strings.Contains(sink.String(), "new line") {
		select {
		case <-deadline:
			t.Fatalf("follow did not stream appended data; got %q", sink.String())
		case <-time.After(50 * time.Millisecond):
		}
	}

	cancel()
	if err := <-done; err != nil {
		t.Errorf("followFile returned error: %v", err)
	}
	if strings.Contains(sink.String(), "old line") {
		t.Errorf("follow should only stream new data, got %q", sink.String())
	}
}
