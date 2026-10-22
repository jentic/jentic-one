package api

// vendorcli.go runs an agent's locally installed vendor CLI (`cf`, later `gh`
// and `aws`) through a Jentic broker facade: `jentic cf …` passes every
// argument untouched to the real `cf`, pointed at the facade address the
// control plane lists (GET /cli-facades) and authenticated with a short-lived
// `cit_` invocation token (POST /cli-invocations) minted with the agent's own
// access token. The vendor credential never reaches this machine: the broker
// injects it per call, under the agent's rules, and records every call under
// the token's trace id.
//
// Nothing here is a proxy or interception layer: the wrapper only builds the
// child's environment and runs it. The vendor CLI's stdin/stdout/stderr are
// attached as-is and its exit code passes through unchanged; Jentic outcomes
// travel only in one `jentic: {"directive": …}` stderr line.

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/spf13/cobra"

	"github.com/jentic/jentic-one/cli/client/auth"
	"github.com/jentic/jentic-one/cli/client/generated/control"
	"github.com/jentic/jentic-one/cli/internal/cli/clictx"
	"github.com/jentic/jentic-one/cli/internal/cli/cmdcore"
	"github.com/jentic/jentic-one/cli/internal/cli/ux"
)

// vendorProfile is how one vendor CLI is pointed at its facade. The deployment
// serves it (GET /cli-facades `binary`, `display_name`, `wrapper`), from the
// vendor's CLI profile; builtinVendorProfiles is the fallback for a server
// that does not.
type vendorProfile struct {
	// Vendor is the facade vendor (GET /cli-facades `vendor`, the mint's `vendor`).
	Vendor string
	// CLI is the `jentic <cli>` subcommand and the facade list's `cli`.
	CLI string
	// Binary is the executable looked up on PATH.
	Binary string
	// Display names the vendor in directives.
	Display string
	// EnvSet are the variables set for the child: a literal, or one of the
	// placeholders {address} (the facade address), {address_host} (its
	// hostname) and {token} (the invocation token).
	EnvSet map[string]string
	// StripEnv are credential variables the vendor CLI (or a library it
	// embeds) could authenticate with instead of the invocation token;
	// removed from the child's environment so only that token is presented.
	StripEnv []string
}

// Placeholders an EnvSet value may be.
const (
	envPlaceholderAddress     = "{address}"
	envPlaceholderAddressHost = "{address_host}"
	envPlaceholderToken       = "{token}"
)

// cloudflareProfile wires `cf` when the server serves no wiring. cf
// authenticates with the first credential it finds, CLOUDFLARE_API_TOKEN
// first (process env wins over a `.env` file), and takes its API base from
// CLOUDFLARE_API_BASE_URL. The stripped names cover cf's Access service-token
// pair and the legacy key/email and CF_* names the older Cloudflare tooling
// and SDKs read.
var cloudflareProfile = vendorProfile{
	Vendor:  "cloudflare",
	CLI:     "cf",
	Binary:  "cf",
	Display: "Cloudflare",
	EnvSet: map[string]string{
		"CLOUDFLARE_API_BASE_URL": envPlaceholderAddress,
		"CLOUDFLARE_API_TOKEN":    envPlaceholderToken,
	},
	StripEnv: []string{
		"CLOUDFLARE_API_KEY",
		"CLOUDFLARE_EMAIL",
		"CLOUDFLARE_API_EMAIL",
		"CLOUDFLARE_API_USER_SERVICE_KEY",
		"CLOUDFLARE_ACCESS_CLIENT_ID",
		"CLOUDFLARE_ACCESS_CLIENT_SECRET",
		"CF_API_TOKEN",
		"CF_API_KEY",
		"CF_API_EMAIL",
		"CF_EMAIL",
		"CF_ACCESS_CLIENT_ID",
		"CF_ACCESS_CLIENT_SECRET",
	},
}

// builtinVendorProfiles are the wrapper wiring used when the deployment's
// GET /cli-facades carries none (a server older than CLI profiles), by CLI name.
var builtinVendorProfiles = map[string]vendorProfile{cloudflareProfile.CLI: cloudflareProfile}

// jenticSecretEnv are the CLI's own credential variables. The vendor CLI never
// sees them: the agent's access token must not leave this process.
var jenticSecretEnv = []string{"JENTIC_BEARER_TOKEN", "JENTIC_MCP_BEARER"}

