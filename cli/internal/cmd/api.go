package cmd

import (
	"bytes"
	"context"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"strings"

	"github.com/charmbracelet/x/term"
	"github.com/spf13/cobra"

	"github.com/jentic/jentic-one/cli/client"
	"github.com/jentic/jentic-one/cli/client/generated/control"
	apispec "github.com/jentic/jentic-one/cli/internal/cli/apispec"
	"github.com/jentic/jentic-one/cli/internal/cli/clictx"
	"github.com/jentic/jentic-one/cli/internal/cli/ux"
)

type apiOptions struct {
	data        string
	dataFile    string
	failOnError bool
	raw         bool
	live        bool
	query       []string
	headers     []string
}

// newAPICmd is the `jentic api` passthrough: a gh-api-style authenticated escape
// hatch to the CONTROL plane (not the broker — that is `jentic execute`). It
// reuses the SDK transport (auth, session, retry, transport guard) and validates
// the path against the embedded spec so it cannot be aimed at arbitrary URLs
// (impl/5.0 §6a). Not fenced: it mutates host-visible platform state, subject to
// the identity's server-side scopes, but never local config.
func newAPICmd(app *App) *cobra.Command {
	opts := &apiOptions{}
	cmd := &cobra.Command{
		Use:   "api <METHOD> <PATH>",
		Short: "Authenticated passthrough to the control plane",
		Long: "api sends an arbitrary request to the control plane, reusing the same\n" +
			"auth, session, and transport as every other command. The PATH must match a\n" +
			"route in the CLI's embedded OpenAPI spec (use --live to allow routes the\n" +
			"connected server serves that this binary predates). Discover routes with\n" +
			"`jentic api ops` and their contract with `jentic api describe`.\n\n" +
			"Request body (mirrors `jentic execute`): -d/--data inline JSON, -d - for\n" +
			"stdin, --data-file <path>, or auto-stdin when a body is piped/redirected.\n\n" +
			"By default any transport-successful response (2xx or 4xx/5xx) exits 0 and\n" +
			"the body is emitted as-is; --fail-on-error maps non-2xx to exit 1.",
		Example: "  jentic api GET /credentials\n" +
			"  jentic api POST /toolkits -d '{\"name\":\"clarity\"}'\n" +
			"  jentic api POST /apis < api.json\n" +
			"  jentic api GET \"/apis?limit=10\"",
		Args: cobra.ExactArgs(2),
		RunE: func(cmd *cobra.Command, args []string) error {
			return runAPICall(cmd, app, opts, args[0], args[1])
		},
	}
	cmd.Flags().StringVarP(&opts.data, "data", "d", "", "request body JSON (use - for stdin)")
	cmd.Flags().StringVar(&opts.dataFile, "data-file", "", "read request body from this file")
	cmd.Flags().BoolVar(&opts.failOnError, "fail-on-error", false, "exit 1 on a non-2xx response")
	cmd.Flags().BoolVar(&opts.raw, "raw", false, "skip the spec path allowlist (loopback hosts only, for dev)")
	cmd.Flags().BoolVar(&opts.live, "live", false, "fetch the server's /openapi.json instead of the embedded spec")
	cmd.Flags().StringArrayVar(&opts.query, "query", nil, "query parameter as key=value (repeatable)")
	cmd.Flags().StringArrayVar(&opts.headers, "header", nil, "extra header as key=value (repeatable)")

	cmd.AddCommand(newAPIOpsCmd(app))
	cmd.AddCommand(newAPIDescribeCmd(app))
	return cmd
}

