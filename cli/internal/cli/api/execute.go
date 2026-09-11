package api

import (
	"bytes"
	"context"
	"fmt"
	"io"
	"mime"
	"mime/multipart"
	"net/http"
	"net/textproto"
	"net/url"
	"os"
	"path/filepath"
	"strings"

	sdkclient "github.com/jentic/jentic-one/cli/client"
	"github.com/jentic/jentic-one/cli/internal/agentops"
	"github.com/jentic/jentic-one/cli/internal/cli/clictx"
	"github.com/jentic/jentic-one/cli/internal/cli/ux"
	"github.com/jentic/jentic-one/cli/internal/config"
	"github.com/spf13/cobra"
)

// apiEnvelopeSchemaVersion pins the machine-contract version of the ad-hoc JSON
// envelopes this package emits that are NOT a ux wrapper (the catalog import
// maps; the execute success envelope moved to the shared ux.ExecuteEnvelope,
// which stamps the same version). Their bodies are arbitrary job data so they
// can't reuse ux.List/Result, but they must still carry schema_version like
// every sanctioned envelope (AGT-23). Mirrors ux.currentSchemaVersion; bump in
// lockstep with the ux envelope contract.
const apiEnvelopeSchemaVersion = "1"

// operationInfo — the resolved {method, url|path} pair — moved to the UX-free
// core as agentops.Operation (plan 0.2); resolveOperation below returns it.

type executeOptions struct {
	pathParams     []string
	queryParams    []string
	headers        []string
	data           string
	dataFile       string
	form           []string
	formFile       []string
	raw            bool
	json           bool
	brokerScheme   string
	brokerHost     string
	revision       string
	idempotencyKey string
}

