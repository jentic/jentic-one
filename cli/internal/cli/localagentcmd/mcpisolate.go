package localagentcmd

// mcpisolate.go is `jentic setup`'s optional MCP isolation step (local-MCP
// §3.7.5 rung 2): after the MCP entries are written, offer to move each one
// behind a boundary so the desktop-user side holds only a disposable spawn
// line, never the signing key. The automated rung is the SUDO-SHIM entry
// (macOS/Linux only): a dedicated per-runtime service user
// (`_jentic-<runtime>` — `_` prefix, system uid, no login shell, 0700 state
// dir) owns the context's key material; the entry becomes
// `sudo -n -H -u <user> /abs/jentic mcp --context <name>`, matched by an
// argv-pinned NOPASSWD sudoers line.
//
// The CONTAINER rung (§3.7.5's ecosystem-normal variant) is deliberately NOT
// automated here: no published CLI image exists yet (2-E4 owns publishing
// one), and rewriting a working entry to a container spawn would also need
// volume provisioning plus a smoke-spawn to honour the invariant that a
// failed isolation keeps the working non-isolated entry. Docker-equipped
// operators are pointed at the documented manual recipe instead
// (docs/security/mcp-same-host-hardening.md, Recipe 3).
//
// The whole step is BEST-EFFORT and consent-gated: it never runs sudo
// unattended (interactive sessions only), and any failure leaves the working
// non-isolated entry in place — it must never block the setup the operator
// came for.

import (
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"

	"github.com/charmbracelet/huh"
	"github.com/jentic/jentic-one/cli/client/auth"
	sdkconfig "github.com/jentic/jentic-one/cli/client/config"
	"github.com/jentic/jentic-one/cli/internal/cli/clictx"
	"github.com/jentic/jentic-one/cli/internal/cli/prompt"
	"github.com/jentic/jentic-one/cli/internal/localagent"
	"github.com/jentic/jentic-one/cli/internal/mcpcfg"
	"github.com/jentic/jentic-one/cli/internal/theme"
)

// containerRecipePointer is the manual container-isolation pointer printed
// where Docker is present. It names the shipped doc so the operator can build
// the rung by hand; nothing is rewritten automatically (see the file comment).
const containerRecipePointer = "Prefer a container? The manual recipe is docs/security/mcp-same-host-hardening.md (Recipe 3)."

// offerMCPIsolation runs the optional isolation step over the entries that
// were just written. interactive gates the whole step: a non-interactive run
// (--yes, pipes, agent sessions) must never trigger a sudo prompt, so it only
// prints the pointer.
func (a *Cmd) offerMCPIsolation(ctx context.Context, outcomes []mcpcfg.Outcome, interactive bool) {
	if len(outcomes) == 0 {
		return
	}
	if runtime.GOOS != "darwin" && runtime.GOOS != "linux" {
		return // sudo-shim is macOS/Linux only; container-on-Windows is a docs recipe
	}
	if !interactive {
		fmt.Fprintln(a.Out, theme.Dim.Render(
			"Skipping MCP isolation (non-interactive). Re-run `jentic setup` interactively to isolate the entries."))
		return
	}

	ctxName := activeContextName()
	binPath, err := mcpcfg.StableBinaryPath()
	if ctxName == "" || err != nil {
		return // registerMCPEntries already reported the gap
	}

	fmt.Fprintln(a.Out)
	fmt.Fprintln(a.Out, theme.Step.Render("Isolate your MCP entries"))
	fmt.Fprintln(a.Out, theme.Dim.Render(
		"Right now the runtime spawns `jentic mcp` as you, so any same-user process can read"))
	fmt.Fprintln(a.Out, theme.Dim.Render(
		"the context's signing key. Isolation moves the key and the server behind a boundary;"))
	fmt.Fprintln(a.Out, theme.Dim.Render(
		"your side keeps only the spawn line."))
	if _, err := exec.LookPath("docker"); err == nil {
		fmt.Fprintln(a.Out, theme.Dim.Render(containerRecipePointer))
	}

	// One shared context can only be MOVED once: the operator-side key
	// removal is offered AFTER the last runtime, never inside the loop —
	// removing it mid-loop would strand every later runtime's export with a
	// keyless service account while still printing success.
	isolatedAny := false
	for _, out := range outcomes {
		isolated, err := a.isolateSudoShim(ctx, out.Runtime, binPath, ctxName)
		if err != nil {
			if errors.Is(err, huh.ErrUserAborted) {
				fmt.Fprintln(a.Out, theme.Dim.Render("MCP isolation cancelled."))
				break
			}
			// Best-effort: the non-isolated entry keeps working.
			fmt.Fprintln(a.Out, theme.Warnf("could not isolate the %s entry (its non-isolated entry still works): %v", out.Runtime, err))
			continue
		}
		isolatedAny = isolatedAny || isolated
	}

	// 5. Complete the move: with the key now living under the service
	//    user(s), offer to remove the operator-side copy so the desktop side
	//    holds no long-lived key material. Separate consent — it also cuts
	//    the OPERATOR's own CLI off from this context, which is the point but
	//    must be a deliberate choice.
	if isolatedAny {
		a.offerOperatorKeyRemoval(ctx, ctxName)
	}
}

