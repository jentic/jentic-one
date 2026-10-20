package localagentcmd

// run_container.go is `jentic run --container`: the agent runs in a Linux
// container instead of as a dedicated Unix user (see
// internal/localagent/container for the session's topology). The container is
// the isolation, so no agent account, ACL grant or confinement profile is
// involved; the work folder is bind-mounted at its host path and the active
// context is exported into the session's home volume, which teardown deletes.
// With the default "proxy" egress the agent reaches its LLM provider and the
// governed vendor APIs through the session's egress proxy
// (run_container_egress.go).

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"net"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"time"

	"github.com/gofrs/flock"

	sdkconfig "github.com/jentic/jentic-one/cli/client/config"
	"github.com/jentic/jentic-one/cli/internal/cli/clictx"
	"github.com/jentic/jentic-one/cli/internal/cli/cmdcore"
	"github.com/jentic/jentic-one/cli/internal/cli/prompt"
	"github.com/jentic/jentic-one/cli/internal/cli/ux"
	"github.com/jentic/jentic-one/cli/internal/localagent"
	"github.com/jentic/jentic-one/cli/internal/localagent/container"
	"github.com/jentic/jentic-one/cli/internal/theme"
	"github.com/spf13/cobra"
)

const (
	// containerTeardownTimeout bounds the teardown that runs after the
	// session ends, cancelled or not.
	containerTeardownTimeout = 60 * time.Second
	// containerStopGrace is how long the agent container gets to exit on
	// SIGTERM before Docker kills it.
	containerStopGrace = 10 * time.Second
	// defaultBrokerPort is forwarded when the context names no broker URL:
	// the broker's conventional local port.
	defaultBrokerPort = 8100
	// defaultContainerUID is the agent's uid:gid in the container when the
	// engine maps bind-mount ownership itself (Docker Desktop) or the client
	// runs as root.
	defaultContainerUID = 1000
)

// containerEnvPassthrough are the host variables passed to the agent
// container when set: terminal and locale only.
var containerEnvPassthrough = []string{"TERM", "COLORTERM", "LANG", "LC_ALL", "LC_CTYPE"}

// containerAgents are the agents container mode can run, mapped to the
// builder of their own home files.
var containerAgents = map[string]func(a *Cmd, prefs seedPrefs, workDir string, share *container.ProviderShare) ([]container.HomeFile, error){
	"claude": claudeContainerFiles,
}

type containerOptions struct {
	enabled       bool
	image         string
	egress        string
	shareProvider bool
}

func addContainerFlags(cmd *cobra.Command, o *containerOptions) {
	cmd.Flags().BoolVar(&o.enabled, "container", false,
		"EXPERIMENTAL: run the agent in a Docker container instead of as the agent user "+
			"(the work folder is mounted; only Jentic is reachable from inside)")
	cmd.Flags().StringVar(&o.image, "container-image", container.DefaultAgentImage,
		"EXPERIMENTAL: the agent image for --container")
	cmd.Flags().StringVar(&o.egress, "container-egress", container.EgressProxy,
		"EXPERIMENTAL: the --container egress mode; "+strings.Join(container.EgressModes(), ", ")+
			" (proxy: the agent's LLM provider is tunnelled, governed vendor APIs go through Jentic, everything else is refused;"+
			" none: the agent reaches only Jentic)")
	cmd.Flags().BoolVar(&o.shareProvider, shareProviderFlag, false,
		"EXPERIMENTAL, for local testing: share your own LLM-provider login with the --container agent "+
			"(~/.aws or ~/.config/gcloud mounted read-only, the provider's env, or ANTHROPIC_API_KEY); "+
			"the agent can then use that identity directly, bypassing Jentic. Asks for confirmation unless --yes")
}

// containerConflicts names the flags that only mean something for the agent
// user and are refused together with --container.
func containerConflicts(cmd *cobra.Command) []string {
	var bad []string
	for _, name := range []string{"home", "allow-dir", "no-allow-dir", "agent-user", "list-grants", "grant", "revoke"} {
		if f := cmd.Flags().Lookup(name); f != nil && f.Changed {
			bad = append(bad, "--"+name)
		}
	}
	return bad
}

// containerUnavailableErr codes a container session that cannot start. The
// container is the confinement in this mode, so it shares
// CONFINEMENT_UNAVAILABLE with an unconfinable agent-user launch.
func containerUnavailableErr(msg, hint string) error {
	if hint == "" {
		hint = "Fix the Docker setup above, or run without --container to use the agent user."
	}
	return &ux.CodedError{Code: ux.CodeConfinementMissing, Msg: msg, Actionable: hint}
}

