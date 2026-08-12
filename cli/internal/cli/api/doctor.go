package api

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"os"
	"strings"
	"time"

	"github.com/spf13/cobra"

	"github.com/jentic/jentic-one/cli/client/auth"
	"github.com/jentic/jentic-one/cli/client/config"
	"github.com/jentic/jentic-one/cli/internal/accessclient"
	"github.com/jentic/jentic-one/cli/internal/cli/clictx"
	"github.com/jentic/jentic-one/cli/internal/cli/cmdcore"
	"github.com/jentic/jentic-one/cli/internal/theme"
)

// newDoctorCmd builds the AGENT-side `jentic doctor` (F8-4, impl/5.1 §3c). It is
// deliberately separate from `jenticctl doctor`: jenticctl is absent from agent
// hosts, so this is the ONLY self-check that can run where an agent actually
// lives. It is dependency-light — it links NONE of the installer packages
// (internal/install, internal/proc, internal/update), which the binary-boundary
// arch test (Test8) enforces for the whole `jentic` tree — and read-only: it
// never mints a token, writes config, or contacts the host daemon.
//
// It checks what an agent needs to function: its XDG dirs exist and are private,
// an identity/context resolves, it holds a usable (unexpired) token or API key,
// the control plane is reachable, and the local clock is close enough to the
// token's issue time that the server will accept it (the clock-skew surfacing
// P4.3/P6.2 require, here for the agent sibling — F8-9).
func newDoctorCmd(app *app) *cobra.Command {
	var jsonFlag bool
	cmd := &cobra.Command{
		Use:   "doctor",
		Short: "Diagnose this agent's local jentic setup (read-only)",
		Long: "doctor is the agent-side self-check. It verifies the things an agent needs to\n" +
			"work — its config/state directories, a resolvable identity, a usable token or\n" +
			"API key, control-plane reachability, and local clock skew — and prints a\n" +
			"pass/warn/fail report. It is read-only (never mints tokens or writes config)\n" +
			"and, unlike `jenticctl doctor`, needs no operator tooling, so it runs where an\n" +
			"agent actually lives. It exits non-zero when any check fails (warnings keep a\n" +
			"zero exit), so it is safe to wire into a health probe.\n\n" +
			"Output defaults to JSON when stdout is not a TTY (agent-friendly).",
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			return app.doctorE(cmd, jsonFlag)
		},
	}
	cmd.Flags().BoolVar(&jsonFlag, "json", false, "force JSON output (default when stdout is not a TTY)")
	return cmd
}

// agentCheckStatus is the outcome of a single agent-doctor probe.
type agentCheckStatus int

const (
	agentPass agentCheckStatus = iota
	agentWarn
	agentFail
)

func (s agentCheckStatus) String() string {
	switch s {
	case agentPass:
		return "pass"
	case agentWarn:
		return "warn"
	default:
		return "fail"
	}
}

// agentCheck is one diagnostic result (mirrors the jenticctl doctor JSON shape so
// tooling can parse both binaries' reports identically).
type agentCheck struct {
	Section string           `json:"section"`
	Name    string           `json:"name"`
	Status  agentCheckStatus `json:"-"`
	StatusS string           `json:"status"`
	Detail  string           `json:"detail,omitempty"`
	Hint    string           `json:"hint,omitempty"`
}

// agentDoctor accumulates checks while probing each subsystem.
type agentDoctor struct {
	app    *app
	checks []agentCheck
}

func (d *agentDoctor) add(section, name string, status agentCheckStatus, detail, hint string) {
	d.checks = append(d.checks, agentCheck{
		Section: section, Name: name, Status: status, StatusS: status.String(), Detail: detail, Hint: hint,
	})
}

func (a *app) doctorE(cmd *cobra.Command, jsonFlag bool) error {
	d := &agentDoctor{app: a}
	ctx := cmd.Context()

	d.checkPaths()
	baseURL, token := d.checkIdentity(ctx)
	d.checkClockSkew(token)
	d.checkReachability(ctx, baseURL, token)

	if jsonOrPretty(cmd, jsonFlag) {
		return d.renderJSON()
	}
	return d.render()
}

// checkPaths verifies the XDG dirs the agent stores identity/state under exist
// and are private (impl/4.2 §1). A missing dir is only a warning — they are
// created lazily on first use — but group/other-readable perms on the secret
// dirs are a real leak.
func (d *agentDoctor) checkPaths() {
	const section = "Paths"
	for _, p := range []struct {
		name   string
		getter func() (string, error)
		secret bool
	}{
		{"config", config.ConfigDir, true},
		{"state", config.StateDir, true},
		{"cache", config.CacheDir, false},
	} {
		dir, err := p.getter()
		if err != nil {
			d.add(section, p.name, agentWarn, "cannot resolve: "+err.Error(), "")
			continue
		}
		info, statErr := os.Stat(dir)
		switch {
		case statErr != nil && os.IsNotExist(statErr):
			d.add(section, p.name, agentPass, dir+" (created on first use)", "")
		case statErr != nil:
			d.add(section, p.name, agentWarn, fmt.Sprintf("cannot stat %s: %v", dir, statErr), "")
		default:
			d.add(section, p.name, agentPass, dir, "")
			if p.secret {
				if perm := info.Mode().Perm(); perm&0o077 != 0 {
					d.add(section, p.name+" perms", agentWarn,
						fmt.Sprintf("%s is %#o (group/other access)", dir, perm), "chmod 700 "+dir)
				}
			}
		}
	}
}

