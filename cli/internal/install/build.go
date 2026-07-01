package install

import (
	"bytes"
	"encoding/base64"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
)

const (
	// GitURL is the public source cloned when `jenticctl install` runs outside a
	// jentic-one checkout.
	GitURL = "https://github.com/jentic/jentic-one.git"

	projectName = "jentic-one"
)

// SrcEnv lets you pin the source checkout `jenticctl install` builds from,
// overriding both the cwd repo-root walk and the GitHub clone fallback. Point
// it at a local jentic-one checkout to iterate on local changes from anywhere
// (and without a GITHUB_TOKEN): JENTIC_SRC=/path/to/jentic-one jenticctl install.
const SrcEnv = "JENTIC_SRC"

// RepoRoot walks up from the current directory looking for a jentic-one source
// checkout (a pyproject.toml that names the project, plus src/jentic_one). It
// returns the root and true when found. When $JENTIC_SRC is set it short-circuits
// to that path (validated as a real checkout) so the source is decoupled from
// the process's working directory.
func RepoRoot() (string, bool) {
	if env := strings.TrimSpace(os.Getenv(SrcEnv)); env != "" {
		if abs, err := filepath.Abs(env); err == nil && isRepoRoot(abs) {
			return abs, true
		}
	}
	dir, err := os.Getwd()
	if err != nil {
		return "", false
	}
	for {
		if isRepoRoot(dir) {
			return dir, true
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			return "", false
		}
		dir = parent
	}
}

func isRepoRoot(dir string) bool {
	if _, err := os.Stat(filepath.Join(dir, "src", "jentic_one")); err != nil {
		return false
	}
	data, err := os.ReadFile(filepath.Join(dir, "pyproject.toml")) //nolint:gosec // probing the working tree for a repo-root marker.
	if err != nil {
		return false
	}
	return strings.Contains(string(data), `name = "`+projectName+`"`)
}

// BuildPlan describes how the local virtualenv will be built.
type BuildPlan struct {
	// SourceDir is the source checkout to install from (the local repo root, or
	// the clone target when FromGit is true).
	SourceDir string
	// VenvDir is the virtualenv to create under ~/.jentic.
	VenvDir string
	// FromGit reports whether the source must be cloned from GitHub first.
	FromGit bool
	// GitURL is the clone source (set when FromGit is true).
	GitURL string
}

// PlanLocalBuild decides whether to build from a local checkout or to clone the
// source from GitHub first, based on whether the CLI runs inside the repo.
func PlanLocalBuild(venvDir, cloneDir string) BuildPlan {
	if root, ok := RepoRoot(); ok {
		return BuildPlan{SourceDir: root, VenvDir: venvDir}
	}
	return BuildPlan{SourceDir: cloneDir, VenvDir: venvDir, FromGit: true, GitURL: GitURL}
}

// VenvPython returns the python interpreter path inside the given venv dir.
func VenvPython(venvDir string) string {
	if runtime.GOOS == "windows" {
		return filepath.Join(venvDir, "Scripts", "python.exe")
	}
	return filepath.Join(venvDir, "bin", "python")
}

// VenvPython returns the python interpreter path inside this plan's venv.
func (p BuildPlan) VenvPython() string {
	return VenvPython(p.VenvDir)
}

// writeSourceLine appends the "source:" row shared by the local and Docker
// build headers: either a git clone target or the local checkout path.
func (p BuildPlan) writeSourceLine(b *strings.Builder) {
	if p.FromGit {
		b.WriteString("  source: " + commandStyle.Render("git clone "+p.GitURL) +
			" -> " + p.SourceDir + "\n")
	} else {
		b.WriteString("  source: " + commandStyle.Render(p.SourceDir) + " (local checkout)\n")
	}
}

// RenderHeader returns a styled description of what the build will do.
func (p BuildPlan) RenderHeader() string {
	var b strings.Builder
	b.WriteString(headingStyle.Render("Build (local environment)"))
	b.WriteString("\n")
	p.writeSourceLine(&b)
	b.WriteString("  venv:   " + commandStyle.Render(p.VenvDir) + "\n")
	return b.String()
}