func (a *Cmd) runContainer(cmd *cobra.Command, opts *runOptions, agentID string, posArgs, agentArgs []string) error {
	ctx := cmd.Context()
	if bad := containerConflicts(cmd); len(bad) > 0 {
		return fmt.Errorf("--container cannot be combined with %s", strings.Join(bad, ", "))
	}
	homeFilesFor, ok := containerAgents[agentID]
	if !ok {
		return fmt.Errorf("--container runs only %s for now, not %q", strings.Join(containerAgentIDs(), ", "), agentID)
	}
	egressMode, err := container.ParseEgressMode(opts.container.egress)
	if err != nil {
		return err
	}
	workDir, err := containerWorkDir(posArgs)
	if err != nil {
		return err
	}

	st := clictx.ActiveContext(ctx)
	if st == nil || st.ResolvedState == nil {
		return accountMissingErr("--container needs an active context to hand to the agent; run `jentic register` first")
	}
	if st.InjectedBearerToken != "" {
		return containerUnavailableErr(
			"--container cannot run file-less (JENTIC_BEARER_TOKEN): there is no context on disk to hand to the agent",
			"Unset JENTIC_BASE_URL/JENTIC_BEARER_TOKEN and select a registered context, then re-run.")
	}
	ports, err := jenticLoopbackPorts(st.BaseURL, st.BrokerURL)
	if err != nil {
		return containerUnavailableErr(err.Error(),
			"--container reaches Jentic through the host's loopback only; use a context on a local Jentic.")
	}

	d := container.CLI{}
	images := []string{opts.container.image, container.DefaultBridgeImage}
	if egressMode == container.EgressProxy {
		images = append(images, container.DefaultProxyImage)
	}
	engine, err := container.Preflight(ctx, d, images...)
	if err != nil {
		var pe *container.PreflightError
		if errors.As(err, &pe) {
			return containerUnavailableErr(pe.Reason, pe.Hint)
		}
		return err
	}

	locks, err := containerLockDir()
	if err != nil {
		return err
	}
	owner := containerOwner()
	reaped, err := container.Reap(ctx, d, owner, locks.alive)
	for _, id := range reaped {
		locks.remove(id)
		fmt.Fprintln(a.Out, theme.Dim.Render("Removed the leftovers of container session "+id+" (its client is gone)."))
	}
	if err != nil {
		fmt.Fprintln(a.Out, theme.Warnf("could not remove every stale container session: %v", err))
	}

	mat, skip, err := a.buildExportMaterial(ctx)
	if err != nil {
		return err
	}
	share, err := a.prepareProviderShare(cmd, opts)
	if err != nil {
		return err
	}
	agentFiles, err := homeFilesFor(a, opts.seedPrefs(cmd), workDir, share)
	if err != nil {
		return err
	}
	files := make([]container.HomeFile, 0, len(agentFiles)+4)
	if !skip {
		exported, err := mat.homeFiles()
		if err != nil {
			return err
		}
		files = append(files, exported...)
		fmt.Fprintln(a.Out, theme.Dim.Render(fmt.Sprintf(
			"Handing context %q (identity %q, environment %q) to the agent.", mat.ctxName, mat.identity, mat.environment)))
	}
	files = append(files, agentFiles...)

	sessionID, err := container.NewSessionID()
	if err != nil {
		return err
	}
	lock, err := locks.acquire(sessionID)
	if err != nil {
		return err
	}
	defer func() {
		tctx, cancel := context.WithTimeout(context.WithoutCancel(ctx), containerTeardownTimeout)
		defer cancel()
		if err := container.Teardown(tctx, d, sessionID); err != nil {
			fmt.Fprintln(a.Err, theme.Warnf("container session %s was not fully removed (the next --container run retries): %v", sessionID, err))
		} else {
			locks.remove(sessionID)
		}
		_ = lock.Unlock()
	}()

	uid := containerUID(engine)
	desc, _ := localagent.Lookup(agentID)
	var egress container.Egress = container.NoEgress{}
	var proxy *proxyEgressSetup
	if egressMode == container.EgressProxy {
		if proxy, err = setUpProxyEgress(ctx, st, desc, container.DefaultProxyImage, providerShareGetenv(share)); err != nil {
			return err
		}
		// Registered after the teardown, so it runs first: the tokens are
		// dead before the containers go.
		defer func() {
			rctx, cancel := context.WithTimeout(context.WithoutCancel(ctx), containerTeardownTimeout)
			defer cancel()
			if err := proxy.Close(rctx); err != nil {
				fmt.Fprintln(a.Err, theme.Warnf("not every session invocation token was revoked (each expires on its own): %v", err))
			}
		}()
		egress = proxy.egress
	}
	var providerMounts []container.Mount
	if share != nil {
		providerMounts = share.Mounts
	}
	plan, err := container.NewPlan(container.Options{
		SessionID:      sessionID,
		Owner:          owner,
		AgentImage:     opts.container.image,
		BridgeImage:    container.DefaultBridgeImage,
		WorkDir:        workDir,
		JenticPorts:    ports,
		Route:          container.RouteFor(engine, runtime.GOOS),
		AgentUser:      fmt.Sprintf("%d:%d", uid, uid),
		TTY:            prompt.StdinIsTerminal() && cmdcore.StdoutIsTerminal(),
		Env:            passthroughEnv(),
		Command:        append([]string{desc.Binary}, agentArgs...),
		Egress:         egress,
		ProviderMounts: providerMounts,
	})
	if err != nil {
		return err
	}
	archive, err := container.HomeArchive(uid, uid, files)
	if err != nil {
		return err
	}

	fmt.Fprintln(a.Out, theme.Warnf("--container is experimental."))
	fmt.Fprintln(a.Out, theme.Infof("Starting %s in a container (session %s) with %s mounted ...", desc.Binary, sessionID, workDir))
	printEgressSummary(a, egressMode, proxy)
	sess := &container.Session{Plan: plan, Docker: d}
	if err := sess.Up(ctx); err != nil {
		return containerUnavailableErr(fmt.Sprintf("could not start the container session: %v", err), "")
	}
	if proxy != nil && proxy.keeper != nil {
		proxy.keeper.Run(ctx, func(ctx context.Context, tokens map[string]string) error {
			return proxy.egress.PushTokens(ctx, d, plan, tokens)
		})
	}
	if err := sess.Seed(ctx, bytes.NewReader(archive)); err != nil {
		return containerUnavailableErr(err.Error(), "")
	}
	return a.attachContainer(ctx, sess)
}

