package egressproxy

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"
)

// Files the proxy publishes to the session's CA volume, which the agent
// container mounts read-only. Nothing else is ever written there; the CA's
// private key in particular never leaves the proxy's memory.
const (
	// CAFile is the session CA certificate (absent when no vendor is governed).
	CAFile = "ca.pem"
	// BundleFile is the system roots followed by the session CA: the file the
	// agent's TLS clients are pointed at.
	BundleFile = "bundle.pem"
	// ReadyFile is written last: the session's readiness nonce and the
	// digests of the two certificate files.
	ReadyFile = "ready"
)

var nonceRe = regexp.MustCompile(`^[a-f0-9]{32,128}$`)

// ValidNonce reports whether s has the shape of a readiness nonce (hex).
func ValidNonce(s string) bool { return nonceRe.MatchString(s) }

// Publish writes the CA files and then the ready file to dir. ca may be nil
// (no governed vendor): the bundle is then the system roots alone and no CA
// file is written. systemRoots is the PEM bundle of the image's trusted roots.
func Publish(dir string, ca *CA, systemRoots []byte, nonce string) error {
	if !ValidNonce(nonce) {
		return errors.New("invalid readiness nonce")
	}
	if !bytes.Contains(systemRoots, []byte("-----BEGIN CERTIFICATE-----")) {
		return errors.New("the system root bundle holds no certificate")
	}
	bundle := append(bytes.TrimRight(systemRoots, "\n"), '\n')
	caDigest := "-"
	if ca != nil {
		pemBytes := ca.CertPEM()
		bundle = append(bundle, pemBytes...)
		if err := writeAtomic(dir, CAFile, pemBytes); err != nil {
			return err
		}
		caDigest = digest(pemBytes)
	} else if err := os.Remove(filepath.Join(dir, CAFile)); err != nil && !errors.Is(err, os.ErrNotExist) {
		return err
	}
	if err := writeAtomic(dir, BundleFile, bundle); err != nil {
		return err
	}
	ready := nonce + "\n" + digest(bundle) + "\n" + caDigest + "\n"
	return writeAtomic(dir, ReadyFile, []byte(ready))
}

// CheckReady verifies that dir holds the files Publish wrote for this nonce:
// the ready file names the nonce, and the certificate files on disk match
// the digests it records. A CA published by any other session (a stale
// volume, a swapped file) fails.
func CheckReady(dir, nonce string) error {
	if !ValidNonce(nonce) {
		return errors.New("invalid readiness nonce")
	}
	ready, err := os.ReadFile(filepath.Join(dir, ReadyFile)) //nolint:gosec // G304: the proxy's own CA directory.
	if err != nil {
		return fmt.Errorf("the proxy has not published its CA yet: %w", err)
	}
	lines := strings.Split(strings.TrimRight(string(ready), "\n"), "\n")
	if len(lines) != 3 {
		return errors.New("malformed ready file")
	}
	if lines[0] != nonce {
		return errors.New("the published CA belongs to another session (readiness nonce mismatch)")
	}
	bundle, err := os.ReadFile(filepath.Join(dir, BundleFile)) //nolint:gosec // G304: the proxy's own CA directory.
	if err != nil {
		return err
	}
	if digest(bundle) != lines[1] {
		return errors.New("the published CA bundle does not match the ready file")
	}
	caPEM, err := os.ReadFile(filepath.Join(dir, CAFile)) //nolint:gosec // G304: the proxy's own CA directory.
	switch {
	case lines[2] == "-":
		if err == nil {
			return errors.New("a CA file is published but the ready file records none")
		}
	case err != nil:
		return err
	case digest(caPEM) != lines[2]:
		return errors.New("the published CA does not match the ready file")
	}
	return nil
}

func digest(b []byte) string {
	sum := sha256.Sum256(b)
	return "sha256:" + hex.EncodeToString(sum[:])
}

// writeAtomic writes name in dir world-readable (the agent reads it through a
// read-only mount) via a temp file and a rename.
func writeAtomic(dir, name string, data []byte) error {
	tmp, err := os.CreateTemp(dir, "."+name+"-*")
	if err != nil {
		return err
	}
	defer os.Remove(tmp.Name())
	if err := tmp.Chmod(0o644); err != nil {
		_ = tmp.Close()
		return err
	}
	if _, err := tmp.Write(data); err != nil {
		_ = tmp.Close()
		return err
	}
	if err := tmp.Close(); err != nil {
		return err
	}
	return os.Rename(tmp.Name(), filepath.Join(dir, name))
}