// Execute performs the build, streaming command output to w. On success the
// caller should record VenvPython() into the Draft.
func (p BuildPlan) Execute(w io.Writer) error {
	if _, err := exec.LookPath("uv"); err != nil {
		return errors.New("`uv` is required to build the local environment but was not " +
			"found on PATH (install it: https://docs.astral.sh/uv/)")
	}

	if p.FromGit {
		if _, err := exec.LookPath("git"); err != nil {
			return errors.New("`git` is required to download the source but was not found on PATH")
		}
		if err := p.fetchSource(w); err != nil {
			return err
		}
	}

	if err := run(w, "uv", "venv", "--python", "3.12", p.VenvDir); err != nil {
		return fmt.Errorf("create venv: %w", err)
	}

	uiDir := filepath.Join(p.SourceDir, "ui")
	if _, err := os.Stat(filepath.Join(uiDir, "package.json")); err == nil {
		if _, err := exec.LookPath("npm"); err == nil {
			fmt.Fprint(w, mutedStyle.Render("  Building UI...\n"))
			if err := runDir(w, uiDir, "npm", "ci"); err != nil {
				return fmt.Errorf("npm ci: %w", err)
			}
			if err := runDir(w, uiDir, "npm", "run", "build"); err != nil {
				return fmt.Errorf("npm build: %w", err)
			}
			srcStatic := filepath.Join(p.SourceDir, "src", "jentic_one", "static")
			_ = os.RemoveAll(srcStatic)
			if err := copyDir(filepath.Join(uiDir, "dist"), srcStatic); err != nil {
				return fmt.Errorf("copy static assets: %w", err)
			}
		} else {
			fmt.Fprint(w, warnStyle.Render("  npm not found; skipping UI build (SPA will not be available)\n"))
		}
	}

	if err := run(w, "uv", "pip", "install", "--python", p.VenvPython(), "-e", p.SourceDir); err != nil {
		return fmt.Errorf("install %s from %s: %w", projectName, p.SourceDir, err)
	}
	return nil
}

// EnsureUv guarantees the uv build tool is available, installing it via the
// official installer when it is missing, and prepending its install location
// (~/.local/bin) to PATH so the build step in this same process can find it.
//
// It is best-effort: when uv cannot be bootstrapped (no curl, an unsupported
// platform, or an installer failure) it returns without changing anything and
// lets the normal preflight report uv as missing with an install hint.
func EnsureUv(w io.Writer) {
	if _, err := exec.LookPath("uv"); err == nil {
		return
	}

	fmt.Fprintln(w, headingStyle.Render("Install uv"))
	fmt.Fprintln(w, mutedStyle.Render("  uv not found; installing it from https://astral.sh/uv ..."))

	var cmd *exec.Cmd
	switch runtime.GOOS {
	case "windows":
		cmd = exec.Command("powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
			"-Command", "irm https://astral.sh/uv/install.ps1 | iex")
	default:
		if _, err := exec.LookPath("curl"); err != nil {
			fmt.Fprintln(w, warnStyle.Render("  curl not found; cannot auto-install uv — install it manually"))
			return
		}
		cmd = exec.Command("sh", "-c", "curl -LsSf https://astral.sh/uv/install.sh | sh")
	}
	cmd.Stdout = w
	cmd.Stderr = w
	if err := cmd.Run(); err != nil {
		fmt.Fprintln(w, warnStyle.Render("  uv install failed: "+err.Error()))
		return
	}

	// The installer drops uv in ~/.local/bin by default; make it discoverable
	// for the rest of this process (the build step looks uv up on PATH).
	if home, err := os.UserHomeDir(); err == nil {
		binDir := filepath.Join(home, ".local", "bin")
		_ = os.Setenv("PATH", binDir+string(os.PathListSeparator)+os.Getenv("PATH"))
	}
}

