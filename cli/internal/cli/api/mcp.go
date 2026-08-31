package api

// mcp.go is the `jentic mcp` command: a Model Context Protocol server over
// stdio (local-MCP phase 1, PR 1-A). The command registers through the root
// like every other leaf, so it inherits --context/$JENTIC_CONTEXT, the
// interceptor's state injection, fencing, and the migrate gate — it never
// re-implements state resolution. stdout is the JSON-RPC wire: nothing but
// protocol frames may be written to it (the banner skips `mcp`, logs go to a
// file, human-facing text goes nowhere).

import (
	"context"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"os"
	"path/filepath"
	"strings"

	"github.com/modelcontextprotocol/go-sdk/mcp"
	"github.com/spf13/cobra"

	sdkconfig "github.com/jentic/jentic-one/cli/client/config"
	"github.com/jentic/jentic-one/cli/internal/cli/clictx"
	"github.com/jentic/jentic-one/cli/internal/cli/cmdcore"
	"github.com/jentic/jentic-one/cli/internal/cli/ux"
)

// mcpOptions carries the `jentic mcp` flags. There is deliberately no
// targeting flag: context selection is the root --context flag, and broker
// targeting comes from the context's environment (broker_url, never derived).
type mcpOptions struct {
	readOnly     bool
	excludeTools []string
	logFile      string
}

func newMCPCmd(app *app) *cobra.Command {
	opts := &mcpOptions{}
	cmd := &cobra.Command{
		Use:   "mcp",
		Short: "Serve Jentic tools to a local MCP client over stdio",
		Long: "mcp runs a Model Context Protocol server on stdin/stdout for a local MCP\n" +
			"client (Claude Code, Cursor, …) to spawn. It serves the Jentic tool surface\n" +
			"pre-auth: the session always boots — `tools/list` works with no or invalid\n" +
			"configuration — and the `get_started` tool diagnoses this machine's setup\n" +
			"state (config, registration, instance reachability) with the exact operator\n" +
			"instruction for each gap. `whoami` reports the agent identity the control\n" +
			"plane sees. Every tool result carries a top-level `instance` key identifying\n" +
			"the Jentic One instance it came from.\n\n" +
			"Context selection uses the root --context flag / $JENTIC_CONTEXT; broker\n" +
			"targeting comes from the context's environment. stdout is the JSON-RPC wire —\n" +
			"server logs go to --log-file (default under the XDG state dir), never stdout.\n" +
			"The command never prompts and never starts or stops the instance.",
		Args: cobra.NoArgs,
		Annotations: map[string]string{
			// The MCP client owns this process's lifetime (one process per
			// session); the interceptor's 60s non-interactive deadline would
			// kill the session mid-flight. Tool calls bound their own contexts.
			cmdcore.LongRunningAnnotation: "true",
		},
		RunE: func(cmd *cobra.Command, _ []string) error {
			return app.mcpE(cmd, opts)
		},
	}
	cmd.Flags().BoolVar(&opts.readOnly, "read-only", false, "serve only tools annotated read-only")
	cmd.Flags().StringSliceVar(&opts.excludeTools, "exclude-tools", nil, "tool names to withhold from the client (repeatable or comma-separated)")
	cmd.Flags().StringVar(&opts.logFile, "log-file", "", "server log file (default: <XDG state dir>/jentic/logs/mcp.log)")
	return cmd
}

func (a *app) mcpE(cmd *cobra.Command, opts *mcpOptions) error {
	aud := ux.FromContext(cmd.Context())

	logger, closeLog, err := openMCPLogger(opts.logFile)
	if err != nil {
		return reportCoded(aud, err)
	}
	defer closeLog()

	srv := newMCPServer(a, version, opts, logger)
	// Every control-plane client built during this session composes the
	// server's attribution RoundTripper (User-Agent + session-id fallback)
	// over the SEC-20 resolved transport.
	//
	// KNOWN GAP (predates this command; follow-up tracked): the RFC 7523
	// token-mint exchange uses the package-global client in
	// client/auth/oauth.go, which is built outside clictx — mints go out
	// without these attribution headers AND without the SEC-20 CA-pinned
	// transport (a pinned-CA environment's mints fail closed through the
	// untrusted-roots path). Fixing it belongs to the oauth client / DoWith
	// seam work, not here.
	ctx := clictx.WithTransportHook(cmd.Context(), srv.transportHook())

	logger.Info("mcp server starting", "version", version, "read_only", opts.readOnly, "exclude_tools", opts.excludeTools)
	err = srv.run(ctx)
	switch {
	case err == nil:
	case errors.Is(err, context.Canceled):
		// SIGINT/SIGTERM cancelled the root context: a clean shutdown, not an
		// error to report (mirrors `jentic events`' watch loop).
		err = nil
	case isClientDisconnect(err):
		// The MCP client closed (or killed) the stdio pipe — the normal end of
		// a stdio session, even when it arrives mid-flight. Not an error.
		logger.Info("mcp client disconnected", "cause", err.Error())
		err = nil
	}
	if err != nil {
		logger.Error("mcp server exited", "error", err)
		return reportCoded(aud, err)
	}
	return nil
}

// isClientDisconnect reports whether the session ended because the peer went
// away: a clean stdin EOF, the SDK's connection-closed sentinel, or the
// jsonrpc2 "server is closing" abort (an EOF that arrived with calls still in
// flight — clients routinely kill the child without draining). The last is a
// string match because the SDK keeps its jsonrpc2 error types internal.
func isClientDisconnect(err error) bool {
	return errors.Is(err, io.EOF) ||
		errors.Is(err, mcp.ErrConnectionClosed) ||
		strings.Contains(err.Error(), "server is closing")
}

// openMCPLogger opens the server log sink. stdout is the JSON-RPC wire and
// stderr is reserved for the coded error contract, so logs always go to a
// file: --log-file when given, else <XDG state dir>/logs/mcp.log (the state
// dir, not cache — a purge mid-session must not destroy the operator's only
// diagnostic trail). JSON handler so the log is grep/jq-able.
func openMCPLogger(path string) (*slog.Logger, func(), error) {
	if path == "" {
		stateDir, err := sdkconfig.StateDir()
		if err != nil {
			return nil, nil, fmt.Errorf("resolving state dir for the mcp log: %w", err)
		}
		path = filepath.Join(stateDir, "logs", "mcp.log")
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return nil, nil, fmt.Errorf("creating log dir for %s: %w", path, err)
	}
	f, err := os.OpenFile(path, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o600) //nolint:gosec // path is the operator-chosen --log-file (or the XDG default), not untrusted input.
	if err != nil {
		return nil, nil, fmt.Errorf("opening log file %s: %w", path, err)
	}
	logger := slog.New(slog.NewJSONHandler(f, nil))
	return logger, func() { _ = f.Close() }, nil
}

// discardLogger is the fallback sink for tests and for callers that pass no
// file; the SDK requires a non-nil logger only when set, but keeping one
// non-nil avoids nil checks at every log site.
func discardLogger() *slog.Logger {
	return slog.New(slog.NewTextHandler(io.Discard, nil))
}
