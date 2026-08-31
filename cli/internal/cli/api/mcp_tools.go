package api

// mcp_tools.go holds the PR 1-A tool handlers. get_started is the pre-auth
// diagnosis: it walks the same identity ladder the skill's step 1 teaches
// (context → credential/registration → approval → instance reachability) and
// returns the matching operator instruction VERBATIM from the skill/CLI
// wording, so the model relays exactly what the CLI would have said. whoami
// is the existing identity surface (GET /me, agent variant) as a tool.

import (
	"context"
	"encoding/json"
	"fmt"
	"net"
	"net/url"
	"strings"

	"github.com/modelcontextprotocol/go-sdk/mcp"

	"github.com/jentic/jentic-one/cli/client/auth"
	"github.com/jentic/jentic-one/cli/client/config"
	"github.com/jentic/jentic-one/cli/internal/cli/clictx"
	"github.com/jentic/jentic-one/cli/internal/cli/cmdcore"
	"github.com/jentic/jentic-one/cli/internal/cli/ux"
)

// setupState is the closed enum get_started reports. Closed like the CLI's
// error_code taxonomy so a model can branch on it instead of parsing prose.
const (
	setupNoConfig            = "no_config"
	setupNotRegistered       = "not_registered"
	setupPendingApproval     = "pending_approval"
	setupInstanceUnreachable = "instance_unreachable"
	setupReady               = "ready"
)

// setupProbe is everything diagnoseSetup needs, gathered up front so the
// branch selection itself is a pure function (unit-testable without disk or
// network).
type setupProbe struct {
	hasContext  bool
	baseURL     string
	identity    string
	environment string
	// fileless: $JENTIC_BASE_URL/$JENTIC_BEARER_TOKEN session — the injected
	// token IS the credential, registration state does not apply.
	fileless bool
	// hasAPIKey: a jak_* credential is stored for the pair — like fileless,
	// registration state does not apply.
	hasAPIKey bool
	// registered/regStatus: the (identity, environment) registration from
	// config.yaml (client_id present / status).
	registered bool
	regStatus  string
	// configErr is a config.yaml READ failure (EACCES, corruption) hit while
	// resolving the registration state: registration is then UNKNOWN, not
	// absent — the diagnosis must surface the cause instead of prescribing a
	// re-register that cannot help while the file is unreadable.
	configErr error
	// probeErr is the GET /instance result; only probed when the identity
	// ladder passes (probed=true) — an unregistered machine reports its
	// registration gap without a pointless dial.
	probed   bool
	probeErr error
}

// identityResolves reports whether the credential side of the ladder is
// satisfied: a file-less token, a stored API key, or an approved registration.
func (p setupProbe) identityResolves() bool {
	if p.fileless || p.hasAPIKey {
		return true
	}
	return p.registered && p.regStatus == "approved"
}

// setupDiagnosis is one diagnosed branch: the closed state, a one-line
// summary, and the operator instruction to relay verbatim.
type setupDiagnosis struct {
	State       string
	Summary     string
	Instruction string
}

// Operator instructions. Wording mirrors the sources the model already knows:
// the V2 no-config error (client/config/loader.go), the skill's step-1
// identity branches, and the skill's stopped-instance pitfall (which names
// `jenticctl start` verbatim for same-host installs). Registration/approval
// always block on a human; the instance is never auto-started (decided,
// master §3.3: lifecycle belongs to jenticctl).
const (
	instructionNoConfig = "No configuration found. Ask your operator to run " +
		"`jentic register --url <control-plane URL>` on this machine to onboard it, or to set " +
		"JENTIC_BASE_URL and JENTIC_BEARER_TOKEN in this server's environment. That step blocks " +
		"on a human and cannot be completed by an autonomous agent."
	instructionNotRegistered = "Stop and ask your operator to run `jentic register --url <install URL>` " +
		"and approve the agent — that step blocks on a human and cannot be completed by an " +
		"autonomous agent. (For a local install the URL must be `http://127.0.0.1:8000`, not " +
		"`localhost` — the token audience is matched exactly.) Once approved, a token is minted " +
		"and reused automatically."
	instructionLocalUnreachable = "Your Jentic One instance appears to be stopped. Ask your operator to " +
		"restart it with `jenticctl start` (then `jenticctl status` to confirm), and retry. " +
		"This server never starts or stops the instance."
	instructionReady = "You're set up. Call the `whoami` tool to see your identity, status, scopes, " +
		"and toolkit bindings. Request access before executing anything new; discovery and " +
		"execution follow the search → inspect → execute flow."
)