// attachContainer runs the created agent container in the foreground until it
// exits, passing its exit code through. A cancel (SIGINT/SIGTERM to jentic)
// stops the container; a bridge container that stops ends the session.
func (a *Cmd) attachContainer(ctx context.Context, sess *container.Session) error {
	watchCtx, stopWatch := context.WithCancel(ctx)
	defer stopWatch()
	bridgeDown := sess.WatchBridges(watchCtx)

	attach := sess.AttachCmd(ctx)
	wireTerminal(attach)
	attach.Cancel = func() error {
		stopCtx, cancel := context.WithTimeout(context.WithoutCancel(ctx), containerStopGrace+5*time.Second)
		defer cancel()
		return sess.Stop(stopCtx, containerStopGrace)
	}
	attach.WaitDelay = containerStopGrace + 10*time.Second
	if err := attach.Start(); err != nil {
		return fmt.Errorf("attach to the agent container: %w", err)
	}
	waited := make(chan error, 1)
	go func() { waited <- attach.Wait() }()

	var waitErr error
	select {
	case waitErr = <-waited:
	case name := <-bridgeDown:
		stopCtx, cancel := context.WithTimeout(context.WithoutCancel(ctx), containerStopGrace+5*time.Second)
		_ = sess.Stop(stopCtx, containerStopGrace)
		cancel()
		<-waited
		return containerUnavailableErr(fmt.Sprintf("the session's bridge container %s stopped, so the session was ended", name), "")
	}
	if waitErr == nil {
		return nil
	}
	var exitErr *exec.ExitError
	if errors.As(waitErr, &exitErr) && exitErr.ExitCode() >= 0 {
		return &cmdcore.ExitCodeError{Code: exitErr.ExitCode()}
	}
	return fmt.Errorf("run the agent container: %w", waitErr)
}