// confirmIsolation runs one consent prompt; declining is a clean no.
func (a *Cmd) confirmIsolation(_ mcpcfg.Runtime, title, description string) (bool, error) {
	accepted := false
	if err := prompt.RunConfirm(huh.NewConfirm().
		Title(title).
		Description(description).
		Affirmative("Yes, isolate it").
		Negative("Not now").
		Value(&accepted)); err != nil {
		return false, err
	}
	return accepted, nil
}

// isolateSudoShim provisions the per-runtime service account and rewrites the
// entry in sudo-shim form, returning whether the entry was actually isolated
// (false on a clean decline). Every privileged command runs only after the
// explicit consent above, with stdio wired to the terminal so the sudo
// password prompt is visible — never unattended. Ordering is load-bearing:
// home creation (root-side, 0700, no operator grant) → context export via
// `sudo install` (the operator never needs access to the home) → the sudoers
// rule → the entry rewrite, so a failure at any step leaves the working
// non-isolated entry in place.
func (a *Cmd) isolateSudoShim(ctx context.Context, rt mcpcfg.Runtime, binPath, ctxName string) (bool, error) {
	serviceUser := localagent.ServiceUserName(string(rt))
	homeDir := localagent.ServiceHomeDir(serviceUser)
	if err := localagent.ValidateAccount(serviceUser, homeDir); err != nil {
		return false, err
	}
	if err := localagent.ValidateMcpSudoersInputs(binPath, ctxName); err != nil {
		return false, err
	}

	accepted, err := a.confirmIsolation(rt,
		fmt.Sprintf("Isolate the %s entry behind service user %s? (requires sudo)", rt, serviceUser),
		"Creates a no-login system account owning the context's key material, adds one NOPASSWD "+
			"sudoers line pinned to exactly `jentic mcp --context "+ctxName+"`, and rewrites the entry as a sudo shim.")
	if err != nil {
		return false, err
	}
	if !accepted {
		fmt.Fprintln(a.Out, theme.Dimf("Keeping the non-isolated %s entry.", rt))
		return false, nil
	}

	// Render the context material into an operator-private staging dir BEFORE
	// any privileged step: a keyless/unresolvable context fails here, loudly,
	// with nothing created yet.
	mat, skip, err := a.buildExportMaterial(ctx)
	if err != nil {
		return false, fmt.Errorf("hand the context to the service account: %w", err)
	}
	if skip {
		return false, errors.New("no exportable context material (register a context, then re-run `jentic setup`)")
	}
	staging, err := a.renderExportStaging(mat)
	if staging != "" {
		defer os.RemoveAll(staging)
	}
	if err != nil {
		return false, fmt.Errorf("stage the context material: %w", err)
	}

	// 1. Service account + 0700 state dir (idempotent-ish: an existing
	//    account is reused only when its live home is the managed one).
	exists := localagent.UserExists(ctx, serviceUser)
	if exists {
		if err := localagent.VerifyManagedHome(serviceUser, homeDir); err != nil {
			return false, fmt.Errorf("refusing to reuse existing account %q: %w", serviceUser, err)
		}
		fmt.Fprintln(a.Out, theme.Dimf("Service account %q already exists — reusing it.", serviceUser))
	} else {
		fmt.Fprintln(a.Out, theme.Infof("Creating service account %q (state dir %s) ...", serviceUser, homeDir))
	}

	// 2+3. The ordered privileged plan: home creation (when needed), the
	//    root-side export installs, then the argv-pinned NOPASSWD line (one
	//    source user → one target user → exactly the pinned command, on the
	//    same visudo-validated drop-in plumbing as the launch rule;
	//    RemoveSudoersCmd — wired into `jentic reset` — reverses it).
	rule := localagent.McpSudoersRule(currentOperator(), serviceUser, binPath, ctxName)
	for i, step := range sudoShimPrivilegedSteps(!exists, serviceUser, homeDir, staging, mat, rule) {
		c := step.Cmd
		c.Stdout, c.Stderr = a.Out, a.Err
		if err := c.Run(); err != nil {
			return false, fmt.Errorf("%s: %w", step.What, err)
		}
		// Verify the account actually exists right after the create step:
		// sysadminctl can refuse the add yet still exit 0.
		if !exists && i == 0 && !localagent.UserExists(ctx, serviceUser) {
			return false, fmt.Errorf("service account %q was not created (the account tool reported success but the account does not exist)", serviceUser)
		}
	}

	// 4. Rewrite the runtime's entry in sudo-shim form.
	if err := a.applyIsolatedEntry(ctx, rt, mcpcfg.SudoShimEntry(serviceUser, binPath, ctxName), "sudo-shim"); err != nil {
		return false, err
	}
	return true, nil
}

