package api

// mcp_connect.go is `jentic mcp --connect <url>`: the first-party
// credential-less stdio↔Streamable-HTTP relay (phase-3 item 9). A stdio-only
// MCP client spawns this process; every JSON-RPC frame arriving on stdin is
// POSTed to the daemon's /mcp endpoint and the response frames are written
// back to stdout. The relay is a dumb pump — it interprets no methods, keeps
// no session state, and (in the local unix-socket mode) holds NO key
// material: the daemon authenticates the relay by OS identity
// (peer-credential check on the socket), so there is nothing at rest on the
// desktop side to steal (master §3.7.5 client-side residual).
//
// Against a remote daemon (https), the caller supplies a short-lived scoped
// bearer via $JENTIC_MCP_BEARER or --bearer-file; the relay forwards it on
// every request and never persists it anywhere (unlike the third-party
// mcp-remote/mcp-proxy bridges, which cache OAuth tokens in the desktop
// user's home, e.g. plaintext ~/.mcp-auth).

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net"
	"net/http"
	"net/url"
	"os"
	"strings"
	"sync"
	"time"
)

// mcpBearerEnv is the environment variable a caller sets to hand the relay a
// short-lived bearer for a REMOTE daemon (the `env` block of an mcp.json
// entry). Never persisted by the relay.
const mcpBearerEnv = "JENTIC_MCP_BEARER" //nolint:gosec // the env var NAME, not a credential.

// mcpRelayMaxLine bounds one stdio JSON-RPC frame — comfortably above the
// daemon transport's 4 MiB request bound, so the relay never rejects what
// the daemon would have accepted.
const mcpRelayMaxLine = 8 << 20

// mcpRelayConcurrency caps in-flight requests so a runaway client cannot
// fan out unbounded goroutines/connections.
const mcpRelayConcurrency = 8

// mcpRelayOptions carries the `--connect` flags.
type mcpRelayOptions struct {
	url        string
	bearerFile string
}

// mcpRelay pumps frames between a stdio pair and one Streamable HTTP
// endpoint.
type mcpRelay struct {
	endpoint string // fully resolved http(s) URL to POST each frame to
	client   *http.Client
	bearer   string
	logger   *slog.Logger

	outMu sync.Mutex
	out   io.Writer
}

// newMCPRelay validates the target and assembles the pump. The rules are the
// SEC-1 posture applied to this hop:
//
//   - unix://<path>: HTTP over the unix socket; credential-less (a bearer is
//     refused — OS identity IS the credential on this transport).
//   - http://<loopback>: allowed, with or without a bearer.
//   - http://<non-loopback>: refused — a plaintext hop off-host is never
//     acceptable, bearer or not. Use https.
//   - https://…: allowed; the bearer (if any) rides every request.
func newMCPRelay(opts *mcpRelayOptions, out io.Writer, logger *slog.Logger) (*mcpRelay, error) {
	bearer, err := resolveRelayBearer(opts.bearerFile, os.Getenv(mcpBearerEnv))
	if err != nil {
		return nil, err
	}

	target, err := url.Parse(opts.url)
	if err != nil {
		return nil, fmt.Errorf("--connect %q is not a URL: %w", opts.url, err)
	}

	r := &mcpRelay{bearer: bearer, logger: logger, out: out}
	switch target.Scheme {
	case "unix":
		socketPath := target.Path
		if target.Host != "" {
			// unix://relative/path parses the first segment as a host; treat
			// host+path as one filesystem path so both spellings work.
			socketPath = target.Host + target.Path
		}
		if socketPath == "" {
			return nil, fmt.Errorf("--connect %q carries no socket path (expected unix:///path/to/mcp.sock)", opts.url)
		}
		if bearer != "" {
			return nil, errors.New("a bearer does not apply to a unix socket target — the daemon authenticates this process by OS identity; unset " + mcpBearerEnv + " / drop --bearer-file")
		}
		r.endpoint = "http://jentic-mcp" + mcpHTTPPath
		r.client = &http.Client{Transport: &http.Transport{
			DialContext: func(ctx context.Context, _, _ string) (net.Conn, error) {
				var d net.Dialer
				return d.DialContext(ctx, "unix", socketPath)
			},
		}}
	case "http", "https":
		if target.Scheme == "http" && !hostIsLoopback(target.Hostname()) {
			return nil, fmt.Errorf("refusing the plaintext non-loopback target %q — use https", opts.url)
		}
		if target.Path == "" || target.Path == "/" {
			target.Path = mcpHTTPPath
		}
		r.endpoint = target.String()
		r.client = &http.Client{}
	default:
		return nil, fmt.Errorf("--connect %q: unsupported scheme %q (unix://, http://<loopback>, or https://)", opts.url, target.Scheme)
	}
	return r, nil
}

