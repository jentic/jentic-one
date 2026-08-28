package localagentcmd

// mcpisolate.go is `jentic setup`'s optional MCP isolation step (local-MCP
// §3.7.5 rung 2): after the MCP entries are written, offer to move each one
// behind a boundary so the desktop-user side holds only a disposable spawn
// line, never the signing key. Two variants, per the hardening ladder:
//
//   - CONTAINER entry (offered FIRST where Docker Desktop is present — the
//     MCP ecosystem's mainstream isolation pattern): the entry becomes a
//     hardened `docker run -i --rm` with a named state volume; key material
//     lives in the volume.
//   - SUDO-SHIM entry (docker-less machines; macOS/Linux only): a dedicated
//     per-runtime service user (`_jentic-<runtime>` — `_` prefix, system uid,
//     no login shell, 0700 state dir) owns the context's key material; the
//     entry becomes `sudo -n -u <user> /abs/jentic mcp --context <name>`,
//     matched by an argv-pinned NOPASSWD sudoers line.
//
// The whole step is BEST-EFFORT and consent-gated: it never runs sudo
// unattended (interactive sessions only), and any failure leaves the working
// non-isolated entry in place — it must never block the setup the operator
// came for.

import (
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"time"

	"github.com/charmbracelet/huh"
	"github.com/jentic/jentic-one/cli/client/auth"
	sdkconfig "github.com/jentic/jentic-one/cli/client/config"
	"github.com/jentic/jentic-one/cli/internal/cli/clictx"
	"github.com/jentic/jentic-one/cli/internal/cli/prompt"
	"github.com/jentic/jentic-one/cli/internal/localagent"
	"github.com/jentic/jentic-one/cli/internal/mcpcfg"
	"github.com/jentic/jentic-one/cli/internal/theme"
)

// mcpContainerImageEnv overrides the container-entry image (a mirror, a
// pinned digest). The default image must already be present locally — the
// step never pulls.
const mcpContainerImageEnv = "JENTIC_MCP_CONTAINER_IMAGE"

// defaultMCPContainerImage is the CLI image the container entry runs. There
// is no published CLI image yet (2-E4's registry/release automation owns
// that), so the container variant is offered only when this image — or the
// env override — already exists locally.
const defaultMCPContainerImage = "ghcr.io/jentic/jentic-one-cli:latest"

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

	image, haveDocker := a.containerVariantAvailable(ctx)
	for _, out := range outcomes {
		if err := a.isolateOne(ctx, out.Runtime, binPath, ctxName, image, haveDocker); err != nil {
			if errors.Is(err, huh.ErrUserAborted) {
				fmt.Fprintln(a.Out, theme.Dim.Render("MCP isolation cancelled."))
				return
			}
			// Best-effort: the non-isolated entry keeps working.
			fmt.Fprintln(a.Out, theme.Warnf("could not isolate the %s entry (its non-isolated entry still works): %v", out.Runtime, err))
		}
	}
}

// containerVariantAvailable reports whether the container entry can be
// offered: Docker Desktop (a responsive docker CLI) plus the CLI image
// already present locally. The probe never pulls and never blocks long.
func (a *Cmd) containerVariantAvailable(ctx context.Context) (image string, ok bool) {
	image = os.Getenv(mcpContainerImageEnv)
	if image == "" {
		image = defaultMCPContainerImage
	}
	if _, err := exec.LookPath("docker"); err != nil {
		return image, false
	}
	probeCtx, cancel := context.WithTimeout(ctx, 3*time.Second)
	defer cancel()
	cmd := exec.CommandContext(probeCtx, "docker", "image", "inspect", image) //nolint:gosec // image is a fixed default or the operator's own env override.
	cmd.Stdout, cmd.Stderr = nil, nil
	return image, cmd.Run() == nil
}