// envCredential names the agent's credential for the vendor when it has
// several (the mint's `credential_name`). `DisableFlagParsing` leaves no room
// for a flag, so it is an environment variable.
const envCredential = "JENTIC_CREDENTIAL" //nolint:gosec // G101: an env var name, not a credential

// envShimDir names a PATH directory of `exec jentic <cli>` shims that the real
// binary lookup skips, so a shim can never resolve to itself.
const envShimDir = "JENTIC_CLI_SHIM_DIR"

// vendorCLIExitRefused is the exit code when the vendor CLI never ran (no
// facade, not installed, no token): the generic-failure code, with the reason
// in the directive line.
const vendorCLIExitRefused = ux.ExitError

const (
	vendorCLICallTimeout   = 30 * time.Second
	vendorCLIRevokeTimeout = 10 * time.Second
	minRenewInterval       = 50 * time.Millisecond
)

// newCfCmd is `jentic cf …`: the local `cf`, through the Cloudflare facade.
func newCfCmd(a *app) *cobra.Command {
	return newVendorCLICmd(a, cloudflareProfile)
}

func newVendorCLICmd(a *app, prof vendorProfile) *cobra.Command {
	return &cobra.Command{
		Use:   prof.CLI + " [args…]",
		Short: fmt.Sprintf("Run your local `%s` through Jentic (%s, via the Broker)", prof.Binary, prof.Display),
		Long: fmt.Sprintf("Runs the %s CLI installed on this machine in the current directory, with every\n"+
			"argument passed through untouched, pointed at the Jentic Broker's %s facade.\n"+
			"Jentic injects the real %s credential per call under your rules and records\n"+
			"every call under one trace; the credential never reaches this machine.\n\n"+
			"%s's stdout, stderr and exit code pass through unchanged. When the command\n"+
			"fails, or cannot run, one `jentic: {\"directive\": …}` line on stderr says what\n"+
			"Jentic saw and what to do next.\n\n"+
			"Set %s=<name> to pick a credential when you hold several for %s.",
			prof.Binary, prof.Display, prof.Display, prof.Binary, envCredential, prof.Display),
		Example:            fmt.Sprintf("  jentic %s zones list\n  %s=prod jentic %s zones list", prof.CLI, envCredential, prof.CLI),
		DisableFlagParsing: true,
		Args:               cobra.ArbitraryArgs,
		RunE: func(cmd *cobra.Command, args []string) error {
			return a.runVendorCLI(cmd, prof, args)
		},
	}
}