// diagnoseSetup is the pure branch selection. Ladder order mirrors the skill's
// step 1: config/context first, then registration, then approval, then — only
// for a resolvable identity — instance reachability.
func diagnoseSetup(p setupProbe) setupDiagnosis {
	pair := fmt.Sprintf("identity %q in environment %q", p.identity, p.environment)
	switch {
	case !p.hasContext:
		return setupDiagnosis{
			State:       setupNoConfig,
			Summary:     "no Jentic configuration or active context on this machine",
			Instruction: instructionNoConfig,
		}
	case p.baseURL == "":
		return setupDiagnosis{
			State:   setupNoConfig,
			Summary: fmt.Sprintf("environment %q has no base_url", p.environment),
			Instruction: fmt.Sprintf("Environment %q has no base_url. Ask your operator to set it "+
				"(`jentic env add`, or re-run `jentic register --url <control-plane URL>`).", p.environment),
		}
	case !p.fileless && !p.hasAPIKey && p.configErr != nil:
		return setupDiagnosis{
			State:   setupNotRegistered,
			Summary: fmt.Sprintf("the registration state for %s could not be read: %v", pair, p.configErr),
			Instruction: fmt.Sprintf("The Jentic configuration could not be read (%v). Ask your operator to "+
				"fix the configuration file (permissions or corruption) under the XDG config directory "+
				"(~/.config/jentic), then retry get_started. Re-registering will not help while the file "+
				"is unreadable.", p.configErr),
		}
	case !p.fileless && !p.hasAPIKey && !p.registered:
		return setupDiagnosis{
			State:       setupNotRegistered,
			Summary:     pair + " is not registered",
			Instruction: instructionNotRegistered,
		}
	case !p.fileless && !p.hasAPIKey && p.regStatus != "approved":
		return setupDiagnosis{
			State:   setupPendingApproval,
			Summary: fmt.Sprintf("%s is registered but %s", pair, cmdcore.ValueOr(p.regStatus, "pending")),
			Instruction: "The agent is registered and awaiting operator approval — that step blocks on a " +
				"human and cannot be completed by an autonomous agent. Ask your operator to approve it, " +
				"then retry; once approved, a token is minted and reused automatically.",
		}
	case p.probed && p.probeErr != nil:
		if loopbackURL(p.baseURL) {
			return setupDiagnosis{
				State:       setupInstanceUnreachable,
				Summary:     fmt.Sprintf("the instance at %s did not answer: %v", p.baseURL, p.probeErr),
				Instruction: instructionLocalUnreachable,
			}
		}
		return setupDiagnosis{
			State:   setupInstanceUnreachable,
			Summary: fmt.Sprintf("the instance at %s did not answer: %v", p.baseURL, p.probeErr),
			Instruction: fmt.Sprintf("The Jentic One instance at %s is unreachable from this machine. "+
				"Ask your operator to check the deployment (their runbook owns the instance lifecycle) "+
				"and your network path to it, then retry. This server never starts or stops the instance.", p.baseURL),
		}
	default:
		return setupDiagnosis{
			State:       setupReady,
			Summary:     pair + " resolves and the instance is reachable",
			Instruction: instructionReady,
		}
	}
}

