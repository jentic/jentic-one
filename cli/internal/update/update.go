// Package update backs `jentic update`: it inspects what is installed (via the
// manifest and build-time metadata) and compares its version against the latest
// release tag to report whether a newer build is available, then fetches and
// swaps in the rebuilt binaries.
package update

import (
	"context"
	"encoding/base64"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
)

// authArgs returns the `git -c http.extraheader=...` prefix carrying a Basic
// auth header for token, or nil when no token is set. It mirrors the auth
// scheme used by tools/install.sh so private repositories resolve.
func authArgs(token string) []string {
	if token == "" {
		return nil
	}
	basic := base64.StdEncoding.EncodeToString([]byte("x-access-token:" + token))
	return []string{"-c", "http.extraheader=Authorization: Basic " + basic}
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