func (p BuildPlan) fetchSource(w io.Writer) error {
	// credential.helper= disables any inherited helper (keychain, GCM) so git
	// can't pop its own prompt; auth (if any) comes solely from the header below.
	common := append([]string{"-c", "credential.helper="}, gitAuthArgs()...)

	if isGitRepo(p.SourceDir) {
		// Sync to the remote's default branch by fetch + hard reset rather than
		// `pull --ff-only`. A fast-forward pull dead-ends ("Not possible to
		// fast-forward") whenever upstream history was rewritten/force-pushed —
		// e.g. after an OSS re-baseline of the published repo. $SourceDir is a
		// throwaway build checkout under ~/.jentic (never a tree the user edits),
		// so matching the remote exactly is the correct, always-succeeding sync.
		fetch := append(append([]string{}, common...), "fetch", "--prune", "origin")
		if err := runGit(w, p.SourceDir, fetch...); err != nil {
			return fmt.Errorf("fetch source: %w", err)
		}
		branch := remoteDefaultBranch(p.SourceDir)
		reset := append(append([]string{}, common...), "reset", "--hard", "origin/"+branch)
		if err := runGit(w, p.SourceDir, reset...); err != nil {
			return fmt.Errorf("sync source to origin/%s: %w", branch, err)
		}
		return nil
	}
	if err := os.MkdirAll(filepath.Dir(p.SourceDir), 0o700); err != nil {
		return fmt.Errorf("create source parent: %w", err)
	}

	args := append(append([]string{}, common...), "clone", "--depth", "1", p.GitURL, p.SourceDir)
	if err := runGit(w, "", args...); err != nil {
		if os.Getenv("GITHUB_TOKEN") == "" {
			return fmt.Errorf("clone failed — %s is likely private. To build from a local "+
				"checkout instead (no token needed), set %s=/path/to/jentic-one and re-run; "+
				"or set a token with 'repo' read scope: GITHUB_TOKEN=ghp_xxx jenticctl install: %w",
				p.GitURL, SrcEnv, err)
		}
		return fmt.Errorf("clone failed (check the ref and your token's access): %w", err)
	}
	return nil
}

// remoteDefaultBranch returns origin's default branch name (e.g. "main"),
// falling back to "main" when it can't be resolved. Lets the build checkout
// re-sync to whatever branch the remote publishes as HEAD.
func remoteDefaultBranch(dir string) string {
	//nolint:gosec // dir is a CLI-internal build checkout path.
	out, err := exec.Command("git", "-C", dir, "symbolic-ref", "--short", "refs/remotes/origin/HEAD").Output()
	if err == nil {
		if b := strings.TrimSpace(strings.TrimPrefix(strings.TrimSpace(string(out)), "origin/")); b != "" {
			return b
		}
	}
	return "main"
}

// gitAuthArgs returns a `git -c http.extraheader=...` prefix carrying a Basic
// auth header built from GITHUB_TOKEN, so cloning/pulling the source works for
// private or access-restricted repos. It returns nil when no token is set (anonymous access).
// Mirrors the scheme used by tools/install.sh.
func gitAuthArgs() []string {
	token := os.Getenv("GITHUB_TOKEN")
	if token == "" {
		return nil
	}
	basic := base64.StdEncoding.EncodeToString([]byte("x-access-token:" + token))
	return []string{"-c", "http.extraheader=Authorization: Basic " + basic}
}

// runGit runs a git subcommand like run, but (1) disables interactive credential
// prompts so a missing/invalid token fails fast instead of hanging on
// "Username for ...", and (2) redacts the auth header when echoing the command
// so the token never lands in the console output or logs.
func runGit(w io.Writer, dir string, args ...string) error {
	fmt.Fprintf(w, "\n$ git %s\n", strings.Join(redactGitArgs(args), " "))
	cmd := exec.Command("git", args...) //nolint:gosec // args are CLI-internal build commands, not user input.
	cmd.Dir = dir
	cmd.Stdout = w
	cmd.Stderr = w
	cmd.Env = append(os.Environ(), "GIT_TERMINAL_PROMPT=0", "GIT_ASKPASS=true", "GCM_INTERACTIVE=never")
	return cmd.Run()
}

// redactGitArgs returns a copy of args with any HTTP auth header value masked,
// so a printed command line never exposes the GITHUB_TOKEN.
func redactGitArgs(args []string) []string {
	out := make([]string, len(args))
	for i, a := range args {
		if strings.HasPrefix(a, "http.extraheader=Authorization:") {
			out[i] = "http.extraheader=Authorization: Basic ***"
		} else {
			out[i] = a
		}
	}
	return out
}

func isGitRepo(dir string) bool {
	info, err := os.Stat(filepath.Join(dir, ".git"))
	return err == nil && info.IsDir()
}

