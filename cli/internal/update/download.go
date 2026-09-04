package update

import (
	"archive/tar"
	"archive/zip"
	"compress/gzip"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strings"

	"github.com/jentic/jentic-one/cli/client"
)

// cosignCertIdentityRegexp / cosignOIDCIssuer pin the keyless-signing identity
// the release job uses (docs/development/releasing.md). Shared by the installer shell copy;
// keep the two in lockstep. The identity is the release workflow's OIDC subject.
const (
	cosignCertIdentityRegexp = `^https://github.com/jentic/jentic-one/\.github/workflows/release\.yml@refs/tags/`
	cosignOIDCIssuer         = "https://token.actions.githubusercontent.com"
)

// releaseBaseURL is the GitHub releases origin. Overridable in tests so the
// download path can be exercised against an httptest server without real HTTPS.
var releaseBaseURL = "https://github.com"

// releaseAssetURL builds the download URL for a named asset of a release tag.
func releaseAssetURL(repo, tag, asset string) string {
	return fmt.Sprintf("%s/%s/releases/download/%s/%s", releaseBaseURL, repo, tag, asset)
}

// httpGet fetches url (with an optional bearer token, matching FetchInstaller's
// private-repo auth) into a byte slice, failing closed on any non-200.
func httpGet(ctx context.Context, url, token string) ([]byte, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return nil, err
	}
	if token != "" {
		req.Header.Set("Authorization", "Bearer "+token)
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return nil, fmt.Errorf("download %s: %w", url, err)
	}
	defer func() { _ = resp.Body.Close() }()
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("download %s: unexpected status %s", url, resp.Status)
	}
	// Bound the read: checksums.txt / .sig / .pem are small metadata files, but a
	// hostile or misconfigured origin must not be able to stream an unbounded body
	// into RAM (review round-3 P0 / theme 2). The archive itself is streamed to
	// disk with its own larger io.Copy limit; this path only buffers metadata.
	return client.ReadAllBounded(resp.Body, client.MaxBodyBytes)
}

// verifyError marks a FAIL-CLOSED verification failure (checksum mismatch,
// missing checksum row, or a present-cosign signature failure) so callers can
// refuse any unverified fallback. IsVerificationError classifies it.
type verifyError struct{ err error }

func (e verifyError) Error() string { return e.err.Error() }
func (e verifyError) Unwrap() error { return e.err }

// IsVerificationError reports whether err is a fail-closed verification failure
// (as opposed to a transport/asset-missing error a caller may fall back from).
func IsVerificationError(err error) bool {
	var ve verifyError
	return errors.As(err, &ve)
}

// verifyChecksum enforces the sha256 gate FAIL-CLOSED: it finds the exact row
// for assetName in checksums.txt and compares it to the archive's digest.
// `sha256sum --check --ignore-missing` can pass vacuously ("no files checked" is
// not an error), so we grep the target line out explicitly and verify THAT one
// rather than relying on --ignore-missing. A missing row or a mismatch aborts.
func verifyChecksum(assetName string, archive, checksums []byte) error {
	sum := sha256.Sum256(archive)
	got := hex.EncodeToString(sum[:])
	var want string
	for _, line := range strings.Split(string(checksums), "\n") {
		fields := strings.Fields(line)
		if len(fields) != 2 {
			continue
		}
		// checksums.txt rows are "<sha256>  <name>"; the name may carry a
		// dist-relative path prefix, so match on the basename.
		if filepath.Base(fields[1]) == assetName {
			want = fields[0]
			break
		}
	}
	if want == "" {
		return verifyError{fmt.Errorf("no checksum entry for %q in checksums.txt — refusing to install unverified", assetName)}
	}
	if !strings.EqualFold(got, want) {
		return verifyError{fmt.Errorf("checksum mismatch for %q: got %s, want %s — refusing to install", assetName, got, want)}
	}
	return nil
}

// verifyCosign runs `cosign verify-blob` over checksums.txt using the pinned
// keyless identity, when cosign is on PATH. Absence of cosign is a loud WARNING
// (returned as warn), never a silent skip of the (already-enforced) sha256 gate.
// A present-cosign verification FAILURE is a hard error.
func verifyCosign(ctx context.Context, tag string, checksums, sig, cert []byte, stageDir string) (warn string, err error) {
	if !cosignAvailable() {
		return "signature not verified (install cosign to verify the release signature)", nil
	}
	// cosign verify-blob needs files on disk.
	write := func(name string, data []byte) (string, error) {
		p := filepath.Join(stageDir, name)
		return p, os.WriteFile(p, data, 0o600)
	}
	cksPath, err := write("checksums.txt", checksums)
	if err != nil {
		return "", err
	}
	sigPath, err := write("checksums.txt.sig", sig)
	if err != nil {
		return "", err
	}
	certPath, err := write("checksums.txt.pem", cert)
	if err != nil {
		return "", err
	}
	// The certificate identity embeds the release tag; the regexp is
	// tag-agnostic (matches any refs/tags/...) to avoid brittleness, matching
	// docs/development/releasing.md's runbook.
	_ = tag
	cmd := exec.CommandContext(ctx, "cosign", "verify-blob", //nolint:gosec // fixed argv; only file paths (under our stageDir) and pinned identity flags vary.
		"--certificate", certPath,
		"--signature", sigPath,
		"--certificate-identity-regexp", cosignCertIdentityRegexp,
		"--certificate-oidc-issuer", cosignOIDCIssuer,
		cksPath,
	)
	if out, cerr := cmd.CombinedOutput(); cerr != nil {
		return "", verifyError{fmt.Errorf("cosign verify-blob failed (release signature invalid: %s): %w", strings.TrimSpace(string(out)), cerr)}
	}
	return "", nil
}