// printEgressSummary says what the agent can reach beyond Jentic.
func printEgressSummary(a *Cmd, mode string, proxy *proxyEgressSetup) {
	if mode == container.EgressNone || proxy == nil {
		fmt.Fprintln(a.Out, theme.Dim.Render("  Egress: none. The agent reaches only Jentic; calls to its LLM provider and to vendor APIs fail."))
		return
	}
	governed := "none"
	if len(proxy.egress.Vendors) > 0 {
		governed = strings.Join(proxy.egress.Vendors, ", ")
	}
	tunnel := "none"
	if len(proxy.egress.Tunnel) > 0 {
		tunnel = strings.Join(proxy.egress.Tunnel, ", ")
	}
	fmt.Fprintln(a.Out, theme.Dim.Render("  Egress: proxy. Governed through Jentic: "+governed+"; tunnelled: "+tunnel+"; everything else is refused."))
}

func containerAgentIDs() []string {
	ids := make([]string, 0, len(containerAgents))
	for id := range containerAgents {
		ids = append(ids, id)
	}
	return ids
}

// containerWorkDir resolves the work folder (the path argument, else the
// current directory) to its canonical form and refuses one that may not be
// handed to an agent at all (a home root, a credential dir, a system tree).
func containerWorkDir(posArgs []string) (string, error) {
	var dir string
	if len(posArgs) > 1 {
		dir = posArgs[1]
	} else {
		cwd, err := os.Getwd()
		if err != nil {
			return "", err
		}
		dir = cwd
	}
	abs := localagent.Canonicalize(dir)
	info, err := os.Stat(abs)
	if err != nil {
		return "", fmt.Errorf("work folder %s: %w", abs, err)
	}
	if !info.IsDir() {
		return "", fmt.Errorf("work folder %s is not a directory", abs)
	}
	if v := localagent.Classify(abs, localagent.OperatorHome()); v.Banned() {
		return "", fmt.Errorf("refusing to mount %s into the container: %s", abs, v.Reason)
	}
	return abs, nil
}

// jenticLoopbackPorts returns the ports the agent must reach Jentic on: the
// base URL's and the broker URL's (or defaultBrokerPort when there is none).
// Both must name the host's loopback, which is all the relay forwards to.
func jenticLoopbackPorts(baseURL, brokerURL string) ([]int, error) {
	var ports []int
	add := func(raw, what string) error {
		port, err := loopbackPort(raw)
		if err != nil {
			return fmt.Errorf("the context's %s %q: %w", what, raw, err)
		}
		for _, p := range ports {
			if p == port {
				return nil
			}
		}
		ports = append(ports, port)
		return nil
	}
	if err := add(baseURL, "base URL"); err != nil {
		return nil, err
	}
	if brokerURL != "" {
		if err := add(brokerURL, "broker URL"); err != nil {
			return nil, err
		}
	} else if err := add("http://127.0.0.1:"+strconv.Itoa(defaultBrokerPort), "broker URL"); err != nil {
		return nil, err
	}
	return ports, nil
}

func loopbackPort(raw string) (int, error) {
	u, err := url.Parse(raw)
	if err != nil || u.Host == "" {
		return 0, errors.New("is not an absolute URL")
	}
	host := u.Hostname()
	if host != "localhost" {
		ip := net.ParseIP(host)
		if ip == nil || !ip.IsLoopback() {
			return 0, errors.New("is not on this machine's loopback (127.0.0.1 or localhost)")
		}
	}
	portText := u.Port()
	if portText == "" {
		switch u.Scheme {
		case "http":
			portText = "80"
		case "https":
			portText = "443"
		default:
			return 0, fmt.Errorf("has an unsupported scheme %q", u.Scheme)
		}
	}
	port, err := strconv.Atoi(portText)
	if err != nil || port < 1 || port > 65535 {
		return 0, fmt.Errorf("has an invalid port %q", portText)
	}
	return port, nil
}

// containerUID is the agent's uid (and gid) in the container. Docker Desktop
// maps bind-mount ownership to the host user whatever the uid; a native engine
// writes the container's uid to the host, so the client's own uid keeps the
// work folder's files the operator's.
func containerUID(e container.Engine) int {
	if e.Desktop() || runtime.GOOS != "linux" {
		return defaultContainerUID
	}
	if uid := os.Getuid(); uid > 0 {
		return uid
	}
	return defaultContainerUID
}

func containerOwner() string {
	host, err := os.Hostname()
	if err != nil || host == "" {
		host = "localhost"
	}
	return container.OwnerLabel(os.Getuid(), host)
}

func passthroughEnv() []string {
	var env []string
	for _, k := range containerEnvPassthrough {
		if v, ok := os.LookupEnv(k); ok {
			env = append(env, k+"="+v)
		}
	}
	return env
}