// resolveRelayBearer picks the caller-supplied bearer: --bearer-file wins
// over $JENTIC_MCP_BEARER. The file gets the same permission check as the
// daemon's --token-file.
func resolveRelayBearer(bearerFile, envBearer string) (string, error) {
	if bearerFile != "" {
		token, err := readMCPTokenFile(bearerFile)
		if err != nil {
			return "", err
		}
		return string(token), nil
	}
	return strings.TrimSpace(envBearer), nil
}

// run pumps until stdin closes (the client owning this process went away) or
// the context is cancelled. Frames are relayed concurrently — the daemon is
// stateless, so ordering guarantees belong to JSON-RPC ids, not the pipe.
func (r *mcpRelay) run(ctx context.Context, in io.Reader) error {
	scanner := bufio.NewScanner(in)
	scanner.Buffer(make([]byte, 64<<10), mcpRelayMaxLine)

	sem := make(chan struct{}, mcpRelayConcurrency)
	var wg sync.WaitGroup
	for scanner.Scan() {
		frame := bytes.TrimSpace(scanner.Bytes())
		if len(frame) == 0 {
			continue
		}
		frameCopy := make([]byte, len(frame))
		copy(frameCopy, frame)

		select {
		case sem <- struct{}{}:
		case <-ctx.Done():
			return ctx.Err()
		}
		wg.Add(1)
		go func() {
			defer wg.Done()
			defer func() { <-sem }()
			r.relayFrame(ctx, frameCopy)
		}()
	}
	wg.Wait()
	if err := scanner.Err(); err != nil && !errors.Is(err, io.EOF) {
		return fmt.Errorf("reading stdin: %w", err)
	}
	return nil
}

// relayFrame POSTs one JSON-RPC frame and writes whatever frames come back.
// Failures never kill the session: a request gets a synthesized JSON-RPC
// error so the client can recover; a notification's failure is only logged.
func (r *mcpRelay) relayFrame(ctx context.Context, frame []byte) {
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, r.endpoint, bytes.NewReader(frame))
	if err != nil {
		r.writeErrorFor(frame, fmt.Sprintf("building the relay request: %v", err))
		return
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Accept", "application/json, text/event-stream")
	if r.bearer != "" {
		req.Header.Set("Authorization", "Bearer "+r.bearer)
	}

	resp, err := r.client.Do(req)
	if err != nil {
		r.writeErrorFor(frame, fmt.Sprintf("the MCP daemon is unreachable: %v", redactedErr(err)))
		return
	}
	defer func() { _ = resp.Body.Close() }()

	switch {
	case resp.StatusCode == http.StatusAccepted || resp.StatusCode == http.StatusNoContent:
		// A notification/response frame was accepted; nothing comes back.
	case resp.StatusCode/100 == 2:
		r.writeResponseBody(resp, frame)
	default:
		body, _ := io.ReadAll(io.LimitReader(resp.Body, 4<<10))
		r.writeErrorFor(frame, fmt.Sprintf("the MCP daemon answered HTTP %d: %s", resp.StatusCode, strings.TrimSpace(string(body))))
	}
}