// sudoShimPrivilegedSteps assembles the ordered privileged plan for one
// runtime's sudo-shim isolation: account + home creation (when the account is
// new) → context export via root-side `sudo install` → the sudoers rule LAST
// (the NOPASSWD line must never exist before the material it grants access to
// is in place). Enumerated so the ordering is assertable in tests without
// sudo.
func sudoShimPrivilegedSteps(createAccount bool, serviceUser, homeDir, staging string, mat *exportMaterial, rule string) []localagent.AccountStep {
	var steps []localagent.AccountStep
	if createAccount {
		steps = append(steps, localagent.CreateServiceAccountCmds(serviceUser, homeDir)...)
	}
	steps = append(steps, localagent.ExportInstallCmds(serviceUser, homeDir, staging, mat.relDirs, mat.relFiles())...)
	steps = append(steps, localagent.AccountStep{
		What: "install the sudoers rule",
		Cmd:  localagent.InstallSudoersRuleCmd(rule),
	})
	return steps
}

// renderExportStaging writes the export material into a fresh operator-private
// (0700) temp dir laid out exactly like the target home's XDG tree, returning
// the staging root. The caller removes it once the root-side install ran.
func (a *Cmd) renderExportStaging(mat *exportMaterial) (string, error) {
	staging, err := os.MkdirTemp("", "jentic-mcp-export-*")
	if err != nil {
		return "", err
	}
	if err := os.Chmod(staging, 0o700); err != nil { //nolint:gosec // 0700 not 0600: a directory needs owner-execute to be traversable; no group/world bits.
		return staging, err
	}
	for _, rel := range mat.relDirs {
		if err := os.MkdirAll(filepath.Join(staging, rel), 0o700); err != nil {
			return staging, err
		}
	}
	if err := mat.renderInto(staging); err != nil {
		return staging, err
	}
	return staging, nil
}

// applyIsolatedEntry rewrites one runtime's MCP entry with the isolated
// variant and reports the outcome.
func (a *Cmd) applyIsolatedEntry(ctx context.Context, rt mcpcfg.Runtime, entry mcpcfg.Entry, kind string) error {
	env, err := a.mcpEnv()
	if err != nil {
		return err
	}
	out, err := a.applyMCPEntry(ctx, rt, env, entry)
	if err != nil {
		return err
	}
	fmt.Fprintln(a.Out, "  "+theme.Successf("%-16s entry rewritten in %s form (%s)", rt, kind, prettyPath(out.Path)))
	return nil
}

// offerOperatorKeyRemoval offers to delete the OPERATOR-side key/token files
// for the active context, completing the move behind the service user(s). It
// runs ONCE, after the LAST runtime's isolation — with one shared context a
// true move is only safe once, so removing the operator copy inside the
// per-runtime loop would strand every later runtime. It is deliberately its
// own consent: after removal, the operator's own CLI can no longer act as
// this context (that asymmetry is the security property, but it must never
// happen silently). Declining keeps a copy on both sides — noted, since a
// desktop-side key is exactly what isolation exists to eliminate.
func (a *Cmd) offerOperatorKeyRemoval(ctx context.Context, ctxName string) {
	st := clictx.ActiveContext(ctx)
	if st == nil || st.InjectedBearerToken != "" {
		return
	}
	remove := false
	if err := prompt.RunConfirm(huh.NewConfirm().
		Title("Remove your own copy of the context's key material?").
		Description("Completes the move: only the service user holds the key. Your own `jentic` commands " +
			"then need a different context (register one for yourself).").
		Affirmative("Yes, remove my copy").
		Negative("Keep both copies").
		Value(&remove)); err != nil || !remove {
		if !remove && err == nil {
			fmt.Fprintln(a.Out, theme.Dim.Render(
				"Keeping your copy — note the key is still readable by same-user processes until you remove it."))
		}
		return
	}

	ref := auth.IdentityRef{Identity: st.IdentityName, Environment: st.EnvironmentName}
	var paths []string
	if key, err := auth.KeyPathForImport(ref); err == nil {
		paths = append(paths, key)
	}
	if stem, err := ref.Stem(); err == nil {
		if stateDir, serr := sdkconfig.StateDir(); serr == nil {
			paths = append(paths, filepath.Join(stateDir, stem+"_tokens.json"), filepath.Join(stateDir, stem+".apikey"))
		}
	}
	for _, p := range paths {
		if err := os.Remove(p); err != nil && !os.IsNotExist(err) {
			fmt.Fprintln(a.Out, theme.Warnf("could not remove %s: %v", prettyPath(p), err))
		}
	}
	fmt.Fprintln(a.Out, theme.Dimf("Key material for context %q now lives only under the service user.", ctxName))
}
