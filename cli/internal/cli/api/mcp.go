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
	"net/http"
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
	readOnly       bool
	excludeTools   []string
	logFile        string
	maxResultBytes int64

	// httpMode serves the same tool surface over stateless Streamable HTTP
	// (the isolated-local-daemon mode, mcp_http.go) instead of stdio.
	httpMode bool
	http     mcpHTTPOptions
	// relay is `--connect <url>`: this process becomes the credential-less
	// stdio↔HTTP relay (mcp_connect.go) instead of a server.
	relay mcpRelayOptions
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
			"The command never prompts and never starts or stops the instance.\n\n" +
			"--http serves the same tool surface as a stateless Streamable HTTP daemon\n" +
			"(the isolated-local-daemon mode): a unix socket with OS-identity\n" +
			"(peer-credential) checks by default, or TCP with --listen (token required;\n" +
			"non-loopback additionally requires --allow-non-loopback + TLS). The daemon\n" +
			"holds exactly one context's keys and idle-exits for socket activation — see\n" +
			"deploy/mcp-daemon/ for the systemd/launchd templates.\n\n" +
			"--connect <url> turns this process into the credential-less stdio relay for\n" +
			"stdio-only clients: frames are pumped to the daemon over Streamable HTTP.\n" +
			"Against a local unix socket the relay holds no key material at all; against\n" +
			"a remote https daemon it forwards the short-lived bearer from\n" +
			"$JENTIC_MCP_BEARER or --bearer-file, never persisting it.",
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
	cmd.Flags().Int64Var(&opts.maxResultBytes, "max-result-bytes", defaultMaxResultBytes,
		"hard cap on a relayed response body in a tool result; larger bodies are truncated with {truncated, total_bytes, execution_id}")

	// --http: the isolated-local-daemon serving mode (phase-3 item 9).
	cmd.Flags().BoolVar(&opts.httpMode, "http", false,
		"serve stateless Streamable HTTP instead of stdio (unix socket by default; see --socket/--listen)")
	cmd.Flags().StringVar(&opts.http.socket, "socket", "",
		"unix socket path to serve --http on; callers are checked by OS identity (default: <XDG state dir>/jentic/mcp.sock)")
	cmd.Flags().StringVar(&opts.http.listen, "listen", "",
		"TCP host:port to serve --http on; TCP callers must present the --token-file token")
	cmd.Flags().StringVar(&opts.http.tokenFile, "token-file", "",
		"file holding the bearer token TCP callers must present (mode 0600; required for --listen)")
	cmd.Flags().StringVar(&opts.http.tlsCert, "tls-cert", "", "TLS certificate for --listen (required for a non-loopback bind)")
	cmd.Flags().StringVar(&opts.http.tlsKey, "tls-key", "", "TLS private key for --listen (required for a non-loopback bind)")
	cmd.Flags().BoolVar(&opts.http.allowNonLoopback, "allow-non-loopback", false,
		"explicitly allow a non-loopback --listen bind (still refuses without --tls-cert/--tls-key and --token-file)")
	cmd.Flags().BoolVar(&opts.http.allowUnauthenticated, "allow-unauthenticated", false,
		"serve loopback --listen without a token — every local user may then act as this context; loopback-only")
	cmd.Flags().StringSliceVar(&opts.http.allowOrigins, "allow-origin", nil,
		"Origin values allowed on --http requests besides loopback (repeatable); anything else is refused with 403")
	cmd.Flags().IntSliceVar(&opts.http.allowUIDs, "allow-uid", nil,
		"peer uids allowed to connect to the --http unix socket (repeatable); the daemon's own uid and root always are")
	cmd.Flags().DurationVar(&opts.http.idleTimeout, "idle-timeout", defaultMCPIdleTimeout,
		"exit after this long without a request in --http mode (0 disables) — pairs with socket activation")
	cmd.Flags().BoolVar(&opts.http.fromLaunchd, "from-launchd", false,
		"inherit the launchd inetd-wait listening socket on fd 0 (set by the LaunchDaemon template)")

	// --connect: the credential-less stdio relay (phase-3 item 9).
	cmd.Flags().StringVar(&opts.relay.url, "connect", "",
		"relay stdio to a Streamable HTTP daemon at this URL (unix:///path/mcp.sock, http://<loopback>, or https://…) instead of serving")
	cmd.Flags().StringVar(&opts.relay.bearerFile, "bearer-file", "",
		"file holding a short-lived bearer for a remote --connect target (mode 0600; $"+mcpBearerEnv+" is the env alternative; never persisted)")

	cmd.MarkFlagsMutuallyExclusive("http", "connect")
	cmd.MarkFlagsMutuallyExclusive("socket", "listen")
	return cmd
}

func (a *app) mcpE(cmd *cobra.Command, opts *mcpOptions) error {
	aud := ux.FromContext(cmd.Context())

	logger, closeLog, err := openMCPLogger(opts.logFile)
	if err != nil {
		return reportCoded(aud, err)
	}
	defer closeLog()

	// --connect: this process is the credential-less relay, not a server —
	// no tool surface, no state resolution, no key material.
	if opts.relay.url != "" {
		if err := a.mcpConnectE(cmd.Context(), &opts.relay, logger); err != nil &&
			!errors.Is(err, context.Canceled) && !isClientDisconnect(err) {
			logger.Error("mcp relay exited", "error", redactedErr(err))
			return reportCoded(aud, err)
		}
		return nil
	}

	srv := newMCPServer(a, version, opts, logger)
	// Every control-plane client built during this session composes the
	// server's attribution RoundTripper (User-Agent + session-id fallback)
	// over the SEC-20 resolved transport. The RFC 7523 token-mint exchange
	// rides the same pinned+hooked client: it is threaded into
	// auth.Credentials.HTTPClient by the SDK constructors and the clictx
	// credential builders (#1205), so mints carry these attribution headers
	// and honor a pinned-CA environment's bundle like every other call.
	ctx := clictx.WithTransportHook(cmd.Context(), srv.transportHook())

	// --http: the isolated-local-daemon serving mode — same server assembly,
	// Streamable HTTP transport, idle-exit, socket activation.
	if opts.httpMode {
		logger.Info("mcp http daemon starting", "version", version, "read_only", opts.readOnly, "exclude_tools", opts.excludeTools)
		err := a.mcpHTTPE(ctx, srv, &opts.http, logger)
		if err != nil && !errors.Is(err, context.Canceled) && !errors.Is(err, http.ErrServerClosed) {
			logger.Error("mcp http daemon exited", "error", redactedErr(err))
			return reportCoded(aud, err)
		}
		return nil
	}

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
		logger.Info("mcp client disconnected", "cause", redactedErr(err))
		err = nil
	}
	if err != nil {
		logger.Error("mcp server exited", "error", redactedErr(err))
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
