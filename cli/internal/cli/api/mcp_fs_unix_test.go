//go:build unix

package api

// mcp_fs_unix_test.go pins the token-file ownership assertion (F9): a file
// owned by a foreign uid is refused even at mode 0600 — creating a real
// foreign-owned file needs root, so the check is pinned against a fake
// FileInfo carrying the unix stat.

import (
	"os"
	"strings"
	"syscall"
	"testing"
)

// fakeOwnedFileInfo satisfies os.FileInfo just enough for fileOwnerUID: only
// Sys() is consulted.
type fakeOwnedFileInfo struct {
	os.FileInfo
	uid uint32
}

func (f fakeOwnedFileInfo) Sys() any { return &syscall.Stat_t{Uid: f.uid} }

func TestCheckTokenFileOwner(t *testing.T) {
	self := uint32(os.Getuid()) // getuid is non-negative on unix

	for _, uid := range []uint32{self, 0} {
		if err := checkTokenFileOwner("/tmp/t", fakeOwnedFileInfo{uid: uid}); err != nil {
			t.Errorf("uid %d (self or root) must pass, got %v", uid, err)
		}
	}

	err := checkTokenFileOwner("/tmp/t", fakeOwnedFileInfo{uid: self + 12345})
	if err == nil {
		t.Fatal("a foreign-owned token file must be refused")
	}
	if !strings.Contains(err.Error(), "owned by uid") {
		t.Errorf("refusal must name the foreign owner, got %q", err)
	}
}
