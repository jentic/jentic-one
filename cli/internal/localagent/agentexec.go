package localagent

import (
	"context"
	"os"
	"os/exec"
	"strings"
)

// agentBashArgs builds the sudo argv that runs snippet as agentUser in a
// NON-login, non-interactive bash that reads no startup files (`--noprofile
// --norc`). Shared by every unconfined agent invocation (preflight, probes,
// PATH fixes, and the install route); the confined launch has its own builder.
//
// The agent's rc/profile files are agent-writable, so sourcing them here would
// run agent-controlled code outside the confinement wrapper. They are only ever
// sourced by the login shell confineExec starts INSIDE the wrapper. Because no
// profile is read, the snippet runs with a fixed system PATH (agentProbePATH)
// instead of whatever the agent's profile exports; ProbeBinary accounts for the
// one directory our own profile export adds (~/.local/bin) via ProbePaths.
//
// We use `sudo -u <user> -H /bin/bash …` rather than `sudo -i`: `-i`
// re-serializes the command through the login shell (mangling any
// multi-token/multi-line snippet), while plain sudo passes argv straight
// through. `-H` points HOME at the agent's home. The shell is named by its
// ABSOLUTE path (agentLaunchShell): sudo resolves a bare command name against
// ITS environment's PATH, which is empty on the launch path (launchEnv carries
// no PATH) and not guaranteed by sudoers `secure_path` (macOS's default
// sudoers sets none) — a bare `bash` fails there with "sudo: bash: command not
// found". The absolute path needs no resolution and is the exact command the
// sudoers NOPASSWD rule is scoped to (the rule does not pin arguments).
func agentBashArgs(agentUser, snippet string) []string {
	return []string{
		"-u", agentUser, "-H", agentLaunchShell, "--noprofile", "--norc", "-c",
		"export PATH=" + shellQuote(agentProbePATH()) + "; " + snippet,
	}
}

// agentSystemPATH is the fixed PATH unconfined agent commands run with: the
// standard system binary directories every account's login PATH starts from.
const agentSystemPATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

// agentProbePATH is agentSystemPATH plus the world-traversable operator tool
// dirs (SharedBinPaths) that `jentic run` adds to the agent's profile right
// after provisioning — so a binary the agent reaches through them still probes
// as on-PATH, matching what the confined login shell will resolve.
func agentProbePATH() string {
	return strings.Join(append([]string{agentSystemPATH}, SharedBinPaths(OperatorHome())...), ":")
}

// agentCmd builds `sudo -u <user> -H bash --noprofile --norc -c <snippet>` with the working
// directory pinned to "/". Pinning is essential: the parent process's cwd is
// typically inside the operator's now-700 home, which the agent user cannot
// read — inheriting it makes bash spew `getcwd: Permission denied` before the
// snippet even runs. "/" is traversable by everyone.
//
// The environment is the curated launchEnv allowlist, NOT the operator's full
// environment — the same env the confined launch hands to sudo. This is what
// keeps the binary PROBE truthful: sudo's env_reset preserves the caller's
// PATH unless sudoers sets secure_path (macOS's default sudoers sets none), so
// an inherited environment leaks the OPERATOR's PATH into the agent's shell —
// `command -v <binary>` then resolves the operator's copy under the operator's
// (agent-unreachable) home, the provisioning flow is skipped as "already
// installed", and the launch (which does use the curated env) dies with
// `exec: <binary>: not found`. Probing with the launch's own environment makes
// the probe answer the question the launch will actually ask. It also stops
// operator-exported secrets from riding into agent-side commands.
func agentCmd(agentUser, snippet string) *exec.Cmd {
	cmd := exec.Command("sudo", agentBashArgs(agentUser, snippet)...) //nolint:gosec // agentUser is a config account name; snippet is shell-quoted / a fixed literal.
	cmd.Dir = "/"
	cmd.Env = launchEnv()
	return cmd
}

// agentCmdContext is agentCmd with a cancellation context. It carries the same
// curated launchEnv environment — see agentCmd for why the probe/launch envs
// must match.
func agentCmdContext(ctx context.Context, agentUser, snippet string) *exec.Cmd {
	cmd := exec.CommandContext(ctx, "sudo", agentBashArgs(agentUser, snippet)...) //nolint:gosec // agentUser is a config account name; snippet is shell-quoted.
	cmd.Dir = "/"
	cmd.Env = launchEnv()
	return cmd
}

