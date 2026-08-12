package cmdcore

// context_export.go hands the operator's ACTIVE V2 context to the shared agent
// account at launch time. The identity's home store stays the OPERATOR's XDG
// tree (registration/approval are operator ceremonies); what the agent gets is
// a fresh export of exactly the material its session needs — a minimal
// config.yaml naming one environment + identity + context, and the env-scoped
// credential files — written into the agent home's own XDG layout and chowned
// to the agent uid. Exporting per launch (not once at provisioning) means the
// agent always starts with the operator's current context and freshest
// credential state, and there is no second writable copy drifting between
// sessions.

import (
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"

	"gopkg.in/yaml.v3"

	"github.com/jentic/jentic-one/cli/client/auth"
	sdkconfig "github.com/jentic/jentic-one/cli/client/config"
	"github.com/jentic/jentic-one/cli/internal/cli/clictx"
	"github.com/jentic/jentic-one/cli/internal/config"
	"github.com/jentic/jentic-one/cli/internal/localagent"
	"github.com/jentic/jentic-one/cli/internal/theme"
)

// exportContextToAgent writes the active context's config + credentials into
// the agent home's XDG store (<home>/.config/jentic, <home>/.local/state/
// jentic) and chowns them to the agent user. When there is no active V2
// context (or the session is file-less) it is a reported no-op — the session
// then simply has no identity until the operator registers one.
//
// The exported context's mode is forced to "agent": inside the isolated
// session the actor IS an agent, so fencing must hold there regardless of the
// operator-side mode.
func (a *App) exportContextToAgent(ctx context.Context, acct config.AgentAccount) error {
	st := clictx.ActiveV2(ctx)
	if st == nil {
		fmt.Fprintln(a.Out, theme.Dim.Render(
			"No active context to hand to the agent — its session starts without an identity "+
				"(run `jentic register`, then relaunch)."))
		return nil
	}
	if st.InjectedBearerToken != "" {
		// File-less orchestrator mode: there is no on-disk material to export,
		// and the injected token belongs to THIS process's environment only.
		fmt.Fprintln(a.Out, theme.Dim.Render(
			"Running file-less (JENTIC_BASE_URL/JENTIC_AGENT_TOKEN) — nothing to export to the agent home."))
		return nil
	}
	if acct.HomeDir == "" {
		return errors.New("agent account has no recorded home directory")
	}
	// The home is about to receive files and a privileged recursive chown;
	// guard the recorded path exactly as the reset teardown does.
	if err := localagent.ValidateHomeDir(acct.HomeDir); err != nil {
		return fmt.Errorf("refusing to export the context: %w", err)
	}

	cfgDir := filepath.Join(acct.HomeDir, ".config", "jentic")
	stateDir := filepath.Join(acct.HomeDir, ".local", "state", "jentic")
	keysDir := filepath.Join(cfgDir, "keys")
	for _, d := range []string{cfgDir, stateDir, keysDir} {
		if err := os.MkdirAll(d, 0o700); err != nil {
			return fmt.Errorf("create agent config dir %s: %w", d, err)
		}
		// MkdirAll's mode is masked by umask and leaves pre-existing dirs
		// untouched; pin 0700 so the secrets dirs can't sit wider. A directory
		// needs its owner-execute bit to be traversable, so 0700 (not 0600) is
		// the correct floor here.
		if err := os.Chmod(d, 0o700); err != nil { //nolint:gosec // G302: dir needs owner-exec; 0700 is the intended secrets-dir floor
			return err
		}
	}

	// Minimal config: ONE environment/identity/context — the agent must not
	// inherit the operator's other environments or identities. The context
	// name and CA path come from the operator's config (ResolvedState carries
	// neither); a context that no longer round-trips gets the neutral name
	// "agent".
	opCfg, err := sdkconfig.Load()
	if err != nil {
		return err
	}
	ctxName := "agent"
	if c, ok := opCfg.Contexts[opCfg.ActiveContext]; ok &&
		c.Environment == st.EnvironmentName && c.Identity == st.IdentityName {
		ctxName = opCfg.ActiveContext
	}
	var reg map[string]sdkconfig.EnvRegState
	if r, ok := opCfg.Identities[st.IdentityName].Environments[st.EnvironmentName]; ok {
		reg = map[string]sdkconfig.EnvRegState{st.EnvironmentName: r}
	}
	minimal := sdkconfig.Config{
		ActiveContext: ctxName,
		Contexts: map[string]sdkconfig.Context{
			ctxName: {
				Environment: st.EnvironmentName,
				Identity:    st.IdentityName,
				Mode:        clictx.ModeAgent,
			},
		},
		Environments: map[string]sdkconfig.Env{
			st.EnvironmentName: {
				BaseURL:    st.BaseURL,
				BrokerURL:  st.BrokerURL,
				CACertPath: opCfg.Environments[st.EnvironmentName].CACertPath,
			},
		},
		Identities: map[string]sdkconfig.Identity{
			st.IdentityName: {Type: "agent", Environments: reg},
		},
	}
	data, err := yaml.Marshal(&minimal)
	if err != nil {
		return fmt.Errorf("encode agent config: %w", err)
	}
	if err := writeFile0600(filepath.Join(cfgDir, "config.yaml"), data); err != nil {
		return err
	}

	// Credential material: the env-scoped key (config/keys/<stem>.key) plus
	// token/API-key state (<state>/<stem>*). Copied best-effort per file —
	// e.g. a not-yet-exchanged identity has a key but no tokens.
	ref := auth.IdentityRef{Identity: st.IdentityName, Environment: st.EnvironmentName}
	stem, err := ref.Stem()
	if err != nil {
		return err
	}
	srcKey, err := auth.KeyPathForImport(ref)
	if err != nil {
		return err
	}
	copies := [][2]string{{srcKey, filepath.Join(keysDir, stem+".key")}}
	if srcState, serr := sdkconfig.StateDir(); serr == nil {
		for _, suffix := range []string{"_tokens.json", ".apikey"} {
			copies = append(copies, [2]string{
				filepath.Join(srcState, stem+suffix),
				filepath.Join(stateDir, stem+suffix),
			})
		}
	}
	for _, c := range copies {
		if err := copyCredFile(c[0], c[1]); err != nil {
			return err
		}
	}

	// Hand the files to the agent uid: they were created by the operator, but
	// the agent must read its own 0600 key/tokens when it runs as itself.
	// Best-effort, like the V1 hand-off: a chown failure is reported, not
	// fatal (the launch may still work if a previous export already chowned).
	for _, d := range []string{filepath.Join(acct.HomeDir, ".config"), filepath.Join(acct.HomeDir, ".local")} {
		chown := localagent.ChownToAgentCmd(acct.User, d)
		chown.Stdout, chown.Stderr = a.Out, a.Err
		if err := chown.Run(); err != nil {
			fmt.Fprintln(a.Out, theme.Warnf("could not hand the agent its config (%s): %v", d, err))
		}
	}
	fmt.Fprintln(a.Out, theme.Dim.Render(fmt.Sprintf(
		"Handed context %q (identity %q, environment %q) to the agent.", ctxName, st.IdentityName, st.EnvironmentName)))
	return nil
}

