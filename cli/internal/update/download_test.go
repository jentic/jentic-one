package update

import (
	"archive/tar"
	"bytes"
	"compress/gzip"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"
)

// makeTarGz builds an in-memory .tar.gz containing one file (binaryName→content).
func makeTarGz(t *testing.T, binaryName, content string) []byte {
	t.Helper()
	var buf bytes.Buffer
	gz := gzip.NewWriter(&buf)
	tw := tar.NewWriter(gz)
	body := []byte(content)
	if err := tw.WriteHeader(&tar.Header{Name: binaryName, Mode: 0o755, Size: int64(len(body))}); err != nil {
		t.Fatal(err)
	}
	if _, err := tw.Write(body); err != nil {
		t.Fatal(err)
	}
	if err := tw.Close(); err != nil {
		t.Fatal(err)
	}
	if err := gz.Close(); err != nil {
		t.Fatal(err)
	}
	return buf.Bytes()
}

// fakeReleaseServer serves a release: the archive under its asset name and a
// checksums.txt whose row for that asset holds checksumOverride (or the real
// digest when empty). cosign sig/cert 404 so the no-cosign path is exercised.
func fakeReleaseServer(t *testing.T, asset string, archive []byte, checksumOverride string) *httptest.Server {
	t.Helper()
	sum := sha256.Sum256(archive)
	digest := hex.EncodeToString(sum[:])
	if checksumOverride != "" {
		digest = checksumOverride
	}
	checksums := fmt.Sprintf("%s  %s\n", digest, asset)
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case strings.HasSuffix(r.URL.Path, "/"+asset):
			_, _ = w.Write(archive)
		case strings.HasSuffix(r.URL.Path, "/checksums.txt"):
			_, _ = w.Write([]byte(checksums))
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
}

func TestDownloadAndVerify_HappyPath(t *testing.T) {
	// Force cosign-absent by pointing PATH at an empty dir so the test is
	// hermetic (a dev box with cosign would otherwise try the sig fetch → 404).
	t.Setenv("PATH", t.TempDir())
	asset := AssetName("jentic", "v0.31.0", "linux", "amd64")
	archive := makeTarGz(t, "jentic", "BINARY-CONTENT")
	srv := fakeReleaseServer(t, asset, archive, "")
	defer srv.Close()

	stage := t.TempDir()
	res, err := DownloadAndVerify(context.Background(), DownloadRequest{
		Repo:     repoFromURL(srv.URL),
		Tag:      "v0.31.0",
		Binary:   "jentic",
		GOOS:     "linux",
		GOARCH:   "amd64",
		Token:    "",
		StageDir: stage,
	})
	if err != nil {
		t.Fatalf("DownloadAndVerify: %v", err)
	}
	got, err := os.ReadFile(res.BinaryPath)
	if err != nil {
		t.Fatalf("read staged binary: %v", err)
	}
	if string(got) != "BINARY-CONTENT" {
		t.Errorf("staged binary content = %q", got)
	}
	if res.Warning == "" {
		t.Error("cosign-absent path should return a not-verified warning")
	}
}

func TestDownloadAndVerify_ChecksumMismatchAborts(t *testing.T) {
	t.Setenv("PATH", t.TempDir())
	asset := AssetName("jentic", "v0.31.0", "linux", "amd64")
	archive := makeTarGz(t, "jentic", "BINARY-CONTENT")
	// Serve a wrong digest → must fail closed.
	srv := fakeReleaseServer(t, asset, archive, strings.Repeat("0", 64))
	defer srv.Close()

	_, err := DownloadAndVerify(context.Background(), DownloadRequest{
		Repo:     repoFromURL(srv.URL),
		Tag:      "v0.31.0",
		Binary:   "jentic",
		GOOS:     "linux",
		GOARCH:   "amd64",
		Token:    "",
		StageDir: t.TempDir(),
	})
	if err == nil || !strings.Contains(err.Error(), "checksum mismatch") {
		t.Fatalf("want a checksum-mismatch abort, got %v", err)
	}
}

func TestVerifyChecksum_MissingEntryAborts(t *testing.T) {
	// A checksums file with no row for the asset must be a hard error, not a
	// vacuous pass (the --ignore-missing footgun).
	archive := []byte("hello")
	checksums := []byte("deadbeef  some_other_asset.tar.gz\n")
	if err := verifyChecksum("jentic_0.31.0_linux_amd64.tar.gz", archive, checksums); err == nil ||
		!strings.Contains(err.Error(), "no checksum entry") {
		t.Fatalf("missing entry should abort, got %v", err)
	}
}

// repoFromURL sets the release base URL to the httptest server and returns a
// dummy owner/name (the fake server matches on path suffix, so any repo works).
func repoFromURL(base string) string {
	releaseBaseURL = base
	return "o/n"
}