// agentBashArgsNoLogin builds the sudo argv that runs snippet as agentUser in a
// NON-login bash (`bash -c`, no `-l`). It exists for the confined launch: the
// outer shell must source NO agent-owned rc, so no agent code runs before the
// confinement wrapper takes hold. The login shell (which sources rc, honouring the
// agent's ~/.local/bin PATH export) is run INSIDE the wrapper by confineExec, so
// rc is honoured but only under confinement. The shell is the ABSOLUTE
// agentLaunchShell for the same reason as agentBashArgs — the launch env carries
// no PATH for sudo to resolve a bare name with — and the sudoers passwordless
// rule is scoped to that command path (not its arguments), so login vs non-login
// makes no difference to what the rule matches.
func agentBashArgsNoLogin(agentUser, snippet string) []string {
	return []string{"-u", agentUser, "-H", agentLaunchShell, "-c", snippet}
}

// sensitiveEnvVars are environment variables that must never carry from the
// operator's session into the agent's: they hand the agent a live channel back to
// the operator's own credentials or agents. SSH_AUTH_SOCK / SSH_AGENT_PID would let
// the agent authenticate as the operator over SSH via the forwarded agent socket;
// the GPG equivalents do the same for signing/decryption. The confined launch
// unsets these in the snippet (see ConfineLaunchCmd) so the scrub does not depend
// on the machine's sudoers env_keep configuration.
var sensitiveEnvVars = []string{
	"SSH_AUTH_SOCK", "SSH_AGENT_PID", "SSH_CONNECTION", "SSH_CLIENT",
	"GPG_AGENT_INFO", "GPG_TTY",
}

// UnsetSensitiveEnvSnippet is a shell prefix that unsets every sensitiveEnvVar. It
// is prepended to the confined launch so the agent session starts with no inherited
// handle to the operator's SSH/GPG agents, regardless of how sudo's env_reset /
// env_keep is configured on this machine.
func UnsetSensitiveEnvSnippet() string {
	return "unset " + strings.Join(sensitiveEnvVars, " ") + " && "
}

// launchEnvAllowlist are the ONLY environment variable names the launch forwards
// from the operator to the sudo invocation — a defence-in-depth allowlist for the
// case where sudoers has env_reset disabled (env_reset normally rebuilds the
// environment itself). It is limited to what an interactive TUI agent genuinely
// needs: the terminal type and the locale/encoding. Everything else — including any
// operator secret exported into the shell — is dropped.
var launchEnvAllowlist = []string{
	"TERM", "COLORTERM", "LANG", "LC_ALL", "LC_CTYPE", "TERM_PROGRAM",
}

// launchEnv returns the curated environment the launch hands to sudo: only the
// allowlisted names that are actually set in the operator's environment. sudo with
// env_reset (the default) rebuilds HOME/USER/PATH/etc. for the target account
// itself, so this deliberately carries no PATH or HOME — just the terminal/locale
// hints — and serves as the backstop when env_reset is off.
func launchEnv() []string {
	var out []string
	for _, name := range launchEnvAllowlist {
		if v, ok := os.LookupEnv(name); ok {
			out = append(out, name+"="+v)
		}
	}
	return out
}

// agentCmdContextNoLogin is agentCmdContext with a NON-login outer shell — the
// launch path (see ConfineLaunchCmd). Working directory is pinned to "/" for the
// same reason as agentCmd: the parent cwd is typically inside the operator's home,
// unreadable by the agent uid. Its environment is the curated launchEnv allowlist,
// not the operator's full environment.
func agentCmdContextNoLogin(ctx context.Context, agentUser, snippet string) *exec.Cmd {
	cmd := exec.CommandContext(ctx, "sudo", agentBashArgsNoLogin(agentUser, snippet)...) //nolint:gosec // agentUser is a config account name; snippet is shell-quoted.
	cmd.Dir = "/"
	// Hand sudo only the curated allowlist, not the operator's full environment.
	// sudo's env_reset (default) rebuilds the target account's environment; this is
	// the backstop for a machine where env_reset is disabled. sudo is resolved from
	// the process PATH when the command was built, so an empty PATH here can't stop
	// it launching.
	cmd.Env = launchEnv()
	return cmd
}

// runAsAgent runs a shell snippet as the agent user (no startup files) and returns
// its error (nil on exit 0). Output is discarded; callers only need the verdict.
func runAsAgent(ctx context.Context, agentUser, snippet string) error {
	cmd := agentCmdContext(ctx, agentUser, snippet)
	cmd.Stdout = nil
	cmd.Stderr = nil
	return cmd.Run()
}

// quoteProbePath quotes a probe path for an agent-shell test, leaving a leading
// "~/" unquoted so the agent's shell expands it to the agent's home
// (single-quoting the whole string would make bash treat "~" literally).
func quoteProbePath(p string) string {
	if rest, ok := strings.CutPrefix(p, "~/"); ok {
		return "~/" + shellQuote(rest)
	}
	return shellQuote(p)
}

// shellQuote wraps s in single quotes, escaping any embedded single quotes, so
// it is safe to interpolate into a `bash -c` snippet.
func shellQuote(s string) string {
	return "'" + strings.ReplaceAll(s, "'", `'\''`) + "'"
}