// runVendorCLI is the whole wrapper: preflight, mint, run, renew, revoke,
// post-exit directive.
func (a *app) runVendorCLI(cmd *cobra.Command, prof vendorProfile, args []string) error {
	ctx := cmd.Context()
	stderr := cmd.ErrOrStderr()
	refuse := func(d ux.VendorCLIDirective) error {
		ux.RenderVendorCLIDirective(stderr, d)
		return cmdcore.NewExitCodeError(vendorCLIExitRefused)
	}

	client, err := clictx.GetControlClient(ctx)
	if err != nil {
		return refuse(notSignedInDirective(err))
	}

	prof, address, d := resolveVendorFacade(ctx, client, prof)
	if d != nil {
		return refuse(*d)
	}

	binary, err := findVendorBinary(prof.Binary)
	if err != nil {
		return refuse(ux.VendorCLIDirective{Directive: ux.Directive{
			Strategy:    "prompt_human",
			Parameters:  map[string]any{"cli": prof.Binary},
			Instruction: fmt.Sprintf("`%s` is not installed or not on PATH. Ask the user to install the %s CLI, then re-run the command.", prof.Binary, prof.Display),
		}})
	}

	// Mint with a fresh access token: the invocation token never outlives the
	// access token it was minted with, so a nearly expired one would cut the
	// command short. Static credentials (an injected token, an API key) are
	// returned as they are and the mint decides.
	if st := clictx.FromContext(ctx); st != nil && st.ResolvedState != nil {
		if creds, cerr := credsFromState(ctx, st); cerr == nil && auth.CanReExchange(creds) {
			if _, rerr := auth.RefreshBearerToken(creds); rerr != nil {
				return refuse(notSignedInDirective(rerr))
			}
		}
	}

	inv, d := mintInvocation(ctx, client, prof, args)
	if d != nil {
		return refuse(*d)
	}

	revokeOnce := sync.OnceFunc(func() { revokeInvocation(ctx, client, inv.Id) })
	defer revokeOnce()

	child := exec.Command(binary, args...) //nolint:gosec // binary is the vendor CLI resolved on PATH; args are the agent's own pass-through argv.
	child.Stdin, child.Stdout, child.Stderr = cmd.InOrStdin(), cmd.OutOrStdout(), stderr
	child.Env, err = vendorChildEnv(os.Environ(), prof, address, inv.Token)
	if err != nil {
		revokeOnce()
		return refuse(ux.VendorCLIDirective{TraceID: inv.TraceId, Directive: ux.Directive{
			Strategy:    "prompt_human",
			Parameters:  map[string]any{"cli": prof.Binary, "suggested_command": "jentic update"},
			Instruction: fmt.Sprintf("This Jentic wires `%s` in a way this jentic CLI does not understand (%v). Update the jentic CLI, then re-run the command.", prof.Binary, err),
		}})
	}
	if err := child.Start(); err != nil {
		revokeOnce()
		return refuse(ux.VendorCLIDirective{TraceID: inv.TraceId, Directive: ux.Directive{
			Strategy:    "fatal",
			Parameters:  map[string]any{"cli": prof.Binary},
			Instruction: fmt.Sprintf("`%s` could not be started: %v", binary, err),
		}})
	}

	stopSignals := forwardSignals(child.Process, isTerminal(cmd.InOrStdin()))
	stopRenew := renewWhileRunning(ctx, client, inv.Id, time.Until(inv.ExpiresAt))
	waitErr := child.Wait()
	stopRenew()
	stopSignals()
	revokeOnce()

	code, err := childExitCode(waitErr)
	if err != nil {
		return refuse(ux.VendorCLIDirective{TraceID: inv.TraceId, Directive: ux.Directive{
			Strategy:    "fatal",
			Parameters:  map[string]any{"cli": prof.Binary},
			Instruction: fmt.Sprintf("waiting for `%s` failed: %v", prof.Binary, err),
		}})
	}
	if code == 0 {
		return nil
	}
	ux.RenderVendorCLIDirective(stderr, postExitDirective(ctx, client, prof, inv.TraceId))
	return cmdcore.NewExitCodeError(code)
}

// findVendorBinary is exec.LookPath that skips the shim directory and any
// candidate that resolves to this executable, so `jentic cf` never runs itself.
func findVendorBinary(name string) (string, error) {
	shimDir := cleanDir(os.Getenv(envShimDir))
	self := ""
	if exe, err := os.Executable(); err == nil {
		if resolved, rerr := filepath.EvalSymlinks(exe); rerr == nil {
			self = resolved
		}
	}
	for _, dir := range filepath.SplitList(os.Getenv("PATH")) {
		if dir == "" || (shimDir != "" && cleanDir(dir) == shimDir) {
			continue
		}
		candidate, err := exec.LookPath(filepath.Join(dir, name))
		if err != nil {
			continue
		}
		if resolved, rerr := filepath.EvalSymlinks(candidate); rerr == nil && self != "" && resolved == self {
			continue
		}
		return candidate, nil
	}
	return "", fmt.Errorf("%s: not found on PATH", name)
}

func cleanDir(dir string) string {
	if dir == "" {
		return ""
	}
	if abs, err := filepath.Abs(dir); err == nil {
		dir = abs
	}
	if resolved, err := filepath.EvalSymlinks(dir); err == nil {
		dir = resolved
	}
	return filepath.Clean(dir)
}

// servedVendorProfile is the wrapper wiring the deployment serves for facade
// f, from the vendor's CLI profile; false when it serves none (a server older
// than CLI profiles, or a vendor whose CLI is not wrapped).
func servedVendorProfile(f control.CliFacadeResponse) (vendorProfile, bool) {
	if f.Wrapper == nil || len(f.Wrapper.EnvSet) == 0 {
		return vendorProfile{}, false
	}
	prof := vendorProfile{
		Vendor:   f.Vendor,
		CLI:      f.Cli,
		Binary:   f.Cli,
		Display:  f.Vendor,
		EnvSet:   f.Wrapper.EnvSet,
		StripEnv: f.Wrapper.EnvStrip,
	}
	if f.Binary != nil && *f.Binary != "" {
		prof.Binary = *f.Binary
	}
	if f.DisplayName != nil && *f.DisplayName != "" {
		prof.Display = *f.DisplayName
	}
	return prof, true
}