func newExecuteCmd(app *app) *cobra.Command {
	opts := &executeOptions{}

	cmd := &cobra.Command{
		Use:   "execute <METHOD:url | METHOD:/path | operation_id>",
		Short: "Execute an operation through the Jentic broker",
		Long: "execute sends an HTTP request through the Jentic broker. The broker\n" +
			"authenticates the caller with their agent token and injects the stored\n" +
			"upstream credential, so the agent token is never sent to the upstream\n" +
			"API directly. The target can be specified in three ways:\n\n" +
			"  1. METHOD:url — a discovered operation's full URL, the same form\n" +
			"     `jentic search`/`jentic inspect` accept (e.g.\n" +
			"     GET:https://rest.coincap.io/v3/markets). Resolved via inspect, then\n" +
			"     routed through the broker. The space form (`GET <url>`) is also\n" +
			"     accepted, but the colon form is canonical (it needs no shell quoting).\n" +
			"  2. operation_id — resolve via inspect, then route through the broker.\n" +
			"  3. METHOD:/path — a broker-relative path sent to --broker-host\n" +
			"     verbatim (e.g. GET:/v1/pets); the caller supplies the broker path.\n\n" +
			"Path parameters, query parameters, headers, and a request body can be\n" +
			"supplied via flags.\n\n" +
			"When the broker denies the call (e.g. you are not bound to a toolkit\n" +
			"for the API, or no credential is provisioned), it returns an\n" +
			"agent_directive describing how to recover. execute surfaces that\n" +
			"directive on stderr and exits 2 so a script can branch on the denial.\n\n" +
			"Exit codes:\n" +
			"  0 — broker returned a non-denial HTTP response (incl. 2xx and upstream errors)\n" +
			"  1 — local/transport failure (DNS, TLS, timeout, connection refused)\n" +
			"  2 — denied by the broker (carries an agent_directive) or resolve failure\n" +
			"      (inspect error, e.g. unknown operation_id)\n\n" +
			"Broker target: resolved as built-in default (https://127.0.0.1:8100) <\n" +
			"the active environment's broker_url < --broker-scheme/--broker-host. A\n" +
			"local install serves the broker over plain HTTP, so `jentic register`\n" +
			"seeds broker_url=http://127.0.0.1:8100 for a loopback environment. A TLS\n" +
			"error like \"server gave HTTP response to HTTPS client\" means the target\n" +
			"resolved to https against a local http broker — set the environment's\n" +
			"broker_url (`jentic env add <env> --broker-url http://127.0.0.1:8100\n" +
			"--force`) or override per call with --broker-scheme http. For a REMOTE\n" +
			"control plane you MUST set broker_url (or JENTIC_BROKER_URL); execute\n" +
			"refuses with RESOLVE_FAILED rather than dialing the local default.",
		Example: "  jentic execute GET:https://rest.coincap.io/v3/markets --json\n" +
			"  jentic execute listPets --query limit=10 --json\n" +
			"  jentic execute GET:/v1/pets/{petId} --path petId=123 --raw\n" +
			"  echo '{\"name\":\"Bob\"}' | jentic execute POST:/v1/users --json\n" +
			"  jentic execute uploadPic --form demo=true --form-file images=@face.jpg --json\n" +
			"  # Local broker over http, one-off (usually unnecessary — register seeds broker_url):\n" +
			"  jentic execute listPets --broker-scheme http --broker-host 127.0.0.1:8100",
		Args: exactNamedArgs("<METHOD:url | METHOD:/path | operation_id>", "target"),
		RunE: func(cmd *cobra.Command, args []string) error {
			return app.executeE(cmd, opts, args[0])
		},
	}

	cmd.Flags().StringArrayVar(&opts.pathParams, "path", nil, "path parameter as key=value (repeatable)")
	cmd.Flags().StringArrayVar(&opts.queryParams, "query", nil, "query parameter as key=value (repeatable)")
	cmd.Flags().StringArrayVar(&opts.headers, "header", nil, "extra header as key=value (repeatable)")
	cmd.Flags().StringVarP(&opts.data, "data", "d", "", "request body JSON (use - for stdin)")
	cmd.Flags().StringVar(&opts.dataFile, "data-file", "", "read request body from this file")
	cmd.Flags().StringArrayVar(&opts.form, "form", nil, "multipart/form-data text field as key=value (repeatable; cannot be combined with --data/--data-file; a piped stdin body is ignored)")
	cmd.Flags().StringArrayVar(&opts.formFile, "form-file", nil, "multipart/form-data file part as key=@path (repeatable; cannot be combined with --data/--data-file; a piped stdin body is ignored)")
	cmd.Flags().BoolVar(&opts.raw, "raw", false, "stream response body directly to stdout")
	cmd.Flags().BoolVar(&opts.json, "json", false, "force JSON envelope output")
	cmd.Flags().StringVar(&opts.brokerScheme, "broker-scheme", config.DefaultBrokerScheme, "broker target scheme (http or https)")
	cmd.Flags().StringVar(&opts.brokerHost, "broker-host", config.DefaultBrokerHost, "broker target host as host[:port] (no scheme; use --broker-scheme)")
	cmd.Flags().StringVar(&opts.revision, "revision", "", "pin to a specific revision ID for inspect")
	cmd.Flags().StringVar(&opts.idempotencyKey, "idempotency-key", "", "caller-supplied Idempotency-Key so a retried POST/PUT is de-duplicated by the broker")
	planFlags(cmd)

	return cmd
}