// cosignAvailable reports whether the cosign binary is on PATH.
func cosignAvailable() bool {
	_, err := exec.LookPath("cosign")
	return err == nil
}

// DownloadResult is the outcome of a verified download: the extracted binary
// path in stageDir and any non-fatal warning (e.g. cosign absent).
type DownloadResult struct {
	BinaryPath string
	Warning    string
}

// DownloadAndVerify fetches the release archive for `binary` at `tag`, enforces
// the sha256 gate against the release checksums.txt (fail-closed), verifies the
// cosign signature when cosign is present (a bad signature aborts; absence
// warns), extracts the archive into stageDir, and returns the path to the
// extracted binary. It is the runtime analogue of the installer's download mode
// and shares the AssetName construction so the two never diverge.
func DownloadAndVerify(ctx context.Context, repo, tag, binary, goos, goarch, token, stageDir string) (*DownloadResult, error) {
	asset := AssetName(binary, tag, goos, goarch)
	archive, err := httpGet(ctx, releaseAssetURL(repo, tag, asset), token)
	if err != nil {
		return nil, err
	}
	checksums, err := httpGet(ctx, releaseAssetURL(repo, tag, "checksums.txt"), token)
	if err != nil {
		return nil, err
	}
	if err := verifyChecksum(asset, archive, checksums); err != nil {
		return nil, err
	}

	// cosign is best-effort-present: fetch the sig/cert and verify when cosign
	// exists. A fetch failure here is non-fatal (the sha256 gate already passed),
	// but a PRESENT-cosign verification failure is hard.
	var warn string
	if cosignAvailable() {
		sig, sigErr := httpGet(ctx, releaseAssetURL(repo, tag, "checksums.txt.sig"), token)
		cert, certErr := httpGet(ctx, releaseAssetURL(repo, tag, "checksums.txt.pem"), token)
		if sigErr != nil || certErr != nil {
			warn = "cosign is installed but the release signature/cert could not be fetched; verified sha256 only"
		} else if _, verr := verifyCosign(ctx, tag, checksums, sig, cert, stageDir); verr != nil {
			return nil, verr
		}
	} else {
		warn = "signature not verified (install cosign to verify the release signature)"
	}

	binPath, err := extractBinary(asset, archive, binary, goos, stageDir)
	if err != nil {
		return nil, err
	}
	return &DownloadResult{BinaryPath: binPath, Warning: warn}, nil
}

// extractBinary unpacks the single binary out of a .tar.gz (unix) or .zip
// (windows) archive into stageDir, returning its path. The windows binary
// carries a .exe suffix (goreleaser appends it).
func extractBinary(asset string, archive []byte, binary, goos string, stageDir string) (string, error) {
	want := binary
	if goos == "windows" {
		want = binary + ".exe"
	}
	if strings.HasSuffix(asset, ".zip") {
		return extractFromZip(archive, want, stageDir)
	}
	return extractFromTarGz(archive, want, stageDir)
}

func extractFromTarGz(archive []byte, want, stageDir string) (string, error) {
	gz, err := gzip.NewReader(strings.NewReader(string(archive)))
	if err != nil {
		return "", fmt.Errorf("open gzip: %w", err)
	}
	defer func() { _ = gz.Close() }()
	tr := tar.NewReader(gz)
	for {
		hdr, err := tr.Next()
		if err == io.EOF {
			break
		}
		if err != nil {
			return "", fmt.Errorf("read tar: %w", err)
		}
		if filepath.Base(hdr.Name) != want {
			continue
		}
		return writeStaged(want, tr, stageDir)
	}
	return "", fmt.Errorf("binary %q not found in archive", want)
}

func extractFromZip(archive []byte, want, stageDir string) (string, error) {
	zr, err := zip.NewReader(strings.NewReader(string(archive)), int64(len(archive)))
	if err != nil {
		return "", fmt.Errorf("open zip: %w", err)
	}
	for _, f := range zr.File {
		if filepath.Base(f.Name) != want {
			continue
		}
		rc, err := f.Open()
		if err != nil {
			return "", fmt.Errorf("open zip entry: %w", err)
		}
		defer func() { _ = rc.Close() }()
		return writeStaged(want, rc, stageDir)
	}
	return "", fmt.Errorf("binary %q not found in archive", want)
}

// writeStaged copies an archive entry to stageDir/<name> with exec perms,
// bounding the copy to guard against a decompression bomb.
func writeStaged(name string, r io.Reader, stageDir string) (string, error) {
	dst := filepath.Join(stageDir, name)
	out, err := os.OpenFile(dst, os.O_WRONLY|os.O_CREATE|os.O_TRUNC, 0o755) //nolint:gosec // staged CLI binary must be executable.
	if err != nil {
		return "", err
	}
	// 256 MiB ceiling: far above any real CLI binary, well below a bomb.
	if _, err := io.Copy(out, io.LimitReader(r, 256<<20)); err != nil {
		_ = out.Close()
		return "", err
	}
	if err := out.Close(); err != nil {
		return "", err
	}
	return dst, nil
}
