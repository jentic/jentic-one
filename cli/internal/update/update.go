// Package update backs `jentic update`: it inspects what is installed (via the
// manifest and build-time metadata) and compares it against the tracked git ref
// to report whether a newer build is available.
package update

import (
	"context"
	"encoding/base64"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strings"
)

// shortLen is the number of SHA characters compared/displayed, matching the
// `git rev-parse --short` length stamped into the binary by the installer.
const shortLen = 7

// RemoteCommit resolves the current commit of ref in repo using `git ls-remote`,
// returning the short SHA. A token (GitHub PAT) is sent as an HTTP Basic auth
// header so private or access-restricted repositories resolve; it mirrors
// the auth scheme used by tools/install.sh. An empty token queries anonymously.
//
// Releases are tagged with a `v` prefix (refs/tags/vX.Y.Z), but the installed
// build tracks a bare-semver ref (e.g. "0.15.0") stamped from the version. So
// for a bare-semver ref we also try the fully-qualified release tag; the first
// candidate that resolves wins. See candidateRefs.
func RemoteCommit(ctx context.Context, repo, ref, token string) (string, error) {
	if repo == "" {
		return "", errors.New("no repository to check")
	}
	if ref == "" {
		return "", errors.New("no ref to check")
	}
	if _, err := exec.LookPath("git"); err != nil {
		return "", errors.New("`git` is required to check for updates but was not found on PATH")
	}

	var lastErr error
	for _, cand := range candidateRefs(ref) {
		sha, err := lsRemote(ctx, repo, cand, token)
		if err != nil {
			lastErr = err
			continue
		}
		if sha != "" {
			return short(sha), nil
		}
	}
	if lastErr != nil {
		return "", lastErr
	}
	return "", fmt.Errorf("ref %q not found in %s", ref, repo)
}

// candidateRefs returns the refs to try, in order, when resolving ref. It always
// starts with ref as-given (covering branches like "main", full SHAs, and
// already-`v`-prefixed tags). When ref is a bare semver (starts with a digit and
// contains a dot), it also appends the fully-qualified release tag
// "refs/tags/v<ref>" — fully-qualified so `git ls-remote` matches only the
// canonical release tag and not a similarly-suffixed one (e.g. cli/v0.15.0).
func candidateRefs(ref string) []string {
	cands := []string{ref}
	if bareSemver.MatchString(ref) {
		cands = append(cands, "refs/tags/v"+ref)
	}
	return cands
}

// bareSemver matches a version without the `v` prefix, e.g. "0.15.0".
var bareSemver = regexp.MustCompile(`^\d+\.\d+`)

// lsRemote runs `git ls-remote <url> <ref>` and returns the first matching SHA
// (empty if the ref does not resolve). credential.helper= disables any inherited
// helper so git can't prompt.
func lsRemote(ctx context.Context, repo, ref, token string) (string, error) {
	url := "https://github.com/" + repo + ".git"
	args := append([]string{"-c", "credential.helper="}, authArgs(token)...)
	args = append(args, "ls-remote", url, ref)

	cmd := exec.CommandContext(ctx, "git", args...) //nolint:gosec // args are CLI-internal; repo/ref come from the manifest/flags.
	// Never fall back to an interactive prompt: a missing/invalid token must
	// fail fast rather than block waiting for a username on the terminal.
	cmd.Env = append(os.Environ(), "GIT_TERMINAL_PROMPT=0", "GIT_ASKPASS=true", "GCM_INTERACTIVE=never")
	out, err := cmd.Output()
	if err != nil {
		return "", fmt.Errorf("git ls-remote failed (check the ref %q and, for a private repo, GITHUB_TOKEN): %w", ref, err)
	}
	return firstSHA(string(out)), nil
}

// authArgs returns the `git -c http.extraheader=...` prefix carrying a Basic
// auth header for token, or nil when no token is set.
func authArgs(token string) []string {
	if token == "" {
		return nil
	}
	basic := base64.StdEncoding.EncodeToString([]byte("x-access-token:" + token))
	return []string{"-c", "http.extraheader=Authorization: Basic " + basic}
}