func (a *app) executeE(cmd *cobra.Command, opts *executeOptions, target string) error {
	_, token, err := a.agentSession(cmd.Context())
	if err != nil {
		return err
	}

	// Resolve the broker target with precedence defaults < active environment
	// broker_url < flags. A context whose environment declares broker_url
	// routes through THAT broker — without it, pointing a context at a remote
	// install would still execute against the built-in local default.
	flags := cmd.Flags()
	if st := clictx.ActiveContext(cmd.Context()); st != nil && st.BrokerURL != "" {
		u, perr := url.Parse(st.BrokerURL)
		if perr != nil || u.Host == "" || u.Scheme == "" {
			// M2 (review round-3 #3): a broker_url that is SET but malformed
			// (parse error, or missing host/scheme) must not silently fall through
			// to the loopback default — that dials 127.0.0.1 and surfaces a
			// confusing connection-refused instead of naming the real problem.
			// Fail closed with a coded error pointing at the bad value.
			return &ux.CodedError{
				Code: ux.CodeResolveFailed,
				Msg: fmt.Sprintf("environment %q has a malformed broker_url (%q): it must be an absolute URL with a scheme and host",
					st.EnvironmentName, st.BrokerURL),
				Actionable: fmt.Sprintf("Fix it with `jentic env add %s --broker-url https://<broker-host>:<port> --force`, or export JENTIC_BROKER_URL with a valid URL.",
					st.EnvironmentName),
			}
		}
		// SEC-21: in a machine mode (agent/service-account) the broker host is
		// pinned to the environment's configured broker_url. An agent must not
		// be able to redirect its bearer + injected upstream context at an
		// arbitrary host via --broker-host/--broker-scheme. A human operator
		// keeps the override (they own the machine and may be testing). The
		// scheme may still be overridden (http↔https on the same host is the
		// common local papercut, guarded separately by RequireSecureURL).
		if st.IsMachine() && flags.Changed("broker-host") && opts.brokerHost != u.Host {
			return &ux.CodedError{
				Code: ux.CodeResolveFailed,
				Msg: fmt.Sprintf("--broker-host %q is not allowed in %s mode: the broker is pinned to the "+
					"environment's broker_url host (%s)", opts.brokerHost, st.Mode, u.Host),
				Actionable: "Drop --broker-host (it uses the environment's broker_url), or change the environment's broker_url with `jentic env add <env> --broker-url <URL> --force`.",
			}
		}
		if !flags.Changed("broker-scheme") {
			opts.brokerScheme = u.Scheme
		}
		if !flags.Changed("broker-host") {
			opts.brokerHost = u.Host
		}
	}

	// FAIL-CLOSED remote-broker guard (remote-cli-usage F1). Precedence has run;
	// opts.brokerHost is now either the built-in loopback default or an explicit
	// broker. If it is STILL the loopback default but the active environment's
	// control plane (base_url) is remote, the user pointed the CLI at a remote
	// install and never configured a broker. Silently dialing 127.0.0.1 would
	// leak the agent bearer + injected upstream context at the caller's own
	// loopback and fail with a confusing connection-refused. Refuse with a
	// recovery directive instead. Keyed off loopback-ness (reusing the execute
	// core's classifier via agentops.IsLoopbackHost), so a genuinely-local
	// workflow (loopback base_url, seeded loopback broker) never trips it.
	//
	// M1 (review round-3 #3): only fire when the loopback broker is the built-in
	// DEFAULT, not an EXPLICIT operator choice. An operator running
	// `jentic execute --broker-host 127.0.0.1:8100` against a remote control
	// plane (an SSH tunnel / port-forward to the remote broker) deliberately
	// chose a loopback broker — refusing that with "set broker_url" is wrong
	// advice. `flags.Changed` distinguishes the explicit override from the
	// surviving default. (In machine mode the SEC-21 pin above already blocks a
	// disagreeing --broker-host, so honoring it here can't help an agent escape.)
	if st := clictx.ActiveContext(cmd.Context()); st != nil {
		explicitBroker := flags.Changed("broker-scheme") || flags.Changed("broker-host")
		if !explicitBroker && brokerIsLoopbackDefault(opts.brokerHost) && baseURLIsRemote(st.BaseURL) {
			return &ux.CodedError{
				Code: ux.CodeResolveFailed,
				Msg: fmt.Sprintf("environment %q has a remote control plane (%s) but no broker is configured; "+
					"execute would target the local default %s://%s",
					st.EnvironmentName, st.BaseURL, opts.brokerScheme, opts.brokerHost),
				Actionable: fmt.Sprintf("Set the environment's broker_url: `jentic env add %s --url %s --broker-url https://<broker-host>:<port> --force`, "+
					"or in file-less mode export JENTIC_BROKER_URL=https://<broker-host>:<port>. Ask your operator for the broker URL — it is never derived from the control plane.",
					st.EnvironmentName, st.BaseURL),
			}
		}
	}

	// Resolve phase: determine method and path either from METHOD:/path syntax
	// or by inspecting an operation_id.
	opInfo, err := a.resolveOperation(cmd.Context(), target, opts.revision)
	if err != nil {
		return err
	}

	// Parse the key=value flag surfaces (ARCH-4 coded errors stay here — the
	// flag syntax is a CLI concern; agentops receives structured pairs).
	pathParams, err := agentops.ParseKVs(opts.pathParams, func(v string) error { return badFlagKV("--path", v) })
	if err != nil {
		return err
	}
	queryParams, err := agentops.ParseKVs(opts.queryParams, func(v string) error { return badFlagKV("--query", v) })
	if err != nil {
		return err
	}

	// Resolve request body (cobra-side by design: the stdin fallback must never
	// move into agentops — under stdio MCP, stdin is the JSON-RPC wire).
	//
	// Two body modes are mutually exclusive: a raw byte body (--data/--data-file/
	// stdin, defaulting to Content-Type application/json in agentops.BuildRequest)
	// and a multipart/form-data body assembled here from --form/--form-file. When
	// multipart is requested we build the body and carry its boundary Content-Type
	// forward as a header KV, which suppresses the JSON default via BuildRequest's
	// caller-header precedence (#1316).
	usingMultipart := len(opts.form) > 0 || len(opts.formFile) > 0
	var body io.Reader
	var multipartContentType string
	switch {
	case usingMultipart:
		// --form/--form-file cannot be combined with a raw byte body. Reject the
		// explicit raw-body flags; the stdin auto-fallback is only taken when no
		// body flag is set, so it cannot collide here.
		if opts.data != "" || opts.dataFile != "" {
			return &ux.CodedError{
				Code:       ux.CodeMissingArgument,
				Msg:        "--form/--form-file cannot be combined with --data/--data-file",
				Actionable: "Send either a multipart body (--form/--form-file) or a raw body (--data/--data-file), not both.",
			}
		}
		mpBody, ct, mpErr := buildMultipartBody(opts.form, opts.formFile)
		if mpErr != nil {
			return mpErr
		}
		body = mpBody
		multipartContentType = ct
	case opts.data == "-":
		// Explicit stdin body (`-d -`): the caller opted in, so block until EOF.
		data, readErr := io.ReadAll(os.Stdin)
		if readErr != nil {
			return fmt.Errorf("read stdin: %w", readErr)
		}
		if len(data) > 0 {
			body = bytes.NewReader(data)
		}
	case opts.data == "" && opts.dataFile == "" && stdinHasPipedBody(os.Stdin):
		// #1354: implicit stdin fallback (no body flag). Only taken when stdin is a
		// pipe or regular file that actually carries data — a real `echo … |
		// execute`. A non-TTY stdin that is merely INHERITED and idle (a
		// backgrounded process, or an interactive agent/harness whose stdin is a
		// socket/pty with no EOF) must NOT be read: io.ReadAll would block forever
		// there, hanging every body-less execute. That was the bug — the old
		// heuristic keyed on !IsTerminal alone, which is true for those idle fds.
		data, readErr := io.ReadAll(os.Stdin)
		if readErr != nil {
			return fmt.Errorf("read stdin: %w", readErr)
		}
		if len(data) > 0 {
			body = bytes.NewReader(data)
		}
	case opts.dataFile != "":
		data, readErr := os.ReadFile(opts.dataFile)
		if readErr != nil {
			return fmt.Errorf("read %s: %w", opts.dataFile, readErr)
		}
		body = bytes.NewReader(data)
	case opts.data != "":
		body = strings.NewReader(opts.data)
	}

	headers, err := agentops.ParseKVs(opts.headers, func(v string) error { return badFlagKV("--header", v) })
	if err != nil {
		return err
	}
	if multipartContentType != "" {
		// The generated boundary is inseparable from the multipart body bytes,
		// so no caller-supplied Content-Type can ever be correct here — honoring
		// an override would send a header whose boundary does not match the body
		// (a silent upstream parse failure). Reject instead of merging; the
		// generated Content-Type is appended last so it is authoritative.
		for _, kv := range headers {
			if strings.EqualFold(strings.TrimSpace(kv.Key), "Content-Type") {
				return &ux.CodedError{
					Code:       ux.CodeMissingArgument,
					Msg:        "--header Content-Type cannot be combined with --form/--form-file: the multipart Content-Type carries a generated boundary that must match the body",
					Actionable: "Drop the --header Content-Type flag; execute sets the multipart Content-Type (with its boundary) automatically.",
				}
			}
		}
		headers = append(headers, agentops.KV{Key: "Content-Type", Value: multipartContentType})
	}

	// Build phase (agentops.BuildRequest): path-param substitution, query
	// append, the broker catch-all URL, SEC-1 secure-transport guard, bearer/
	// correlation/idempotency headers. The two-phase drive (Build, then Do)
	// exists so --dry-run/--export-plan below can render the built request and
	// stop before firing.
	req, err := agentops.BuildRequest(cmd.Context(), agentops.ExecuteRequest{
		Method:         opInfo.Method,
		URL:            opInfo.URL,
		Path:           opInfo.Path,
		PathParams:     pathParams,
		QueryParams:    queryParams,
		Headers:        headers,
		Body:           body,
		BrokerScheme:   opts.brokerScheme,
		BrokerHost:     opts.brokerHost,
		Token:          token,
		SessionID:      sessionIDFromContext(cmd),
		IdempotencyKey: opts.idempotencyKey,
	})
	if err != nil {
		return err
	}

	// Dry-run / plan (impl/5.0 §5, F8-15). execute is a mutating (side-effecting)
	// call, so it honors --dry-run/--export-plan: render the fully-resolved
	// request that WOULD be sent — method, broker-wrapped URL, and the correlation
	// headers agentops just attached — and STOP before firing. The operation name
	// mirrors the effect ("brokerExecute"); the plan-parity test keeps it honest.
	if maybeEmitPlan(cmd, "brokerExecute", executePlanPayload(req)) {
		return nil
	}

	// Send phase (agentops.DoWith): the SDK broker transport with its response
	// policy, and a bounded body read into the UX-free result. The broker leg
	// rides clictx's SEC-20 CA-pinned client — fail closed on a broken
	// ca_cert_path — with the context's TransportHook composed over it, exactly
	// like the MCP path (§3.7.2). Resolved
	// AFTER the dry-run gate: rendering a plan needs no transport, so --dry-run
	// keeps working on a machine whose CA bundle is momentarily broken.
	hc, err := clictx.BrokerHTTPClient(cmd.Context())
	if err != nil {
		return err
	}
	result, err := agentops.DoWith(hc, req)
	if err != nil {
		return err
	}

	return a.executeOutput(cmd, opts, result)
}

