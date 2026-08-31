package localagentcmd

// context_export.go hands the operator's ACTIVE context to the shared agent
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
// jentic) and chowns them to the agent user. When there is no active
// context (or the session is file-less) it is a reported no-op — the session
// then simply has no identity until the operator registers one.
//
// The exported context's mode is forced to "agent": inside the isolated
// session the actor IS an agent, so fencing must hold there regardless of the
// operator-side mode.
func (a *Cmd) exportContextToAgent(ctx context.Context, acct config.AgentAccount) error {
	return a.exportContextMaterial(ctx, acct.User, acct.HomeDir)
}

// exportContextMaterial is the shared export core for the LAUNCH path: it
// writes the active context's minimal config + credentials into the XDG store
// under homeDir and chowns them to user. This direct-write form works only
// because CreateAccountCmds grants the operator an inherited ACL into the
// agent home; the MCP isolation step's service accounts deliberately grant
// the operator NOTHING, so that path renders to a staging dir and installs
// root-side instead (exportContextMaterialRootSide).
func (a *Cmd) exportContextMaterial(ctx context.Context, user, homeDir string) error {
	mat, skip, err := a.buildExportMaterial(ctx)
	if err != nil || skip {
		return err
	}
	if homeDir == "" {
		return errors.New("target account has no recorded home directory")
	}
	// The home is about to receive files and a privileged recursive chown;
	// guard the recorded path exactly as the reset teardown does.
	if err := localagent.ValidateHomeDir(homeDir); err != nil {
		return fmt.Errorf("refusing to export the context: %w", err)
	}

	for _, rel := range mat.relDirs {
		d := filepath.Join(homeDir, rel)
		if err := os.MkdirAll(d, 0o700); err != nil {
			return fmt.Errorf("create agent config dir %s: %w", d, err)
		}
		// MkdirAll's mode is masked by umask and leaves pre-existing dirs
		// untouched; pin 0700 so the secrets dirs can't sit wider. A directory
		// needs its owner-execute bit to be traversable, so 0700 (not 0600) is
		// the correct floor here. The pin must tolerate a dir a PREVIOUS export
		// already chowned to the agent uid: chmod is owner-gated on POSIX, so
		// the operator's re-run gets EPERM there even though the mode is the
		// 0700 we pinned at creation — pinDirMode0700 skips an already-tight
		// dir instead of failing every launch after the first. A dir that is
		// genuinely wider AND un-chmod-able can't be fixed from here; warn
		// rather than abort (its contents are written 0600 regardless).
		if err := pinDirMode0700(d); err != nil {
			fmt.Fprintln(a.Out, theme.Warnf("could not pin %s to 0700: %v", d, err))
		}
	}
	if err := mat.renderInto(homeDir); err != nil {
		return err
	}

	// Hand the files to the agent uid: they were created by the operator, but
	// the agent must read its own 0600 key/tokens when it runs as itself.
	// Best-effort, like the V1 hand-off: a chown failure is reported, not
	// fatal (the launch may still work if a previous export already chowned).
	for _, d := range []string{filepath.Join(homeDir, ".config"), filepath.Join(homeDir, ".local")} {
		chown := localagent.ChownToAgentCmd(user, d)
		chown.Stdout, chown.Stderr = a.Out, a.Err
		if err := chown.Run(); err != nil {
			fmt.Fprintln(a.Out, theme.Warnf("could not hand the agent its config (%s): %v", d, err))
		}
	}
	fmt.Fprintln(a.Out, theme.Dim.Render(fmt.Sprintf(
		"Handed context %q (identity %q, environment %q) to the agent.", mat.ctxName, mat.identity, mat.environment)))
	return nil
}

// exportMaterial is the rendered form of the active context's exportable
// state: the minimal config bytes plus the credential files to copy, all
// addressed by home-relative XDG paths so the same material can be written
// directly into an agent home (launch path) or staged and installed
// root-side (MCP isolation path).
type exportMaterial struct {
	ctxName     string
	identity    string
	environment string
	configYAML  []byte
	// copies maps an absolute source path to its home-relative destination.
	// The signing key's presence is verified by buildExportMaterial; token/
	// API-key sources may legitimately be absent (copied best-effort).
	copies [][2]string
	// relDirs are the home-relative dirs the material lands in, parent-first.
	relDirs []string
}

// relFiles returns the home-relative paths of every file the render produces:
// the config plus each credential copy whose source exists on disk.
func (m *exportMaterial) relFiles() []string {
	files := []string{filepath.Join(".config", "jentic", "config.yaml")}
	for _, c := range m.copies {
		if _, err := os.Lstat(c[0]); err == nil {
			files = append(files, c[1])
		}
	}
	return files
}

// renderInto writes the material under root (an agent home or a staging
// dir), assuming root's relDirs already exist.
func (m *exportMaterial) renderInto(root string) error {
	if err := writeFile0600(filepath.Join(root, ".config", "jentic", "config.yaml"), m.configYAML); err != nil {
		return err
	}
	for _, c := range m.copies {
		if err := copyCredFile(c[0], filepath.Join(root, c[1])); err != nil {
			return err
		}
	}
	return nil
}