// checkIdentity resolves the active identity and its token state STRICTLY
// READ-ONLY (UX-1): it never creates a directory, generates a key, or mints/
// persists a token — it reports each missing piece as a warning with the
// command that creates it. It inspects the XDG store for the active context;
// with no context there is nothing to inspect and it reports the onboarding
// remediation. It returns the resolved base URL and an already-cached usable
// token ("" if none), which the reachability and skew checks reuse.
func (d *agentDoctor) checkIdentity(ctx context.Context) (baseURL, token string) {
	const section = "Identity"
	st := clictx.ActiveV2(ctx)
	if st == nil {
		d.add(section, "session", agentWarn, "no active context",
			"run `jentic register --url <install URL>` to onboard, or `jentic context use <name>`")
		return "", ""
	}
	return d.checkContextIdentity(st)
}

// checkContextIdentity is the V2-context arm of checkIdentity: it inspects the
// XDG store for the active (identity, environment) pair — registration state
// from config.yaml, credential files from the state dir — reporting each
// missing piece read-only, exactly like the legacy arm.
func (d *agentDoctor) checkContextIdentity(st *clictx.ActiveState) (baseURL, token string) {
	const section = "Identity"
	pair := fmt.Sprintf("identity %q in environment %q", st.IdentityName, st.EnvironmentName)

	if st.InjectedBearerToken != "" {
		d.add(section, "session", agentPass, "file-less session ($JENTIC_BEARER_TOKEN)", "")
		return st.BaseURL, st.InjectedBearerToken
	}
	if st.BaseURL == "" {
		d.add(section, "session", agentWarn,
			fmt.Sprintf("environment %q has no base_url", st.EnvironmentName),
			"set it with `jentic env add`")
		return "", ""
	}

	ref := auth.IdentityRef{Identity: st.IdentityName, Environment: st.EnvironmentName}
	if key, err := auth.ReadAPIKey(ref); err == nil && key != "" {
		d.add(section, "session", agentPass, pair+" resolved (API key)", "")
		return st.BaseURL, key
	}

	cfg, err := config.Load()
	if err != nil {
		d.add(section, "session", agentWarn, "cannot read config: "+err.Error(), "")
		return st.BaseURL, ""
	}
	reg, registered := cfg.Identities[st.IdentityName].Environments[st.EnvironmentName]
	switch {
	case !registered || reg.ClientID == "":
		d.add(section, "session", agentWarn, pair+" is not registered",
			"run `jentic identity register`")
		return st.BaseURL, ""
	case reg.Status != "approved":
		d.add(section, "session", agentWarn,
			fmt.Sprintf("%s is registered but %s", pair, valueOr(reg.Status, "pending")),
			"wait for an operator to approve it")
	}

	tokens, err := auth.ReadTokens(ref)
	if err != nil || tokens == nil || tokens.AccessToken == "" || time.Now().After(tokens.ExpiresAt) {
		d.add(section, "session", agentWarn, "registered, but no fresh token is cached",
			"doctor never mints; run any authenticated command (e.g. `jentic search`) to obtain one")
		return st.BaseURL, ""
	}
	d.add(section, "session", agentPass, pair+" resolved, cached token usable", "")
	return st.BaseURL, tokens.AccessToken
}

// checkClockSkew surfaces local clock drift relative to the token's issue time
// (F8-9). The RFC 7523 exchange signs an assertion stamped with the local clock;
// if this host's clock is far from real time the server rejects the token as
// not-yet-valid/expired. We read the token's own `iat` claim (no extra network
// round-trip) and compare to now. It is skipped when there is no bearer token
// (API-key identities carry no iat).
func (d *agentDoctor) checkClockSkew(token string) {
	const section = "Clock"
	if token == "" {
		return
	}
	iat, ok := jwtIssuedAt(token)
	if !ok {
		return // opaque/API-key token — nothing to compare
	}
	skew := time.Since(iat)
	if skew < 0 {
		skew = -skew
	}
	// The auth layer clamps TTL/skew at 270s; flag well before that so the
	// operator fixes NTP before tokens start bouncing.
	const skewWarn = 60 * time.Second
	if skew > skewWarn {
		d.add(section, "skew", agentWarn,
			fmt.Sprintf("local clock is ~%s from the token issue time", skew.Round(time.Second)),
			"sync this host's clock (enable NTP); large skew makes the server reject freshly-minted tokens")
		return
	}
	d.add(section, "skew", agentPass, fmt.Sprintf("~%s", skew.Round(time.Second)), "")
}