// stdinHasPipedBody reports whether stdin carries a real request body the
// implicit fallback should read (#1354). It is true only for the two shapes a
// deliberate `echo … | execute` / `execute < file` produces:
//
//   - a named pipe (ModeNamedPipe) — the shell pipe case; and
//   - a regular file with non-zero size — the redirection case.
//
// Everything else is left alone so io.ReadAll can never block: a TTY
// (interactive, no piped body), a character device, or — the bug this fixes —
// an inherited non-TTY fd (a backgrounded process, or an interactive
// agent/harness whose stdin is a socket/pty that never sends EOF). Stat errors
// fail closed to false: if we cannot prove a body is present, we do not block
// on the read. An explicit `-d -` bypasses this and blocks by design.
func stdinHasPipedBody(f *os.File) bool {
	if f == nil {
		return false
	}
	info, err := f.Stat()
	if err != nil {
		return false
	}
	mode := info.Mode()
	if mode&os.ModeNamedPipe != 0 {
		return true
	}
	if mode.IsRegular() && info.Size() > 0 {
		return true
	}
	return false
}

// badFlagKV builds the coded error for a malformed key=value flag (ARCH-4). A
// bad --path/--query/--header value is agent-causable INPUT, so it must surface
// a machine error_code (MISSING_ARGUMENT) with an actionable hint — not a bare
// fmt.Errorf that an agent sees only as an untyped generic-failure string.
func badFlagKV(flag, value string) error {
	return &ux.CodedError{
		Code:       ux.CodeMissingArgument,
		Msg:        fmt.Sprintf("invalid %s value %q; expected key=value", flag, value),
		Actionable: fmt.Sprintf("Pass %s as key=value (e.g. %s id=123).", flag, flag),
	}
}