// resolveVendorFacade reads GET /cli-facades for the facade of `jentic
// <prof.CLI>`: its address, and the wiring the deployment serves for it
// (falling back to prof, the built-in wiring, when it serves none). It
// returns the directive to print when the vendor CLI cannot be run.
func resolveVendorFacade(ctx context.Context, client *control.ClientWithResponses, prof vendorProfile) (vendorProfile, string, *ux.VendorCLIDirective) {
	callCtx, cancel := context.WithTimeout(ctx, vendorCLICallTimeout)
	defer cancel()
	resp, err := client.ListCliFacadesWithResponse(callCtx)
	if err := apiErrorFor(resp, err); err != nil {
		return prof, "", controlCallDirective("list the CLI facades", err)
	}
	for _, f := range resp.JSON200.Data {
		if f.Cli != prof.CLI && f.Vendor != prof.Vendor {
			continue
		}
		if served, ok := servedVendorProfile(f); ok {
			prof = served
		} else if len(prof.EnvSet) == 0 {
			return prof, "", &ux.VendorCLIDirective{Directive: ux.Directive{
				Strategy:    "prompt_human",
				Parameters:  map[string]any{"cli": prof.CLI, "suggested_command": "jentic update"},
				Instruction: fmt.Sprintf("This Jentic serves the %s facade but not how to wire `%s`; update Jentic or the jentic CLI.", f.Vendor, prof.CLI),
			}}
		}
		if f.Address == nil || *f.Address == "" {
			return prof, "", &ux.VendorCLIDirective{Directive: ux.Directive{
				Strategy:   "prompt_human",
				Parameters: map[string]any{"vendor": prof.Vendor},
				Instruction: fmt.Sprintf("The %s facade is enabled but this deployment advertises no address for it. "+
					"Ask your operator to set cli_facades.vendors.%s.public_address (or a reachable server.mcp.broker_url).",
					prof.Display, prof.Vendor),
			}}
		}
		return prof, *f.Address, nil
	}
	return prof, "", &ux.VendorCLIDirective{Directive: ux.Directive{
		Strategy:   "prompt_human",
		Parameters: map[string]any{"vendor": prof.Vendor, "suggested_command": "jentic cli list"},
		Instruction: fmt.Sprintf("`jentic %s` is not available on this Jentic deployment: its %s CLI facade is not enabled. "+
			"Ask your operator to enable cli_facades and cli_facades.vendors.%s, then connect a %s credential for you.",
			prof.CLI, prof.Display, prof.Vendor, prof.Display),
	}}
}

// commandTitle is the leading subcommand words of argv (`zones list`,
// `dns records list`), never an argument value: it stops at the first word
// that is not lowercase letters and hyphens, so a flag, an id (`z1`, a hex
// zone id) or a hostname (`example.com`) never rides along. Display-only.
func commandTitle(args []string) string {
	const maxWords = 4
	words := make([]string, 0, maxWords)
	for _, arg := range args {
		if len(words) == maxWords || !isTitleWord(arg) {
			break
		}
		words = append(words, arg)
	}
	return strings.Join(words, " ")
}

func isTitleWord(word string) bool {
	if word == "" || len(word) > 32 || word[0] == '-' {
		return false
	}
	for _, r := range word {
		if (r < 'a' || r > 'z') && r != '-' {
			return false
		}
	}
	return true
}

