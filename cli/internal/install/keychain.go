// keychain.go — macOS Keychain storage for the credentials encryption key.
//
// `jenticctl install --keychain` moves the AES key OUT of the generated
// jentic-one.yaml: the material is stored as a Keychain generic-password item
// and the config carries only a `material_keychain: <service>` reference,
// resolved at runtime by the backend (shared/crypto/key_material.py). The
// point is the local threat model — any process running as the operator's
// user can read a 0600 config file, but a Keychain item read from a
// non-approved binary triggers a user-visible authorization prompt.

package install

import (
	"fmt"
	"os/exec"
	"runtime"
)

// securityBin is the absolute path to the macOS security(1) tool. Absolute on
// purpose: a PATH lookup would let a same-user process interpose a fake
// binary — exactly the adversary this feature exists for.
const securityBin = "/usr/bin/security"

// keychainServicePrefix names the generic-password items this installer
// manages. The full service name is the prefix + the keyset entry id, so a
// rotated multi-key keyset maps to one item per key.
const keychainServicePrefix = "jentic-one-credentials-encryption-"

// keychainAccount is the fixed account attribute for our items; lookups (here
// and backend-side) match on the service name alone.
const keychainAccount = "jentic-one"

// runSecurity executes the security(1) invocation. Package-level seam so
// tests can capture arguments without touching a real keychain.
var runSecurity = func(args ...string) error {
	cmd := exec.Command(securityBin, args...) //nolint:gosec // fixed absolute binary
	if out, err := cmd.CombinedOutput(); err != nil {
		return fmt.Errorf("%s %s: %w (%s)", securityBin, args[0], err, string(out))
	}
	return nil
}

// goos is runtime.GOOS behind a seam so tests can exercise the darwin-only
// paths from any CI platform.
var goos = runtime.GOOS

// KeychainSupported reports whether the host can store the encryption key in
// a keychain. Darwin-only: the item must live in the operator's login
// keychain, which neither Linux nor the Docker path can reach.
func KeychainSupported() bool { return goos == "darwin" }

// KeychainServiceName returns the generic-password service name for a keyset
// entry id (the value written to `material_keychain` in the config).
func KeychainServiceName(keyID string) string { return keychainServicePrefix + keyID }

// storeKeychainSecret upserts (-U) a generic-password item holding the base64
// key material. Known trade-off, documented in the plan: the secret transits
// the argv of one short-lived local process at install time.
func storeKeychainSecret(service, secret string) error {
	return runSecurity(
		"add-generic-password", "-U",
		"-s", service,
		"-a", keychainAccount,
		"-w", secret,
	)
}

// ApplyKeychain moves the draft's encryption key material into the macOS
// Keychain and rewrites the draft so render.go emits `material_keychain`
// references instead of inline `material` values. Call after FillSecrets /
// ReuseSecrets and before Render.
//
// Two cases:
//   - Fresh install: the generated EncryptionKey is stored under the v1
//     service name and cleared from the draft.
//   - Reinstall reusing an existing keyset: every inline entry is migrated
//     (same material, new home — ciphertexts stay decryptable); entries that
//     are already keychain references are left alone, so the flag is
//     idempotent across reinstalls.
func (d *Draft) ApplyKeychain() error {
	if !KeychainSupported() {
		return fmt.Errorf("--keychain requires macOS (GOOS=%s)", goos)
	}
	if d.IsDocker() {
		return fmt.Errorf("--keychain is not supported on the Docker path: " +
			"the app container cannot reach the host keychain; use the source " +
			"runtime path, or inject the key via material_env instead")
	}

	if d.EncryptionKeyset != nil {
		for i := range d.EncryptionKeyset.Entries {
			e := &d.EncryptionKeyset.Entries[i]
			if e.Material == "" {
				continue // already a keychain reference (or malformed; render as-is)
			}
			service := KeychainServiceName(e.ID)
			if err := storeKeychainSecret(service, e.Material); err != nil {
				return fmt.Errorf("store key %q in keychain: %w", e.ID, err)
			}
			e.Material = ""
			e.MaterialKeychain = service
		}
		return nil
	}

	service := KeychainServiceName("v1")
	if err := storeKeychainSecret(service, d.EncryptionKey); err != nil {
		return fmt.Errorf("store key in keychain: %w", err)
	}
	d.EncryptionKey = ""
	d.EncryptionKeychain = service
	return nil
}