// copyCredFile copies one credential file 0600, fsyncing before returning. A
// missing source is a no-op (not every identity has tokens or an API key); it
// REFUSES a non-regular source (symlinks in a credential dir are a sign of
// tampering, and following one could leak an unrelated file into the agent
// home).
func copyCredFile(src, dst string) error {
	info, err := os.Lstat(src)
	if os.IsNotExist(err) {
		return nil
	}
	if err != nil {
		return err
	}
	if !info.Mode().IsRegular() {
		return fmt.Errorf("refusing to copy non-regular credential file %q", src)
	}
	in, err := os.Open(src) //nolint:gosec // src is a stem-validated path under the operator's own XDG store.
	if err != nil {
		return err
	}
	defer in.Close()
	data, err := io.ReadAll(in)
	if err != nil {
		return err
	}
	return writeFile0600(dst, data)
}

// writeFile0600 writes data to path with exactly 0600 perms (chmod pins the
// mode even when the file pre-exists or umask is loose) and fsyncs so the
// bytes are durable before the launch hands the tree over.
func writeFile0600(path string, data []byte) error {
	out, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_TRUNC, 0o600) //nolint:gosec // path is a constructed agent-home XDG path, not user input.
	if err != nil {
		return err
	}
	if err := out.Chmod(0o600); err != nil {
		_ = out.Close()
		return err
	}
	if _, err := out.Write(data); err != nil {
		_ = out.Close()
		return err
	}
	if err := out.Sync(); err != nil {
		_ = out.Close()
		return err
	}
	return out.Close()
}