// firstSHA returns the SHA from the first non-empty `git ls-remote` line, whose
// format is "<sha>\t<refname>".
func firstSHA(out string) string {
	for _, line := range strings.Split(out, "\n") {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		fields := strings.Fields(line)
		if len(fields) > 0 {
			return fields[0]
		}
	}
	return ""
}

// short truncates a full SHA to the comparison/display length.
func short(sha string) string {
	if len(sha) > shortLen {
		return sha[:shortLen]
	}
	return sha
}

// FetchInstaller downloads tools/install.sh for ref from repo's raw content,
// authenticating with a bearer token when one is given (required while the repo
// is private). The script is returned verbatim so the caller can run it.
func FetchInstaller(ctx context.Context, repo, ref, token string) ([]byte, error) {
	url := fmt.Sprintf("https://raw.githubusercontent.com/%s/%s/tools/install.sh", repo, ref)
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return nil, err
	}
	if token != "" {
		req.Header.Set("Authorization", "Bearer "+token)
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return nil, fmt.Errorf("download installer: %w", err)
	}
	defer func() { _ = resp.Body.Close() }()
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("download installer: unexpected status %s (check the ref %q and, for a private repo, GITHUB_TOKEN)", resp.Status, ref)
	}
	return io.ReadAll(io.LimitReader(resp.Body, 1<<20))
}

// ReplaceBinary atomically swaps the file at target with the freshly built
// binary at staged, after backing up the current target to "<target>.bak".
// staged is copied into target's directory first so the final rename is on the
// same filesystem (atomic), avoiding a cross-device rename error when the build
// was staged under a temp dir. Returns the backup path (empty if target did not
// previously exist).
func ReplaceBinary(target, staged string) (string, error) {
	dir := filepath.Dir(target)
	if err := os.MkdirAll(dir, 0o755); err != nil { //nolint:gosec // bin dir is conventionally world-readable.
		return "", err
	}

	tmp := filepath.Join(dir, ".jentic.new")
	if err := copyFile(staged, tmp, 0o755); err != nil {
		return "", fmt.Errorf("stage new binary: %w", err)
	}

	var backup string
	if _, err := os.Stat(target); err == nil {
		backup = target + ".bak"
		if err := copyFile(target, backup, 0o755); err != nil {
			_ = os.Remove(tmp)
			return "", fmt.Errorf("back up current binary: %w", err)
		}
	}

	// Rename over the (possibly running) target: on Linux/macOS this replaces the
	// directory entry while the running process keeps its old inode.
	if err := os.Rename(tmp, target); err != nil {
		_ = os.Remove(tmp)
		return backup, fmt.Errorf("install new binary: %w", err)
	}
	return backup, nil
}

func copyFile(src, dst string, mode os.FileMode) error {
	in, err := os.Open(src) //nolint:gosec // paths are CLI-internal (staged build artifact / install location).
	if err != nil {
		return err
	}
	defer func() { _ = in.Close() }()

	out, err := os.OpenFile(dst, os.O_WRONLY|os.O_CREATE|os.O_TRUNC, mode) //nolint:gosec // see above.
	if err != nil {
		return err
	}
	if _, err := io.Copy(out, in); err != nil {
		_ = out.Close()
		return err
	}
	if err := out.Chmod(mode); err != nil {
		_ = out.Close()
		return err
	}
	return out.Close()
}

// SameCommit reports whether two (possibly differently-truncated) SHAs refer to
// the same commit, comparing on the shorter common prefix length.
func SameCommit(a, b string) bool {
	a = strings.TrimSpace(a)
	b = strings.TrimSpace(b)
	if a == "" || b == "" {
		return false
	}
	n := len(a)
	if len(b) < n {
		n = len(b)
	}
	return strings.EqualFold(a[:n], b[:n])
}