func runAPICall(cmd *cobra.Command, app *App, opts *apiOptions, method, path string) error {
	aud := ux.FromContext(cmd.Context())
	method = strings.ToUpper(method)
	if !strings.HasPrefix(path, "/") {
		path = "/" + path
	}

	raw, err := clictx.GetControlRawClient(cmd.Context())
	if err != nil {
		return reportCoded(aud, err)
	}

	// Path allowlist: unless --raw (loopback only), the path must match a spec
	// route, so the passthrough can't be aimed at arbitrary URLs.
	if !opts.raw {
		spec, serr := loadAPISpec(cmd.Context(), opts.live, raw)
		if serr != nil {
			return reportCoded(aud, asCoded(serr))
		}
		if _, ok := spec.Match(method, path); !ok {
			return reportCoded(aud, unmatchedPathError(spec, method, path, opts.live))
		}
	} else if err := requireLoopbackRaw(raw.Server); err != nil {
		return reportCoded(aud, err)
	}

	// Append --query params.
	if len(opts.query) > 0 {
		qv := url.Values{}
		for _, kv := range opts.query {
			k, v, ok := strings.Cut(kv, "=")
			if !ok {
				return reportCoded(aud, &ux.CodedError{Code: ux.CodeMissingArgument, Msg: fmt.Sprintf("invalid --query %q; expected key=value", kv)})
			}
			qv.Add(k, v)
		}
		sep := "?"
		if strings.Contains(path, "?") {
			sep = "&"
		}
		path += sep + qv.Encode()
	}

	body, berr := resolveAPIBody(opts)
	if berr != nil {
		return reportCoded(aud, &ux.CodedError{Code: ux.CodeMissingArgument, Msg: berr.Error()})
	}

	resp, err := client.RawControlRequest(cmd.Context(), raw, method, path, body, opts.headers...)
	if err != nil {
		return reportCoded(aud, &ux.CodedError{Code: ux.CodeInternalError, Msg: fmt.Sprintf("request failed: %v", err)})
	}
	defer func() { _ = resp.Body.Close() }()

	// Backend version negotiation (impl/5.0 §6a): a 404 on a route the EMBEDDED
	// spec advertises can mean "this connected server predates the route", not a
	// typo. Probe the server version once (lazy, only on the 404) and enrich the
	// error so the agent learns to upgrade the backend or use --live, rather than
	// treating a real route as nonexistent. Skipped for --raw (no allowlist) and
	// --live (the route came from the server's own spec, so a 404 is a plain 404).
	if resp.StatusCode == http.StatusNotFound && !opts.raw && !opts.live {
		if coded := negotiate404(cmd.Context(), raw, method, path); coded != nil {
			return reportCoded(aud, coded)
		}
	}

	return emitAPIResponse(app.Out, resp, opts.failOnError)
}

// emitAPIResponse writes the response body verbatim to out (the payload is the
// API's, not a CLI envelope — impl/5.0 §6a), then maps the status to an exit code:
// exit 0 for any transport-successful response by default, or exit 1 on non-2xx
// when --fail-on-error is set.
func emitAPIResponse(out io.Writer, resp *http.Response, failOnError bool) error {
	data, err := io.ReadAll(resp.Body)
	if err != nil {
		return &ux.CodedError{Code: ux.CodeInternalError, Msg: fmt.Sprintf("reading response: %v", err)}
	}
	// Pass the body through the byte-level redaction backstop before printing:
	// even raw API data must not leak a secret to a machine parser.
	if _, werr := out.Write(ux.RedactBytes(data)); werr != nil {
		return werr
	}
	if len(data) > 0 && data[len(data)-1] != '\n' {
		_, _ = out.Write([]byte("\n"))
	}
	if failOnError && (resp.StatusCode < 200 || resp.StatusCode >= 300) {
		return &ux.CodedError{
			Code: ux.CodeInternalError,
			Msg:  fmt.Sprintf("control plane returned status %d", resp.StatusCode),
		}
	}
	return nil
}

// resolveAPIBody mirrors execute's body contract: -d inline, -d - / auto-stdin,
// --data-file. Returns nil when there is no body.
func resolveAPIBody(opts *apiOptions) (io.Reader, error) {
	switch {
	case opts.data == "-" || (opts.data == "" && opts.dataFile == "" && !term.IsTerminal(os.Stdin.Fd())):
		data, err := io.ReadAll(os.Stdin)
		if err != nil {
			return nil, fmt.Errorf("reading stdin: %w", err)
		}
		if len(data) == 0 {
			return nil, nil
		}
		return bytes.NewReader(data), nil
	case opts.dataFile != "":
		data, err := os.ReadFile(opts.dataFile)
		if err != nil {
			return nil, fmt.Errorf("reading %s: %w", opts.dataFile, err)
		}
		return bytes.NewReader(data), nil
	case opts.data != "":
		return strings.NewReader(opts.data), nil
	}
	return nil, nil
}

// loadAPISpec returns the parsed spec: the embedded vendored control spec by
// default, or the server's live /openapi.json when live is set.
func loadAPISpec(ctx context.Context, live bool, raw *control.Client) (*apispec.Spec, error) {
	if !live {
		return apispec.Parse(control.SpecYAML)
	}
	resp, err := client.RawControlRequest(ctx, raw, http.MethodGet, "/openapi.json", nil)
	if err != nil {
		return nil, &ux.CodedError{Code: ux.CodeResolveFailed, Msg: fmt.Sprintf("fetching live spec: %v", err)}
	}
	defer func() { _ = resp.Body.Close() }()
	if resp.StatusCode != http.StatusOK {
		return nil, &ux.CodedError{Code: ux.CodeResolveFailed, Msg: fmt.Sprintf("live spec returned status %d", resp.StatusCode)}
	}
	data, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, &ux.CodedError{Code: ux.CodeResolveFailed, Msg: fmt.Sprintf("reading live spec: %v", err)}
	}
	return apispec.Parse(data)
}

