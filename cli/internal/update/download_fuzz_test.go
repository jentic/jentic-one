package update

import (
	"archive/tar"
	"archive/zip"
	"bytes"
	"compress/gzip"
	"crypto/sha256"
	"encoding/hex"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func seedTarGzArchive(entries map[string]string) []byte {
	var buf bytes.Buffer
	gz := gzip.NewWriter(&buf)
	tw := tar.NewWriter(gz)
	for name, body := range entries {
		data := []byte(body)
		_ = tw.WriteHeader(&tar.Header{Name: name, Mode: 0o755, Size: int64(len(data))})
		_, _ = tw.Write(data)
	}
	_ = tw.Close()
	_ = gz.Close()
	return buf.Bytes()
}

func seedZipArchive(entries map[string]string) []byte {
	var buf bytes.Buffer
	zw := zip.NewWriter(&buf)
	for name, body := range entries {
		w, err := zw.Create(name)
		if err != nil {
			continue
		}
		_, _ = w.Write([]byte(body))
	}
	_ = zw.Close()
	return buf.Bytes()
}

func FuzzExtractBinary(f *testing.F) {
	f.Add(seedTarGzArchive(map[string]string{"jentic": "BINARY-CONTENT"}), false)
	f.Add(seedTarGzArchive(map[string]string{"dist/jentic": "BINARY-CONTENT", "README.md": "readme"}), false)
	f.Add(seedTarGzArchive(map[string]string{"../evil": "pwn", "other": "nope"}), false)
	f.Add(seedZipArchive(map[string]string{"jentic": "BINARY-CONTENT"}), true)
	f.Add(seedZipArchive(map[string]string{"dist/jentic": "BINARY-CONTENT", "README.md": "readme"}), true)
	f.Add(seedZipArchive(map[string]string{"../evil": "pwn", "other": "nope"}), true)
	f.Add([]byte{}, false)
	f.Add([]byte("not an archive"), true)

	f.Fuzz(func(t *testing.T, archive []byte, isZip bool) {
		stageDir := t.TempDir()
		const want = "jentic"
		asset := "jentic_0.31.0_linux_amd64.tar.gz"
		if isZip {
			asset = "jentic_0.31.0_linux_amd64.zip"
		}

		gotPath, err := extractBinary(asset, archive, want, "linux", stageDir)
		entries, readErr := os.ReadDir(stageDir)
		if readErr != nil {
			t.Fatalf("ReadDir(stageDir): %v", readErr)
		}

		if err != nil {
			for _, e := range entries {
				if e.Name() != want {
					t.Fatalf("failed extractBinary created unexpected file %q in stageDir", e.Name())
				}
			}
			return
		}

		wantPath := filepath.Join(stageDir, want)
		if gotPath != wantPath {
			t.Fatalf("extractBinary path = %q, want %q", gotPath, wantPath)
		}
		if len(entries) != 1 || entries[0].Name() != want {
			t.Fatalf("stageDir entries = %v, want only [%q]", entries, want)
		}
		info, statErr := os.Stat(gotPath)
		if statErr != nil {
			t.Fatalf("Stat(%q): %v", gotPath, statErr)
		}
		if info.Size() > 256<<20 {
			t.Fatalf("extracted binary size = %d, exceeds 256 MiB ceiling", info.Size())
		}
	})
}

func FuzzVerifyChecksum(f *testing.F) {
	const asset = "jentic_0.31.0_linux_amd64.tar.gz"
	archive := []byte("BINARY-CONTENT")
	sum := sha256.Sum256(archive)
	digest := hex.EncodeToString(sum[:])

	f.Add(digest + "  " + asset + "\n")
	f.Add(strings.ToUpper(digest) + "  dist/" + asset + "\n")
	f.Add("deadbeef  other.tar.gz\n" + digest + "  " + asset + "\n")
	f.Add("deadbeef  " + asset + "\n")
	f.Add("deadbeef  other.tar.gz\n")
	f.Add("")
	f.Add(digest + "  ../" + asset + "  extra\n")

	f.Fuzz(func(t *testing.T, checksums string) {
		err := verifyChecksum(asset, archive, []byte(checksums))
		if err != nil {
			if !IsVerificationError(err) {
				t.Fatalf("verifyChecksum error %v is not classified as a verification error", err)
			}
			return
		}

		matched := false
		for _, line := range strings.Split(checksums, "\n") {
			fields := strings.Fields(line)
			if len(fields) != 2 {
				continue
			}
			if filepath.Base(fields[1]) == asset {
				matched = strings.EqualFold(fields[0], digest)
				break
			}
		}
		if !matched {
			t.Fatalf("verifyChecksum succeeded for %q without a matching first row for %q (digest %s)", checksums, asset, digest)
		}
	})
}