// runDir runs a command in the specified directory, streaming output to w.
func runDir(w io.Writer, dir, name string, args ...string) error {
	fmt.Fprintf(w, "\n[cd %s] $ %s %s\n", dir, name, strings.Join(args, " "))
	cmd := exec.Command(name, args...) //nolint:gosec // name/args are CLI-internal build commands (npm), not user input.
	cmd.Dir = dir
	cmd.Stdout = w
	cmd.Stderr = w
	return cmd.Run()
}

// copyDir recursively copies the src directory to dst. Both paths are
// CLI-internal build locations (npm's `ui/dist` output and the repo's packaged
// static dir), never user input, so the gosec file/path findings are waived.
func copyDir(src, dst string) error {
	return filepath.Walk(src, func(path string, info os.FileInfo, err error) error {
		if err != nil {
			return err
		}
		rel, err := filepath.Rel(src, path)
		if err != nil {
			return err
		}
		target := filepath.Join(dst, rel)
		if info.IsDir() {
			return os.MkdirAll(target, 0o750)
		}
		data, err := os.ReadFile(path) //nolint:gosec // path is under the CLI-internal build src dir, not user input.
		if err != nil {
			return err
		}
		return os.WriteFile(target, data, info.Mode()) //nolint:gosec // target is under the CLI-internal build dst dir, not user input.
	})
}

func run(w io.Writer, name string, args ...string) error {
	fmt.Fprintf(w, "\n$ %s %s\n", name, strings.Join(args, " "))
	cmd := exec.Command(name, args...) //nolint:gosec // name/args are CLI-internal build commands (uv, git), not user input.
	cmd.Stdout = w
	cmd.Stderr = w
	return cmd.Run()
}

// runCapture runs a `docker` subcommand like run, but also captures combined
// stdout+stderr into the returned string (teeing to w so the operator still sees
// live output). The capture lets the build path detect a transient BuildKit
// crash signature in the output and recover (#653).
func runCapture(w io.Writer, dir string, args ...string) (string, error) {
	fmt.Fprintf(w, "\n$ docker %s\n", strings.Join(args, " "))
	var buf bytes.Buffer
	tee := io.MultiWriter(w, &buf)
	cmd := exec.Command("docker", args...) //nolint:gosec // args are CLI-internal build commands, not user input.
	cmd.Dir = dir
	cmd.Stdout = tee
	cmd.Stderr = tee
	err := cmd.Run()
	return buf.String(), err
}

// RenderMigrateHeader returns a styled header for the migration step.
func RenderMigrateHeader(configPath string) string {
	return headingStyle.Render("Run migrations") + "\n  config: " +
		commandStyle.Render(configPath) + "\n"
}

// RenderMigrateWarning returns a styled warning shown when migrations could not
// be applied (typically Postgres not yet running).
func RenderMigrateWarning(err error) string {
	return warnStyle.Render("Migrations were not applied: "+err.Error()) + "\n" +
		mutedStyle.Render("Start your database, then run the migrate command in the next steps.")
}

// RenderStartHeader returns a styled header for the background-start step.
func RenderStartHeader() string {
	return headingStyle.Render("Start app (background)")
}

// RenderStartWarning returns a styled warning shown when the app could not be
// started in the background; the install otherwise succeeded.
func RenderStartWarning(err error) string {
	return warnStyle.Render("Could not start the app: "+err.Error()) + "\n" +
		mutedStyle.Render("Start it manually with the command in the next steps.")
}

// RunMigrations applies Alembic migrations for all databases using the freshly
// built venv interpreter, pointed at the generated config. Output is streamed to
// w. The runner is cwd-independent (it loads packaged migration scripts).
func RunMigrations(w io.Writer, venvPython, configPath string) error {
	fmt.Fprintf(w, "\n$ JENTIC_CONFIG_FILE=%s %s -m jentic_one.migrations.run\n",
		configPath, venvPython)
	cmd := exec.Command(venvPython, "-m", "jentic_one.migrations.run")
	cmd.Env = append(os.Environ(), "JENTIC_CONFIG_FILE="+configPath)
	cmd.Stdout = w
	cmd.Stderr = w
	return cmd.Run()
}