// isolateOne offers and applies one runtime's isolation: the container
// variant first where available, else the sudo-shim.
func (a *Cmd) isolateOne(ctx context.Context, rt mcpcfg.Runtime, binPath, ctxName, image string, haveDocker bool) error {
	if haveDocker {
		accepted, err := a.confirmIsolation(rt,
			fmt.Sprintf("Isolate the %s entry in a container? (recommended)", rt),
			"Rewrites the entry as a hardened `docker run -i --rm` with a named state volume ("+
				mcpcfg.ContainerStateVolume(ctxName)+"). No sudo needed.")
		if err != nil {
			return err
		}
		if accepted {
			return a.applyIsolatedEntry(ctx, rt, mcpcfg.ContainerEntry(image, ctxName), "container")
		}
		// Declining the container rung falls through to the sudo-shim offer.
	}
	return a.isolateSudoShim(ctx, rt, binPath, ctxName)
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
// entry in sudo-shim form. Every privileged command runs only after the
// explicit consent above, with stdio wired to the terminal so the sudo
// password prompt is visible — never unattended.
func (a *Cmd) isolateSudoShim(ctx context.Context, rt mcpcfg.Runtime, binPath, ctxName string) error {
	serviceUser := localagent.ServiceUserName(string(rt))
	homeDir := localagent.ServiceHomeDir(serviceUser)
	if err := localagent.ValidateAccount(serviceUser, homeDir); err != nil {
		return err
	}
	if err := localagent.ValidateMcpSudoersInputs(binPath, ctxName); err != nil {
		return err
	}

	accepted, err := a.confirmIsolation(rt,
		fmt.Sprintf("Isolate the %s entry behind service user %s? (requires sudo)", rt, serviceUser),
		"Creates a no-login system account owning the context's key material, adds one NOPASSWD "+
			"sudoers line pinned to exactly `jentic mcp --context "+ctxName+"`, and rewrites the entry as a sudo shim.")
	if err != nil {
		return err
	}
	if !accepted {
		fmt.Fprintln(a.Out, theme.Dimf("Keeping the non-isolated %s entry.", rt))
		return nil
	}

	// 1. Service account + 0700 state dir (idempotent-ish: an existing
	//    account is reused only when its live home is the managed one).
	if localagent.UserExists(ctx, serviceUser) {
		if err := localagent.VerifyManagedHome(serviceUser, homeDir); err != nil {
			return fmt.Errorf("refusing to reuse existing account %q: %w", serviceUser, err)
		}
		fmt.Fprintln(a.Out, theme.Dimf("Service account %q already exists — reusing it.", serviceUser))
	} else {
		fmt.Fprintln(a.Out, theme.Infof("Creating service account %q (state dir %s) ...", serviceUser, homeDir))
		for _, step := range localagent.CreateServiceAccountCmds(serviceUser, homeDir) {
			c := step.Cmd
			c.Stdout, c.Stderr = a.Out, a.Err
			if err := c.Run(); err != nil {
				return fmt.Errorf("%s: %w", step.What, err)
			}
		}
		if !localagent.UserExists(ctx, serviceUser) {
			return fmt.Errorf("service account %q was not created (the account tool reported success but the account does not exist)", serviceUser)
		}
	}

	// 2. Move the context's key material under the service user. The export
	//    writes the minimal config + key + tokens into the service home and
	//    chowns them to the service uid; the operator-side removal below is
	//    what turns the copy into a MOVE.
	if err := a.exportContextMaterial(ctx, serviceUser, homeDir); err != nil {
		return fmt.Errorf("hand the context to the service account: %w", err)
	}
	// The state dir may have been re-created by the export as the operator;
	// re-pin ownership best-effort (the export already chowns .config/.local).
	pin := localagent.ChownToAgentCmd(serviceUser, homeDir)
	pin.Stdout, pin.Stderr = a.Out, io.Discard
	_ = pin.Run()

	// 3. The argv-pinned NOPASSWD line: one source user → one target user →
	//    exactly the pinned command. Uses the same visudo-validated drop-in
	//    plumbing as the launch rule; `jentic reset`'s RemoveSudoersCmd
	//    (anchored on the runas spec) reverses it.
	rule := localagent.McpSudoersRule(currentOperator(), serviceUser, binPath, ctxName)
	install := localagent.InstallSudoersRuleCmd(rule)
	install.Stdout, install.Stderr = a.Out, a.Err
	if err := install.Run(); err != nil {
		return fmt.Errorf("install the sudoers rule: %w", err)
	}

	// 4. Rewrite the runtime's entry in sudo-shim form.
	if err := a.applyIsolatedEntry(ctx, rt, mcpcfg.SudoShimEntry(serviceUser, binPath, ctxName), "sudo-shim"); err != nil {
		return err
	}

	// 5. Complete the move: with the key now living under the service user,
	//    offer to remove the operator-side copy so the desktop side holds no
	//    long-lived key material. Separate consent — it also cuts the
	//    OPERATOR's own CLI off from this context, which is the point but
	//    must be a deliberate choice.
	a.offerOperatorKeyRemoval(ctx, ctxName)
	return nil
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
// for the active context, completing the move behind the service user. It is
// deliberately its own consent: after removal, the operator's own CLI can no
// longer act as this context (that asymmetry is the security property, but it
// must never happen silently). Declining keeps a copy on both sides — noted,
// since a desktop-side key is exactly what isolation exists to eliminate.
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