// buildMultipartBody assembles a multipart/form-data body from --form text
// fields (key=value) and --form-file file parts (key=@path), returning the body
// reader and the boundary-carrying Content-Type the broker must forward verbatim
// (#1316). The broker is byte-transparent for the request body, so once the
// boundary Content-Type is set the multipart bytes reach the upstream intact
// with the credential still injected on the headers.
func buildMultipartBody(form, formFile []string) (io.Reader, string, error) {
	var buf bytes.Buffer
	w := multipart.NewWriter(&buf)

	for _, kv := range form {
		key, value, ok := strings.Cut(kv, "=")
		if !ok || strings.TrimSpace(key) == "" {
			return nil, "", badFlagKV("--form", kv)
		}
		// curl's -F treats a leading @ as a file reference; here file parts live
		// on --form-file, so a @-prefixed --form value is almost always a curl
		// habit that would silently send the literal string as text. Fail closed;
		// a doubled @@ is the escape hatch for an intentional literal leading @.
		if strings.HasPrefix(value, "@@") {
			value = value[1:]
		} else if strings.HasPrefix(value, "@") {
			return nil, "", &ux.CodedError{
				Code:       ux.CodeMissingArgument,
				Msg:        fmt.Sprintf("--form value for %q starts with @ (%q); --form sends the literal text, file parts use --form-file", key, value),
				Actionable: fmt.Sprintf("Upload a file with --form-file %s=@path, or send a literal leading @ by doubling it (--form %s=@@…).", key, key),
			}
		}
		if werr := w.WriteField(key, value); werr != nil {
			return nil, "", fmt.Errorf("write form field %q: %w", key, werr)
		}
	}

	for _, kv := range formFile {
		key, spec, ok := strings.Cut(kv, "=")
		path := strings.TrimPrefix(spec, "@")
		if !ok || strings.TrimSpace(key) == "" || !strings.HasPrefix(spec, "@") || path == "" {
			return nil, "", &ux.CodedError{
				Code:       ux.CodeMissingArgument,
				Msg:        fmt.Sprintf("invalid --form-file value %q; expected key=@path", kv),
				Actionable: "Pass --form-file as key=@path (e.g. --form-file images=@face.jpg).",
			}
		}
		f, oerr := os.Open(path) //nolint:gosec // operator-supplied upload path; same trust as --data-file.
		if oerr != nil {
			// A wrong path is agent-causable input (ARCH-4), so it surfaces a
			// machine error_code like the malformed-spec branch above.
			return nil, "", &ux.CodedError{
				Code:       ux.CodeMissingArgument,
				Msg:        fmt.Sprintf("cannot read --form-file %s: %v", path, oerr),
				Actionable: fmt.Sprintf("Check that the file exists and is readable, then retry with --form-file %s=@<path>.", key),
			}
		}
		if fi, serr := f.Stat(); serr == nil && fi.IsDir() {
			_ = f.Close()
			return nil, "", &ux.CodedError{
				Code:       ux.CodeMissingArgument,
				Msg:        fmt.Sprintf("--form-file %s is a directory; expected a file", path),
				Actionable: fmt.Sprintf("Point --form-file %s=@<path> at a regular file.", key),
			}
		}
		part, cerr := createFilePart(w, key, path)
		if cerr != nil {
			_ = f.Close()
			return nil, "", fmt.Errorf("create form file %q: %w", key, cerr)
		}
		// Stream the file straight into the multipart part rather than reading
		// it fully into a slice and copying again (uploads may be up to the
		// broker's 50 MiB multipart cap). The body still accumulates in buf —
		// a *bytes.Buffer keeps the request replayable for the SDK transport's
		// retry rewind (GetBody), which an io.Pipe stream would break.
		if _, werr := io.Copy(part, f); werr != nil {
			_ = f.Close()
			return nil, "", fmt.Errorf("read --form-file %s: %w", path, werr)
		}
		if cerr := f.Close(); cerr != nil {
			return nil, "", fmt.Errorf("close --form-file %s: %w", path, cerr)
		}
	}

	if err := w.Close(); err != nil {
		return nil, "", fmt.Errorf("finalize multipart body: %w", err)
	}
	return &buf, w.FormDataContentType(), nil
}