// mintInvocation mints the command's invocation token.
func mintInvocation(ctx context.Context, client *control.ClientWithResponses, prof vendorProfile, args []string) (*control.CliInvocationCreatedResponse, *ux.VendorCLIDirective) {
	body := control.CreateCliInvocationJSONRequestBody{Vendor: prof.Vendor}
	if title := commandTitle(args); title != "" {
		body.CommandTitle = ptr(title)
	}
	if name := strings.TrimSpace(os.Getenv(envCredential)); name != "" {
		body.CredentialName = ptr(name)
	}
	callCtx, cancel := context.WithTimeout(ctx, vendorCLICallTimeout)
	defer cancel()
	resp, err := client.CreateCliInvocationWithResponse(callCtx, body)
	if err := apiErrorFor(resp, err); err != nil {
		var he *HTTPError
		if errors.As(err, &he) {
			switch he.StatusCode {
			case http.StatusForbidden:
				return nil, &ux.VendorCLIDirective{Directive: ux.Directive{
					Strategy:   "prompt_human",
					Parameters: map[string]any{"suggested_command": "jentic whoami"},
					Instruction: "Jentic refused to mint an invocation token: only an agent holding capabilities:execute and " +
						"signed in with its own access token (not an API key) can run vendor CLIs. " + he.Detail(),
				}}
			case http.StatusNotFound:
				return nil, &ux.VendorCLIDirective{Directive: ux.Directive{
					Strategy:    "prompt_human",
					Parameters:  map[string]any{"vendor": prof.Vendor, "suggested_command": "jentic cli list"},
					Instruction: fmt.Sprintf("The %s CLI facade was switched off. Ask your operator to enable it.", prof.Display),
				}}
			case http.StatusTooManyRequests:
				return nil, &ux.VendorCLIDirective{Directive: ux.Directive{
					Strategy:    "wait",
					Parameters:  map[string]any{"retry_after_seconds": retryAfterSeconds(resp)},
					Instruction: "Too many vendor CLI commands in the last minute. Wait, then re-run the command.",
				}}
			}
		}
		return nil, controlCallDirective("mint an invocation token", err)
	}
	return resp.JSON201, nil
}

func retryAfterSeconds(resp *control.CreateCliInvocationHTTPResp) string {
	if resp == nil || resp.HTTPResponse == nil {
		return ""
	}
	return resp.HTTPResponse.Header.Get("Retry-After")
}

// controlCallDirective is the directive for a failed control-plane call made
// before the vendor CLI ran.
func controlCallDirective(what string, err error) *ux.VendorCLIDirective {
	var he *HTTPError
	if errors.As(err, &he) && he.StatusCode == http.StatusUnauthorized {
		d := notSignedInDirective(err)
		return &d
	}
	return &ux.VendorCLIDirective{Directive: ux.Directive{
		Strategy:    "retry",
		Parameters:  map[string]any{"suggested_command": "jentic doctor"},
		Instruction: fmt.Sprintf("Could not %s on the Jentic control plane (%s). Check that it is reachable, then re-run the command.", what, oneLine(err)),
	}}
}

func notSignedInDirective(err error) ux.VendorCLIDirective {
	coded := asCoded(err)
	step := coded.Actionable
	if step == "" {
		step = "jentic register"
	}
	return ux.VendorCLIDirective{Directive: ux.Directive{
		Strategy:    "prompt_human",
		Parameters:  map[string]any{"suggested_command": step},
		Instruction: "This agent is not signed in to Jentic (" + oneLine(coded) + ").",
	}}
}

func oneLine(err error) string {
	return strings.Join(strings.Fields(err.Error()), " ")
}

// vendorChildEnv is the vendor CLI's environment: a copy of this process's
// with the profile's variables set (the facade address and invocation token
// filled in), every other credential the vendor CLI could use removed, and
// Jentic's own credentials (anything holding an `at_`/`jak_` token) removed.
// A wiring with a placeholder this CLI does not know, or that never hands
// the child the invocation token, is refused.
func vendorChildEnv(parent []string, prof vendorProfile, address, token string) ([]string, error) {
	host := address
	if u, err := url.Parse(address); err == nil && u.Hostname() != "" {
		host = u.Hostname()
	}
	set := make([]string, 0, len(prof.EnvSet))
	hasToken := false
	for _, k := range sortedKeys(prof.EnvSet) {
		v := prof.EnvSet[k]
		switch v {
		case envPlaceholderAddress:
			v = address
		case envPlaceholderAddressHost:
			v = host
		case envPlaceholderToken:
			v, hasToken = token, true
		default:
			if strings.ContainsAny(v, "{}") {
				return nil, fmt.Errorf("unknown placeholder %s=%s", k, v)
			}
		}
		set = append(set, k+"="+v)
	}
	if !hasToken {
		return nil, errors.New("the wiring hands the CLI no invocation token")
	}
	drop := map[string]bool{}
	for k := range prof.EnvSet {
		drop[envKey(k)] = true
	}
	for _, k := range prof.StripEnv {
		drop[k] = true
	}
	for _, k := range jenticSecretEnv {
		drop[k] = true
	}
	env := make([]string, 0, len(parent)+len(set))
	for _, kv := range parent {
		key, value, _ := strings.Cut(kv, "=")
		if drop[envKey(key)] || strings.HasPrefix(value, "at_") || strings.HasPrefix(value, "jak_") {
			continue
		}
		env = append(env, kv)
	}
	return append(env, set...), nil
}

