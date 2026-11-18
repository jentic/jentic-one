package atomicfile

import (
	"bytes"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"sync"
	"testing"
)

func requireUnixPerms(t *testing.T) {
	t.Helper()
	if runtime.GOOS == "windows" {
		t.Skip("POSIX permission bits are not enforced on Windows")
	}
}

func assertNoTempResidue(t *testing.T, dir string) {
	t.Helper()
	entries, err := os.ReadDir(dir)
	if err != nil {
		t.Fatalf("ReadDir(%s): %v", dir, err)
	}
	for _, e := range entries {
		if strings.Contains(e.Name(), ".tmp-") {
			t.Errorf("temp file residue left behind in %s: %s", dir, e.Name())
		}
	}
}

func TestWrite_ModesAndPermissions(t *testing.T) {
	requireUnixPerms(t)

	cases := []struct {
		name        string
		seedPerm    os.FileMode // 0 means do not pre-create
		opts        Options
		wantPerm    os.FileMode
		payload     []byte
		wantContent []byte
	}{
		{
			name:        "new file gets Perm",
			opts:        Options{Perm: 0o600},
			wantPerm:    0o600,
			payload:     []byte("secret"),
			wantContent: []byte("secret"),
		},
		{
			name:        "zero-length write",
			opts:        Options{Perm: 0o600},
			wantPerm:    0o600,
			payload:     nil,
			wantContent: []byte{},
		},
		{
			name:        "existing 0640 file with PreserveMode stays 0640",
			seedPerm:    0o640,
			opts:        Options{Perm: 0o600, PreserveMode: true},
			wantPerm:    0o640,
			payload:     []byte("updated"),
			wantContent: []byte("updated"),
		},
		{
			name:        "existing 0640 file without PreserveMode is forced to Perm",
			seedPerm:    0o640,
			opts:        Options{Perm: 0o600, PreserveMode: false},
			wantPerm:    0o600,
			payload:     []byte("forced"),
			wantContent: []byte("forced"),
		},
		{
			name:        "SyncDir option writes and syncs cleanly",
			opts:        Options{Perm: 0o600, SyncDir: true},
			wantPerm:    0o600,
			payload:     []byte("synced"),
			wantContent: []byte("synced"),
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			dir := t.TempDir()
			target := filepath.Join(dir, "target.txt")
			if tc.seedPerm != 0 {
				if err := os.WriteFile(target, []byte("initial"), tc.seedPerm); err != nil {
					t.Fatalf("seed file: %v", err)
				}
				if err := os.Chmod(target, tc.seedPerm); err != nil {
					t.Fatalf("chmod seed: %v", err)
				}
			}

			if err := Write(target, tc.payload, tc.opts); err != nil {
				t.Fatalf("Write: %v", err)
			}

			got, err := os.ReadFile(target)
			if err != nil {
				t.Fatalf("ReadFile: %v", err)
			}
			if !bytes.Equal(got, tc.wantContent) {
				t.Errorf("content = %q, want %q", got, tc.wantContent)
			}

			info, err := os.Stat(target)
			if err != nil {
				t.Fatalf("Stat: %v", err)
			}
			if perm := info.Mode().Perm(); perm != tc.wantPerm {
				t.Errorf("perm = %o, want %o", perm, tc.wantPerm)
			}
			assertNoTempResidue(t, dir)
		})
	}
}

func TestWrite_SymlinkTargetReplacedAndTargetUntouched(t *testing.T) {
	requireUnixPerms(t)
	dir := t.TempDir()
	victim := filepath.Join(dir, "victim.txt")
	if err := os.WriteFile(victim, []byte("do-not-touch"), 0o640); err != nil {
		t.Fatalf("seed victim: %v", err)
	}

	link := filepath.Join(dir, "link.txt")
	if err := os.Symlink(victim, link); err != nil {
		t.Fatalf("symlink: %v", err)
	}

	if err := Write(link, []byte("replacement"), Options{Perm: 0o600, PreserveMode: true}); err != nil {
		t.Fatalf("Write over symlink: %v", err)
	}

	linkInfo, err := os.Lstat(link)
	if err != nil {
		t.Fatalf("Lstat(link): %v", err)
	}
	if !linkInfo.Mode().IsRegular() {
		t.Errorf("link mode = %v, want regular file", linkInfo.Mode())
	}
	gotLink, err := os.ReadFile(link)
	if err != nil {
		t.Fatalf("ReadFile(link): %v", err)
	}
	if string(gotLink) != "replacement" {
		t.Errorf("link content = %q, want replacement", gotLink)
	}

	gotVictim, err := os.ReadFile(victim)
	if err != nil {
		t.Fatalf("ReadFile(victim): %v", err)
	}
	if string(gotVictim) != "do-not-touch" {
		t.Errorf("symlink target was modified: %q", gotVictim)
	}
	assertNoTempResidue(t, dir)
}