func writeCosignStub(t *testing.T, scriptBody string) string {
	t.Helper()
	dir := t.TempDir()
	p := dir + string(os.PathSeparator) + "cosign"
	if err := os.WriteFile(p, []byte("#!/bin/sh\n"+scriptBody+"\n"), 0o755); err != nil {
		t.Fatalf("write cosign stub: %v", err)
	}
	return dir
}

func TestDownloadAndVerify_CosignPresentSigMissingFailsClosed(t *testing.T) {
	cosignDir := writeCosignStub(t, "exit 1")
	t.Setenv("PATH", cosignDir)

	asset := AssetName("jentic", "v0.31.0", "linux", "amd64")
	archive := makeTarGz(t, "jentic", "BINARY-CONTENT")
	srv := fakeReleaseServer(t, asset, archive, "")
	defer srv.Close()

	_, err := DownloadAndVerify(context.Background(), DownloadRequest{
		Repo:     repoFromURL(srv.URL),
		Tag:      "v0.31.0",
		Binary:   "jentic",
		GOOS:     "linux",
		GOARCH:   "amd64",
		StageDir: t.TempDir(),
	})
	if err == nil {
		t.Fatal("expected error when cosign is on PATH and signature/cert return 404, got nil")
	}
	if !IsVerificationError(err) {
		t.Fatalf("expected verification error when signature/cert missing, got %v", err)
	}
	if !strings.Contains(err.Error(), "--allow-unsigned") {
		t.Errorf("error should mention --allow-unsigned opt-out, got %v", err)
	}
}

func TestDownloadAndVerify_AllowUnsignedOptOut(t *testing.T) {
	cosignDir := writeCosignStub(t, "exit 1")
	t.Setenv("PATH", cosignDir)

	asset := AssetName("jentic", "v0.31.0", "linux", "amd64")
	archive := makeTarGz(t, "jentic", "BINARY-CONTENT")
	srv := fakeReleaseServer(t, asset, archive, "")
	defer srv.Close()

	res, err := DownloadAndVerify(context.Background(), DownloadRequest{
		Repo:          repoFromURL(srv.URL),
		Tag:           "v0.31.0",
		Binary:        "jentic",
		GOOS:          "linux",
		GOARCH:        "amd64",
		StageDir:      t.TempDir(),
		AllowUnsigned: true,
	})
	if err != nil {
		t.Fatalf("DownloadAndVerify with AllowUnsigned=true: %v", err)
	}
	if res.Warning == "" {
		t.Error("expected warning when AllowUnsigned=true")
	}
}

func TestVerifyCosign_PinsExactTagIdentity(t *testing.T) {
	stage := t.TempDir()
	argLog := stage + string(os.PathSeparator) + "cosign.args"
	cosignDir := writeCosignStub(t, fmt.Sprintf("printf '%%s\\n' \"$@\" > %q\nexit 0", argLog))
	t.Setenv("PATH", cosignDir)

	warn, err := verifyCosign(context.Background(), "v1.2.3", []byte("checksums"), []byte("sig"), []byte("cert"), stage)
	if err != nil {
		t.Fatalf("verifyCosign: %v", err)
	}
	if warn != "" {
		t.Errorf("unexpected warning: %q", warn)
	}
	recorded, err := os.ReadFile(argLog)
	if err != nil {
		t.Fatalf("read cosign args: %v", err)
	}
	args := string(recorded)
	wantIdentity := "https://github.com/jentic/jentic-one/.github/workflows/release.yml@refs/tags/v1.2.3"
	if !strings.Contains(args, "--certificate-identity\n"+wantIdentity) {
		t.Fatalf("cosign argv did not pin exact tag identity %q; got:\n%s", wantIdentity, args)
	}
}

func TestDownloadAndVerify_RequireSignatureWithoutCosignFails(t *testing.T) {
	t.Setenv("PATH", t.TempDir())
	t.Setenv("JENTIC_REQUIRE_SIGNATURE", "1")

	asset := AssetName("jentic", "v0.31.0", "linux", "amd64")
	archive := makeTarGz(t, "jentic", "BINARY-CONTENT")
	srv := fakeReleaseServer(t, asset, archive, "")
	defer srv.Close()

	_, err := DownloadAndVerify(context.Background(), DownloadRequest{
		Repo:     repoFromURL(srv.URL),
		Tag:      "v0.31.0",
		Binary:   "jentic",
		GOOS:     "linux",
		GOARCH:   "amd64",
		StageDir: t.TempDir(),
	})
	if err == nil {
		t.Fatal("expected error when JENTIC_REQUIRE_SIGNATURE=1 and cosign is absent, got nil")
	}
	if !IsVerificationError(err) {
		t.Fatalf("expected verification error when JENTIC_REQUIRE_SIGNATURE=1 and cosign is absent, got %v", err)
	}
}