// envKey normalises a variable name for matching (Windows names are
// case-insensitive).
func envKey(key string) string {
	if filepath.Separator == '\\' {
		return strings.ToUpper(key)
	}
	return key
}

// renewWhileRunning renews the invocation token on a heartbeat well inside its
// lifetime until the returned stop function is called. A renewal that fails is
// logged and retried on the next beat; one the server refuses as no longer
// renewable (revoked, expired, past its hard cap) ends the heartbeat.
func renewWhileRunning(ctx context.Context, client *control.ClientWithResponses, id string, lifetime time.Duration) func() {
	interval := max(lifetime/3, minRenewInterval)
	bg := context.WithoutCancel(ctx)
	done := make(chan struct{})
	finished := make(chan struct{})
	go func() {
		defer close(finished)
		ticker := time.NewTicker(interval)
		defer ticker.Stop()
		for {
			select {
			case <-done:
				return
			case <-ticker.C:
				callCtx, cancel := context.WithTimeout(bg, vendorCLICallTimeout)
				resp, err := client.RenewCliInvocationWithResponse(callCtx, id)
				cancel()
				if err := apiErrorFor(resp, err); err != nil {
					slog.Warn("could not renew the CLI invocation token", "invocation_id", id, "error", err)
					var he *HTTPError
					if errors.As(err, &he) && (he.StatusCode == http.StatusConflict || he.StatusCode == http.StatusNotFound) {
						return
					}
					continue
				}
				if resp.JSON200 != nil {
					if next := time.Until(resp.JSON200.ExpiresAt) / 3; next >= minRenewInterval && next != interval {
						interval = next
						ticker.Reset(interval)
					}
				}
			}
		}
	}()
	var once sync.Once
	return func() {
		once.Do(func() { close(done) })
		<-finished
	}
}

// revokeInvocation revokes the token, best effort. It runs on exit, including
// after a signal has cancelled the command context, so it uses its own deadline.
func revokeInvocation(ctx context.Context, client *control.ClientWithResponses, id string) {
	callCtx, cancel := context.WithTimeout(context.WithoutCancel(ctx), vendorCLIRevokeTimeout)
	defer cancel()
	resp, err := client.RevokeCliInvocationWithResponse(callCtx, id)
	if err := apiErrorFor(resp, err); err != nil {
		slog.Warn("could not revoke the CLI invocation token; it expires on its own", "invocation_id", id, "error", err)
	}
}

// forwardSignals relays termination signals to the vendor CLI until the
// returned stop function is called. A terminal already delivers SIGINT to the
// whole foreground process group, so SIGINT is relayed only when stdin is not
// a terminal (e.g. `kill -INT` on this process), never twice.
func forwardSignals(proc *os.Process, interactive bool) func() {
	sigs := []os.Signal{syscall.SIGTERM, syscall.SIGHUP, syscall.SIGQUIT, os.Interrupt}
	ch := make(chan os.Signal, 4)
	signal.Notify(ch, sigs...)
	done := make(chan struct{})
	go func() {
		for {
			select {
			case <-done:
				return
			case sig := <-ch:
				if sig == os.Interrupt && interactive {
					continue
				}
				_ = proc.Signal(sig)
			}
		}
	}()
	return func() {
		signal.Stop(ch)
		close(done)
	}
}

func isTerminal(r any) bool {
	f, ok := r.(*os.File)
	if !ok {
		return false
	}
	fi, err := f.Stat()
	return err == nil && fi.Mode()&os.ModeCharDevice != 0
}

// childExitCode is the vendor CLI's exit status: its own code, or 128+n when a
// signal ended it (the shell convention).
func childExitCode(waitErr error) (int, error) {
	if waitErr == nil {
		return 0, nil
	}
	var exitErr *exec.ExitError
	if !errors.As(waitErr, &exitErr) {
		return 0, waitErr
	}
	if code := exitErr.ExitCode(); code >= 0 {
		return code, nil
	}
	if ws, ok := exitErr.Sys().(syscall.WaitStatus); ok && ws.Signaled() {
		return 128 + int(ws.Signal()), nil
	}
	return ux.ExitError, nil
}