// createFilePart creates the multipart part for a --form-file upload. Unlike
// multipart.Writer.CreateFormFile — which hardcodes application/octet-stream —
// the part Content-Type is inferred from the file extension so MIME-validating
// upload APIs (image/jpeg, application/pdf, …) accept the part, falling back to
// octet-stream for unknown extensions. Disposition escaping matches
// CreateFormFile via multipart.FileContentDisposition.
func createFilePart(w *multipart.Writer, key, path string) (io.Writer, error) {
	contentType := mime.TypeByExtension(filepath.Ext(path))
	if contentType == "" {
		contentType = "application/octet-stream"
	}
	h := make(textproto.MIMEHeader)
	h.Set("Content-Disposition", multipart.FileContentDisposition(key, filepath.Base(path)))
	h.Set("Content-Type", contentType)
	return w.CreatePart(h)
}

// executePlanPayload summarizes the resolved outbound request for --dry-run/
// --export-plan without consuming its body reader (the body may still be needed
// if this weren't a dry-run; execute returns immediately after emitting the
// plan, but keeping this read-only avoids any coupling). Sensitive headers are
// redacted by the Audience's plan render path (M6), so Authorization is safe to
// include for completeness.
func executePlanPayload(req *http.Request) map[string]any {
	headers := make(map[string]string, len(req.Header))
	for k := range req.Header {
		headers[k] = req.Header.Get(k)
	}
	return map[string]any{
		"method":  req.Method,
		"url":     req.URL.String(),
		"headers": headers,
	}
}

