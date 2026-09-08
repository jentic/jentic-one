package ctlcmd

import (
	"bufio"
	"context"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"time"

	"github.com/jentic/jentic-one/cli/internal/proc"
	"github.com/spf13/cobra"
	"gopkg.in/yaml.v3"
)

type logsOptions struct {
	follow bool
	lines  int
	json   bool
	path   bool
}

func newLogsCmd(app *app) *cobra.Command {
	opts := &logsOptions{}
	cmd := &cobra.Command{
		Use:   "logs",
		Short: "View the jentic-one app logs",
		Long: "logs shows the captured output of the locally-installed app\n" +
			"(~/.jentic/logs/app.log). Use --follow to stream new lines as they are\n" +
			"written, or --json to read the structured JSON-lines sink configured by\n" +
			"`jenticctl install`.",
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			return app.logsE(cmd.Context(), opts)
		},
	}
	cmd.Flags().BoolVarP(&opts.follow, "follow", "f", false,
		"stream new log lines as they are written")
	cmd.Flags().IntVarP(&opts.lines, "lines", "n", 200,
		"number of trailing lines to show")
	cmd.Flags().BoolVar(&opts.json, "json", false,
		"read the structured JSON-lines sink instead of console output")
	cmd.Flags().BoolVar(&opts.path, "path", false,
		"print the resolved log file path and exit")
	return cmd
}

func (a *app) logsE(ctx context.Context, opts *logsOptions) error {
	path := a.resolveLogPath(opts.json)

	if opts.path {
		fmt.Fprintln(a.Out, path)
		return nil
	}

	if !proc.FileExists(path) {
		return fmt.Errorf("no log file at %s — run `jenticctl install` or `jenticctl start` first", path)
	}

	if err := printLastLines(a.Out, path, opts.lines); err != nil {
		return err
	}
	if !opts.follow {
		return nil
	}
	return followFile(ctx, a.Out, path)
}

// resolveLogPath returns the file to read. The default is the CLI-captured
// console log (~/.jentic/logs/app.log); --json resolves the structured sink from
// the generated install config (falling back to the conventional app.jsonl).
func (a *app) resolveLogPath(jsonSink bool) string {
	if !jsonSink {
		return filepath.Join(a.Paths.LogsDir(), "app.log")
	}
	dir, name := a.Paths.LogsDir(), "app.jsonl"
	if d, n, ok := readLoggingConfig(a.Paths.InstallConfigPath()); ok {
		if d != "" {
			dir = d
		}
		if n != "" {
			name = n
		}
	}
	return filepath.Join(dir, name)
}

// readLoggingConfig extracts logging.file_dir/file_name from the generated app
// config, if present. ok is false when the file is missing or unparseable.
func readLoggingConfig(path string) (dir, name string, ok bool) {
	data, err := os.ReadFile(path) //nolint:gosec // path is the CLI-managed install config under JENTIC_HOME.
	if err != nil {
		return "", "", false
	}
	var doc struct {
		Logging struct {
			FileDir  string `yaml:"file_dir"`
			FileName string `yaml:"file_name"`
		} `yaml:"logging"`
	}
	if err := yaml.Unmarshal(data, &doc); err != nil {
		return "", "", false
	}
	return doc.Logging.FileDir, doc.Logging.FileName, true
}

// printLastLines writes the final n lines of the file to w.
func printLastLines(w io.Writer, path string, n int) error {
	if n <= 0 {
		return nil
	}
	f, err := os.Open(path) //nolint:gosec // path is a CLI-managed log file under JENTIC_HOME.
	if err != nil {
		return err
	}
	defer f.Close()

	lines, err := lastLines(f, n)
	if err != nil {
		return err
	}
	for _, ln := range lines {
		fmt.Fprintln(w, ln)
	}
	return nil
}

// lastLines returns up to the final n lines read from r, in order.
func lastLines(r io.Reader, n int) ([]string, error) {
	sc := bufio.NewScanner(r)
	// Log lines (and JSON objects) can be long; raise the token limit to 4 MiB.
	sc.Buffer(make([]byte, 0, 64*1024), 4*1024*1024)

	ring := make([]string, 0, n)
	for sc.Scan() {
		if len(ring) == n {
			ring = ring[1:]
		}
		ring = append(ring, sc.Text())
	}
	return ring, sc.Err()
}

// followFile streams data appended to path until ctx is cancelled. It tolerates
// log rotation: if the file shrinks (truncated/rotated) it reopens from the top.
func followFile(ctx context.Context, w io.Writer, path string) error {
	f, err := os.Open(path) //nolint:gosec // path is a CLI-managed log file under JENTIC_HOME.
	if err != nil {
		return err
	}
	defer func() { _ = f.Close() }()

	if _, err := f.Seek(0, io.SeekEnd); err != nil {
		return err
	}
	var emitted int64
	reader := bufio.NewReader(f)

	ticker := time.NewTicker(400 * time.Millisecond)
	defer ticker.Stop()

	for {
		for {
			line, readErr := reader.ReadString('\n')
			if len(line) > 0 {
				fmt.Fprint(w, line)
				emitted += int64(len(line))
			}
			if readErr != nil {
				break // io.EOF (caught up) or a real error surfaced next tick
			}
		}

		select {
		case <-ctx.Done():
			return nil
		case <-ticker.C:
			if fi, statErr := os.Stat(path); statErr == nil && fi.Size() < emitted {
				_ = f.Close()
				reopened, openErr := os.Open(path) //nolint:gosec // same CLI-managed log file.
				if openErr != nil {
					return openErr
				}
				f = reopened
				reader = bufio.NewReader(f)
				emitted = 0
			}
		}
	}
}