func TestWrite_ReadOnlyDirErrorsWithNoResidue(t *testing.T) {
	requireUnixPerms(t)
	if os.Geteuid() == 0 {
		t.Skip("root bypasses read-only directory permissions")
	}

	dir := t.TempDir()
	if err := os.Chmod(dir, 0o500); err != nil {
		t.Fatalf("chmod dir: %v", err)
	}
	t.Cleanup(func() { _ = os.Chmod(dir, 0o700) })

	target := filepath.Join(dir, "file.txt")
	if err := Write(target, []byte("data"), Options{Perm: 0o600}); err == nil {
		t.Fatal("expected error writing to read-only directory")
	}
	assertNoTempResidue(t, dir)
}

func TestWrite_RenameOntoDirectoryFailsWithNoResidue(t *testing.T) {
	dir := t.TempDir()
	target := filepath.Join(dir, "is-a-dir")
	if err := os.Mkdir(target, 0o700); err != nil {
		t.Fatalf("mkdir target: %v", err)
	}

	if err := Write(target, []byte("data"), Options{Perm: 0o600}); err == nil {
		t.Fatal("expected error renaming temp file onto an existing directory")
	}
	assertNoTempResidue(t, dir)
}

func TestWrite_ConcurrentWritersNeverTear(t *testing.T) {
	dir := t.TempDir()
	target := filepath.Join(dir, "concurrent.txt")

	payloadA := bytes.Repeat([]byte("A"), 4096)
	payloadB := bytes.Repeat([]byte("B"), 4096)

	var wg sync.WaitGroup
	start := make(chan struct{})
	for i := range 2 {
		payload := payloadA
		if i == 1 {
			payload = payloadB
		}
		wg.Add(1)
		go func(data []byte) {
			defer wg.Done()
			<-start
			for range 100 {
				if err := Write(target, data, Options{Perm: 0o600}); err != nil {
					t.Errorf("concurrent Write: %v", err)
					return
				}
			}
		}(payload)
	}
	close(start)
	wg.Wait()

	got, err := os.ReadFile(target)
	if err != nil {
		t.Fatalf("ReadFile: %v", err)
	}
	if !bytes.Equal(got, payloadA) && !bytes.Equal(got, payloadB) {
		t.Fatalf("file content is torn (len=%d)", len(got))
	}
	assertNoTempResidue(t, dir)
}

func TestWrite_MkdirPermCreatesParents(t *testing.T) {
	requireUnixPerms(t)
	base := t.TempDir()
	parent := filepath.Join(base, "nested", "sub")
	target := filepath.Join(parent, "cfg.json")

	if err := Write(target, []byte("{}"), Options{Perm: 0o600, MkdirPerm: 0o750}); err != nil {
		t.Fatalf("Write with MkdirPerm: %v", err)
	}

	info, err := os.Stat(parent)
	if err != nil {
		t.Fatalf("Stat(parent): %v", err)
	}
	if !info.IsDir() {
		t.Fatalf("parent %s is not a directory", parent)
	}
	if perm := info.Mode().Perm(); perm != 0o750 {
		t.Errorf("parent perm = %o, want 750", perm)
	}

	fileInfo, err := os.Stat(target)
	if err != nil {
		t.Fatalf("Stat(target): %v", err)
	}
	if perm := fileInfo.Mode().Perm(); perm != 0o600 {
		t.Errorf("file perm = %o, want 600", perm)
	}
	assertNoTempResidue(t, parent)
}