// checkReachability confirms the control plane answers and reports the agent's
// identity as the server sees it. Read-only: it uses the already-obtained token,
// never minting. No token → a warning (can't probe), never a hard fail.
func (d *agentDoctor) checkReachability(ctx context.Context, baseURL, token string) {
	const section = "Control plane"
	if baseURL == "" || token == "" {
		d.add(section, "reachability", agentWarn, "skipped (no usable token)", "resolve your identity first")
		return
	}
	me, err := accessclient.New(baseURL).Me(ctx, token)
	if err != nil {
		d.add(section, "reachability", agentFail, baseURL+": "+err.Error(),
			"check the base URL and that the control plane is running")
		return
	}
	d.add(section, "reachability", agentPass, baseURL, "")
	scopes := "none"
	if len(me.Scopes) > 0 {
		scopes = strings.Join(me.Scopes, ", ")
	}
	d.add(section, "identity", agentPass, fmt.Sprintf("%s (status %s; scopes: %s)", me.ID, me.Status, scopes), "")
}

// jwtIssuedAt best-effort extracts the `iat` claim (seconds since epoch) from a
// JWT bearer without verifying the signature — doctor only needs the timestamp to
// estimate clock skew, not to trust the token. Returns ok=false for a non-JWT
// (opaque/API-key) token or a payload with no numeric iat.
func jwtIssuedAt(token string) (time.Time, bool) {
	parts := strings.Split(token, ".")
	if len(parts) != 3 {
		return time.Time{}, false
	}
	payload, err := base64.RawURLEncoding.DecodeString(parts[1])
	if err != nil {
		return time.Time{}, false
	}
	var claims struct {
		IssuedAt int64 `json:"iat"`
	}
	if err := json.Unmarshal(payload, &claims); err != nil || claims.IssuedAt == 0 {
		return time.Time{}, false
	}
	return time.Unix(claims.IssuedAt, 0), true
}

// render prints the grouped report to stdout and returns a non-nil error when any
// check failed, so the CLI exits non-zero (warnings keep a zero exit — CI-safe).
func (d *agentDoctor) render() error {
	var b strings.Builder
	section := ""
	for _, c := range d.checks {
		if c.Section != section {
			if section != "" {
				b.WriteString("\n")
			}
			b.WriteString(theme.Heading.Render(c.Section) + "\n")
			section = c.Section
		}
		b.WriteString(agentDotFor(c.Status) + " " + theme.Field(c.Name, c.Detail) + "\n")
		if c.Hint != "" && c.Status != agentPass {
			b.WriteString("  " + theme.Dim.Render("↳ "+c.Hint) + "\n")
		}
	}
	b.WriteString("\n" + d.summary() + "\n")
	fmt.Fprint(d.app.Out, b.String())
	if f := d.failed(); f > 0 {
		return fmt.Errorf("doctor: %d check(s) failed", f)
	}
	return nil
}

func (d *agentDoctor) renderJSON() error {
	var out struct {
		Checks  []agentCheck `json:"checks"`
		Summary struct {
			Passed   int `json:"passed"`
			Warnings int `json:"warnings"`
			Failed   int `json:"failed"`
		} `json:"summary"`
	}
	out.Checks = d.checks
	out.Summary.Passed, out.Summary.Warnings, out.Summary.Failed = d.counts()
	if err := writeJSON(d.app.Out, out); err != nil {
		return err
	}
	if out.Summary.Failed > 0 {
		return fmt.Errorf("doctor: %d check(s) failed", out.Summary.Failed)
	}
	return nil
}

func (d *agentDoctor) counts() (pass, warn, fail int) {
	for _, c := range d.checks {
		switch c.Status {
		case agentPass:
			pass++
		case agentWarn:
			warn++
		default:
			fail++
		}
	}
	return pass, warn, fail
}

func (d *agentDoctor) failed() int {
	_, _, f := d.counts()
	return f
}

func (d *agentDoctor) summary() string {
	p, w, f := d.counts()
	parts := []string{theme.Successf("%d passed", p)}
	if w > 0 {
		parts = append(parts, theme.Warnf("%d warnings", w))
	}
	if f > 0 {
		parts = append(parts, theme.Error.Render(fmt.Sprintf("%d failed", f)))
	}
	return strings.Join(parts, theme.Dim.Render(" · "))
}

func agentDotFor(s agentCheckStatus) string {
	switch s {
	case agentPass:
		return cmdcore.DotOK()
	case agentWarn:
		return cmdcore.DotWarn()
	default:
		return cmdcore.DotFail()
	}
}