// sessionIDFromContext returns the resolved X-Jentic-Session-Id for this
// invocation from the ActiveState the root interceptor injected (sourced from
// the active context or $JENTIC_SESSION_ID). Empty when no session id is set —
// correlation is best-effort and never blocks a call.
//
// It falls back to $JENTIC_SESSION_ID directly when the resolved state does not
// carry one: that is the exact source the SDK loader (client/config) reads, so
// the header is present on the execute path whenever the env var is set,
// independent of whether the CLI's ActiveState round-tripped it.
//
// The value is untrusted env input, so it passes through the SDK's
// client.SanitizeSessionID (charset + length clamp, SEC-5) — the same
// normalization the SDK applies on its own session editor.
func sessionIDFromContext(cmd *cobra.Command) string {
	if st := clictx.FromContext(cmd.Context()); st != nil && st.ResolvedState != nil {
		if st.SessionID != "" {
			return sdkclient.SanitizeSessionID(st.SessionID)
		}
	}
	return sdkclient.SanitizeSessionID(os.Getenv("JENTIC_SESSION_ID"))
}

// parseMethodPath reports whether target is in METHOD:/path form (a broker-
// relative path). Thin shim over the extracted core (agentops.ParseMethodPath)
// so the CLI-side call sites and tests keep their name.
func parseMethodPath(target string) (method, path string) {
	return agentops.ParseMethodPath(target)
}

// resolveOperation resolves an execute target to its method and destination.
// It is deliberately cobra-free (plan 0.2): METHOD:/path short-circuits with no
// network call (and no session resolution — constructing the control client
// first would fail invocations that never need it), everything else resolves
// through the agentops core over the apiClient Inspector seam.
func (a *app) resolveOperation(ctx context.Context, target, revision string) (*agentops.Operation, error) {
	// METHOD:/path → broker-relative direct send (uses --broker-host/scheme).
	if method, path := parseMethodPath(target); method != "" {
		return &agentops.Operation{Method: method, Path: path}, nil
	}

	// METHOD URL / METHOD:URL (absolute) and opaque operation_id both resolve
	// via inspect, which returns the absolute upstream URL to send to.
	client, err := a.apisSession(ctx)
	if err != nil {
		return nil, err
	}
	return agentops.ResolveOperation(ctx, client, target, revision)
}