// unmatchedPathError distinguishes "path exists, wrong method" from "no such
// route", and hints at --live when the embedded spec was used.
func unmatchedPathError(spec *apispec.Spec, method, path string, live bool) *ux.CodedError {
	if spec.HasPath(path) {
		return &ux.CodedError{
			Code: ux.CodeResolveFailed,
			Msg:  fmt.Sprintf("method %s not allowed for %s", method, path),
		}
	}
	e := &ux.CodedError{
		Code: ux.CodeResolveFailed,
		Msg:  fmt.Sprintf("no such route %s %s in the %s spec", method, path, specSource(live)),
	}
	if !live {
		e.Actionable = "Run with --live if the connected server is newer than this CLI, or `jentic api ops` to list routes."
	}
	return e
}

func specSource(live bool) string {
	if live {
		return "live"
	}
	return "embedded"
}

// negotiate404 enriches a 404 on a route the embedded spec DOES advertise (the
// path already passed the allowlist). It probes the connected server's version
// once: on a successful probe, the route exists in the CLI's spec but the server
// returned 404, so the most likely cause is a server that predates the route —
// surfaced as RESOLVE_FAILED with details.route_unsupported_upstream=true and the
// server version, keeping 13 §3a's closed enum untouched while adding actionable
// detail. If the probe itself fails, we return nil so the caller emits the plain
// 404 body rather than guessing.
func negotiate404(ctx context.Context, raw *control.Client, method, path string) *ux.CodedError {
	version, err := client.ProbeServerVersion(ctx, raw)
	if err != nil || version == "" {
		//nolint:nilerr // deliberate: a failed version probe means we can't tell whether the route is unsupported upstream, so we degrade to the plain 404 body rather than fabricating a verdict.
		return nil
	}
	return &ux.CodedError{
		Code:       ux.CodeResolveFailed,
		Msg:        fmt.Sprintf("%s %s is in this CLI's spec but the connected server (v%s) returned 404 — the backend likely predates this route", method, path, version),
		Actionable: "Upgrade the backend, or use --live to see and call what this server actually serves.",
		Details: map[string]any{
			"route_unsupported_upstream": true,
			"server_version":             version,
		},
	}
}

// requireLoopbackRaw gates --raw to loopback control-plane hosts.
func requireLoopbackRaw(server string) *ux.CodedError {
	u, err := url.Parse(server)
	if err != nil {
		return &ux.CodedError{Code: ux.CodeResolveFailed, Msg: fmt.Sprintf("invalid server URL %q", server)}
	}
	host := u.Hostname()
	if host == "localhost" || host == "127.0.0.1" || host == "::1" {
		return nil
	}
	return &ux.CodedError{
		Code: ux.CodeResolveFailed,
		Msg:  "--raw is only permitted against loopback hosts (dev use)",
	}
}

func newAPIOpsCmd(_ *App) *cobra.Command {
	var filter string
	var live bool
	cmd := &cobra.Command{
		Use:   "ops",
		Short: "List control-plane operations (METHOD, path, id, summary)",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			aud := ux.FromContext(cmd.Context())
			var raw *control.Client
			if live {
				var err error
				if raw, err = clictx.GetControlRawClient(cmd.Context()); err != nil {
					return reportCoded(aud, err)
				}
			}
			spec, err := loadAPISpec(cmd.Context(), live, raw)
			if err != nil {
				return reportCoded(aud, asCoded(err))
			}
			aud.Render(spec.List(filter))
			return nil
		},
	}
	cmd.Flags().StringVar(&filter, "filter", "", "only operations whose path/id/summary contains this substring")
	cmd.Flags().BoolVar(&live, "live", false, "list the server's live operations instead of the embedded set")
	return cmd
}

func newAPIDescribeCmd(_ *App) *cobra.Command {
	var live bool
	cmd := &cobra.Command{
		Use:   "describe <METHOD> <PATH>",
		Short: "Describe an operation's params and request/response schema",
		Args:  cobra.ExactArgs(2),
		RunE: func(cmd *cobra.Command, args []string) error {
			aud := ux.FromContext(cmd.Context())
			method := strings.ToUpper(args[0])
			path := args[1]
			if !strings.HasPrefix(path, "/") {
				path = "/" + path
			}
			var raw *control.Client
			if live {
				var err error
				if raw, err = clictx.GetControlRawClient(cmd.Context()); err != nil {
					return reportCoded(aud, err)
				}
			}
			spec, err := loadAPISpec(cmd.Context(), live, raw)
			if err != nil {
				return reportCoded(aud, asCoded(err))
			}
			op, ok := spec.Describe(method, path)
			if !ok {
				return reportCoded(aud, unmatchedPathError(spec, method, path, live))
			}
			aud.Render(op)
			return nil
		},
	}
	cmd.Flags().BoolVar(&live, "live", false, "describe against the server's live spec instead of the embedded one")
	return cmd
}