// gatherSetup collects the probe inputs: the interceptor-resolved state, the
// XDG credential/registration files (read-only, exactly like `jentic doctor`),
// and — only when the identity ladder passes — a forced GET /instance.
func (s *mcpServer) gatherSetup(ctx context.Context) setupProbe {
	var p setupProbe
	st := clictx.ActiveContext(ctx)
	if st == nil {
		return p
	}
	p.hasContext = true
	p.baseURL = st.BaseURL
	p.identity = st.IdentityName
	p.environment = st.EnvironmentName
	p.fileless = st.InjectedBearerToken != ""
	if p.baseURL == "" {
		return p
	}
	if !p.fileless && !p.hasAPIKey {
		ref := auth.IdentityRef{Identity: st.IdentityName, Environment: st.EnvironmentName}
		if key, err := auth.ReadAPIKey(ref); err == nil && key != "" {
			p.hasAPIKey = true
		}
	}
	if !p.fileless && !p.hasAPIKey {
		// A read/parse error means the registration state is UNKNOWN: carry
		// the cause into the probe so the diagnosis can surface it instead of
		// misreporting an EACCES/corrupt config.yaml as "not registered" (and
		// prescribing a re-register that cannot help while the file is
		// unreadable). Note the interceptor may still have resolved a context
		// from the env override, so this branch is reachable.
		if cfg, err := config.Load(); err != nil {
			p.configErr = err
		} else {
			reg, ok := cfg.Identities[st.IdentityName].Environments[st.EnvironmentName]
			p.registered = ok && reg.ClientID != ""
			p.regStatus = reg.Status
		}
	}
	if p.identityResolves() {
		p.probed = true
		p.probeErr = s.instances.probe(ctx)
	}
	return p
}

func (s *mcpServer) handleGetStarted(ctx context.Context, req *mcp.CallToolRequest) (*mcp.CallToolResult, error) {
	s.noteClient(req.ClientInfo())
	cctx, cancel := s.callContext(ctx)
	defer cancel()

	probe := s.gatherSetup(cctx)
	d := diagnoseSetup(probe)
	s.logger.Info("get_started", "state", d.State)

	payload := map[string]any{
		"schema_version": mcpSchemaVersion,
		"state":          d.State,
		"summary":        d.Summary,
		"instruction":    d.Instruction,
	}
	if probe.hasContext {
		payload["context"] = map[string]any{
			"identity":    probe.identity,
			"environment": probe.environment,
			"base_url":    probe.baseURL,
		}
	}
	return s.result(cctx, payload), nil
}

func (s *mcpServer) handleWhoami(ctx context.Context, req *mcp.CallToolRequest) (*mcp.CallToolResult, error) {
	s.noteClient(req.ClientInfo())
	cctx, cancel := s.callContext(ctx)
	defer cancel()

	me, err := s.app.getMe(cctx)
	if err != nil {
		s.logger.Warn("whoami failed", "error", err)
		return s.softError(cctx, err), nil
	}
	// Envelope passthrough: the same GET /me agent object `jentic access
	// whoami --json` prints, re-projected to a map so the instance stamp can
	// join it as a top-level sibling.
	raw, err := json.Marshal(me)
	if err != nil {
		return s.softError(cctx, &ux.CodedError{Code: ux.CodeInternalError, Msg: "encode /me response: " + err.Error()}), nil
	}
	var payload map[string]any
	if err := json.Unmarshal(raw, &payload); err != nil {
		return s.softError(cctx, &ux.CodedError{Code: ux.CodeInternalError, Msg: "decode /me response: " + err.Error()}), nil
	}
	payload["schema_version"] = mcpSchemaVersion
	return s.result(cctx, payload), nil
}

// loopbackURL reports whether the control-plane URL targets this machine —
// the case where the skill's stopped-instance pitfall (and its `jenticctl
// start` remediation) applies. Remote hosts get the operator-runbook wording.
func loopbackURL(rawURL string) bool {
	u, err := url.Parse(rawURL)
	if err != nil {
		return false
	}
	host := u.Hostname()
	if strings.EqualFold(host, "localhost") {
		return true
	}
	if ip := net.ParseIP(host); ip != nil {
		return ip.IsLoopback()
	}
	return false
}