// homeFiles reads the material's files into memory for a container session's
// home: the config plus each credential file that exists. A non-regular
// credential source is refused, as on the agent-user path.
func (m *exportMaterial) homeFiles() ([]container.HomeFile, error) {
	files := []container.HomeFile{{Rel: filepath.ToSlash(filepath.Join(".config", "jentic", "config.yaml")), Data: m.configYAML}}
	for _, c := range m.copies {
		info, err := os.Lstat(c[0])
		if errors.Is(err, os.ErrNotExist) {
			continue
		}
		if err != nil {
			return nil, err
		}
		if !info.Mode().IsRegular() {
			return nil, fmt.Errorf("refusing to copy non-regular credential file %q", c[0])
		}
		data, err := os.ReadFile(c[0])
		if err != nil {
			return nil, err
		}
		files = append(files, container.HomeFile{Rel: filepath.ToSlash(c[1]), Data: data})
	}
	return files, nil
}

// claudeContainerFiles builds Claude Code's home files for a container
// session. The onboarding and project-trust state is always written; the
// operator's own preferences, settings and CLAUDE.md are read only when the
// operator agrees to seed them (the same --seed-config/--no-seed-config
// decision as the agent-user path), and only their allowlisted parts are kept.
// A provider share (--container-share-provider-credentials) adds its env to
// the settings and the home directories its mounts sit below.
func claudeContainerFiles(a *Cmd, prefs seedPrefs, workDir string, share *container.ProviderShare) ([]container.HomeFile, error) {
	var seed container.ClaudeSeed
	home := localagent.OperatorHome()
	sources := []string{filepath.Join(home, ".claude.json"), filepath.Join(home, ".claude", "settings.json"), filepath.Join(home, ".claude", "CLAUDE.md")}
	var present []string
	for _, s := range sources {
		if info, err := os.Lstat(s); err == nil && info.Mode().IsRegular() {
			present = append(present, s)
		}
	}
	if !prefs.forceNoSeed && len(present) > 0 && a.decideSeedConfig(prefs, present) {
		read := func(p string) []byte {
			info, err := os.Lstat(p)
			if err != nil || !info.Mode().IsRegular() {
				return nil
			}
			data, err := os.ReadFile(p) //nolint:gosec // a fixed path under the operator's home.
			if err != nil {
				return nil
			}
			return data
		}
		seed = container.ClaudeSeed{State: read(sources[0]), Settings: read(sources[1]), Memory: read(sources[2])}
		fmt.Fprintln(a.Out, theme.Dim.Render("  Seeding your Claude Code preferences (no hooks, plugins, API keys or login state)."))
	}
	var dirs []container.HomeFile
	if share != nil {
		seed.ProviderEnv = share.Env
		for _, d := range share.Dirs {
			dirs = append(dirs, container.HomeFile{Rel: d, Dir: true})
		}
	}
	files, err := container.ClaudeHomeFiles(workDir, seed)
	if err != nil {
		return nil, err
	}
	return append(files, dirs...), nil
}

// containerLocks are the per-session lock files that tell a live session from
// one whose client died: a running `jentic run --container` holds its
// session's lock, and the kernel drops it when the process ends, however it
// ends.
type containerLocks struct{ dir string }

func containerLockDir() (containerLocks, error) {
	state, err := sdkconfig.StateDir()
	if err != nil {
		return containerLocks{}, err
	}
	dir := filepath.Join(state, "run-sessions")
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return containerLocks{}, fmt.Errorf("create %s: %w", dir, err)
	}
	return containerLocks{dir: dir}, nil
}

func (l containerLocks) path(id string) string { return filepath.Join(l.dir, id+".lock") }

func (l containerLocks) acquire(id string) (*flock.Flock, error) {
	fl := flock.New(l.path(id))
	ok, err := fl.TryLock()
	if err != nil {
		return nil, fmt.Errorf("lock container session %s: %w", id, err)
	}
	if !ok {
		return nil, fmt.Errorf("container session %s is already locked", id)
	}
	return fl, nil
}

// alive reports whether session id's client still holds its lock. An error
// probing the lock counts as alive, so a session is never reaped on doubt.
func (l containerLocks) alive(id string) bool {
	fl := flock.New(l.path(id))
	ok, err := fl.TryLock()
	if err != nil {
		return true
	}
	if ok {
		_ = fl.Unlock()
		return false
	}
	return true
}

func (l containerLocks) remove(id string) { _ = os.Remove(l.path(id)) }