// buildExportMaterial resolves the active context into exportable material.
// skip=true (with a printed note and no error) when there is nothing to
// export: no active context, or a file-less injected-token session. A context
// whose signing KEY is missing on disk is an ERROR, not a silent skip — an
// export that hands over a keyless config would leave the target account
// unable to act as the context while looking provisioned.
func (a *Cmd) buildExportMaterial(ctx context.Context) (*exportMaterial, bool, error) {
	st := clictx.ActiveContext(ctx)
	if st == nil {
		fmt.Fprintln(a.Out, theme.Dim.Render(
			"No active context to hand to the agent — its session starts without an identity "+
				"(run `jentic register`, then relaunch)."))
		return nil, true, nil
	}
	if st.InjectedBearerToken != "" {
		// File-less orchestrator mode: there is no on-disk material to export,
		// and the injected token belongs to THIS process's environment only.
		fmt.Fprintln(a.Out, theme.Dim.Render(
			"Running file-less (JENTIC_BASE_URL/JENTIC_BEARER_TOKEN) — nothing to export to the agent home."))
		return nil, true, nil
	}

	// Minimal config: ONE environment/identity/context — the agent must not
	// inherit the operator's other environments or identities. The context
	// name and CA path come from the operator's config (ResolvedState carries
	// neither); a context that no longer round-trips gets the neutral name
	// "agent".
	opCfg, err := sdkconfig.Load()
	if err != nil {
		return nil, false, err
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
		return nil, false, fmt.Errorf("encode agent config: %w", err)
	}

	// Credential material: the env-scoped key (config/keys/<stem>.key) plus
	// token/API-key state (<state>/<stem>*). The KEY must exist — a context
	// with no key is not exportable material (the export would strand the
	// target with a config it cannot authenticate as). Tokens/API keys are
	// copied best-effort per file — e.g. a not-yet-exchanged identity has a
	// key but no tokens.
	ref := auth.IdentityRef{Identity: st.IdentityName, Environment: st.EnvironmentName}
	stem, err := ref.Stem()
	if err != nil {
		return nil, false, err
	}
	srcKey, err := auth.KeyPathForImport(ref)
	if err != nil {
		return nil, false, err
	}
	if _, err := os.Lstat(srcKey); err != nil {
		return nil, false, fmt.Errorf(
			"context %q has no signing key on disk (%s) — refusing to export keyless material: %w",
			ctxName, prettyPath(srcKey), err)
	}
	copies := [][2]string{{srcKey, filepath.Join(".config", "jentic", "keys", stem+".key")}}
	if srcState, serr := sdkconfig.StateDir(); serr == nil {
		for _, suffix := range []string{"_tokens.json", ".apikey"} {
			copies = append(copies, [2]string{
				filepath.Join(srcState, stem+suffix),
				filepath.Join(".local", "state", "jentic", stem+suffix),
			})
		}
	}

	return &exportMaterial{
		ctxName:     ctxName,
		identity:    st.IdentityName,
		environment: st.EnvironmentName,
		configYAML:  data,
		copies:      copies,
		relDirs: []string{
			filepath.Join(".config", "jentic"),
			filepath.Join(".config", "jentic", "keys"),
			filepath.Join(".local", "state", "jentic"),
		},
	}, false, nil
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

// pinDirMode0700 pins directory d to exactly 0700, skipping the chmod when the
// mode already matches. The skip is what keeps repeat launches working: the
// export chowns the agent's XDG tree to the agent uid at the end of each run,
// and chmod is owner-gated on POSIX, so once the hand-off has happened the
// operator can no longer chmod these dirs (the inherited ACL grants read/write,
// never chmod) — but they are already at the pinned 0700, so there is nothing
// to do. A chmod is attempted (and its error surfaced) only when the mode is
// genuinely wider than the floor.
func pinDirMode0700(d string) error {
	info, err := os.Lstat(d)
	if err != nil {
		return err
	}
	if info.Mode().Perm() == 0o700 {
		return nil
	}
	return os.Chmod(d, 0o700) //nolint:gosec // G302: dir needs owner-exec; 0700 is the intended secrets-dir floor
}

// writeFile0600 writes data to path with exactly 0600 perms (chmod pins the
// mode even when the file pre-exists or umask is loose) and fsyncs so the
// bytes are durable before the launch hands the tree over. Like
// pinDirMode0700, the pin skips an already-0600 file: a previous export
// chowned it to the agent uid, so the operator can still write it through the
// inherited ACL but can no longer chmod it — and doesn't need to.
func writeFile0600(path string, data []byte) error {
	out, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_TRUNC, 0o600) //nolint:gosec // path is a constructed agent-home XDG path, not user input.
	if err != nil {
		return err
	}
	if info, err := out.Stat(); err != nil {
		_ = out.Close()
		return err
	} else if info.Mode().Perm() != 0o600 {
		if err := out.Chmod(0o600); err != nil {
			_ = out.Close()
			return err
		}
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