// writeResponseBody relays a 2xx body: a JSON body is one frame; an SSE body
// (the daemon may stream even in JSON-response mode on future revisions) is
// unwrapped one data: event per frame.
func (r *mcpRelay) writeResponseBody(resp *http.Response, frame []byte) {
	mediaType := resp.Header.Get("Content-Type")
	if idx := strings.IndexByte(mediaType, ';'); idx >= 0 {
		mediaType = mediaType[:idx]
	}
	switch strings.TrimSpace(mediaType) {
	case "text/event-stream":
		if err := r.pumpSSE(resp.Body); err != nil {
			r.logger.Warn("mcp relay: sse stream ended abnormally", "error", redactedErr(err))
		}
	default:
		body, err := io.ReadAll(io.LimitReader(resp.Body, mcpRelayMaxLine))
		if err != nil {
			r.writeErrorFor(frame, fmt.Sprintf("reading the daemon response: %v", redactedErr(err)))
			return
		}
		if len(bytes.TrimSpace(body)) == 0 {
			return
		}
		r.writeFrame(body)
	}
}

// pumpSSE writes each SSE event's data payload as one stdio frame.
func (r *mcpRelay) pumpSSE(body io.Reader) error {
	scanner := bufio.NewScanner(body)
	scanner.Buffer(make([]byte, 64<<10), mcpRelayMaxLine)
	var data bytes.Buffer
	flush := func() {
		if data.Len() > 0 {
			r.writeFrame(data.Bytes())
			data.Reset()
		}
	}
	for scanner.Scan() {
		line := scanner.Text()
		switch {
		case line == "":
			flush()
		case strings.HasPrefix(line, "data:"):
			if data.Len() > 0 {
				data.WriteByte('\n')
			}
			data.WriteString(strings.TrimPrefix(strings.TrimPrefix(line, "data:"), " "))
		default:
			// event:/id:/retry:/comments — framing only, nothing to relay.
		}
	}
	flush()
	return scanner.Err()
}

// writeFrame writes one JSON-RPC message to stdout: compacted so embedded
// newlines can never corrupt the newline-delimited stdio framing.
func (r *mcpRelay) writeFrame(raw []byte) {
	var compact bytes.Buffer
	if err := json.Compact(&compact, raw); err != nil {
		r.logger.Warn("mcp relay: dropping a non-JSON frame from the daemon", "error", err)
		return
	}
	r.outMu.Lock()
	defer r.outMu.Unlock()
	_, _ = r.out.Write(compact.Bytes())
	_, _ = r.out.Write([]byte("\n"))
}

// writeErrorFor synthesizes the JSON-RPC error response for a failed
// request frame, so the client's pending call resolves instead of hanging.
// Notifications (no id) have no response to synthesize — log only.
func (r *mcpRelay) writeErrorFor(frame []byte, message string) {
	var probe struct {
		ID     json.RawMessage `json:"id"`
		Method string          `json:"method"`
	}
	_ = json.Unmarshal(frame, &probe)
	if len(probe.ID) == 0 || string(probe.ID) == "null" {
		r.logger.Warn("mcp relay: dropping a failed notification", "method", probe.Method, "error", message)
		return
	}
	resp, err := json.Marshal(map[string]any{
		"jsonrpc": "2.0",
		"id":      probe.ID,
		"error": map[string]any{
			"code":    -32603,
			"message": message,
		},
	})
	if err != nil {
		return
	}
	r.writeFrame(resp)
}

// mcpConnectE is the `--connect` entrypoint: build the pump against the
// resolved target and run it on this process's stdio until the client hangs
// up. It deliberately touches no config, no state dir, and no key material —
// the logger it receives defaults to stderr (openMCPRelayLogger); only an
// explicit --log-file makes the relay write anywhere on disk.
// Frames go to a.Out — the bootstrap-captured stdout seam — never a raw fd.
func (a *app) mcpConnectE(ctx context.Context, opts *mcpRelayOptions, logger *slog.Logger) error {
	relay, err := newMCPRelay(opts, a.Out, logger)
	if err != nil {
		return err
	}
	relay.client.Timeout = 5 * time.Minute // outlast the daemon's widest per-call budget
	logger.Info("mcp relay starting", "target", opts.url, "bearer", relay.bearer != "")
	return relay.run(ctx, os.Stdin)
}
