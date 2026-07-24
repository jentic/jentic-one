package install

import (
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/x509"
	"encoding/base64"
	"encoding/pem"
	"fmt"
)

// randomBase64 returns base64(std) of n cryptographically-random bytes.
func randomBase64(n int) (string, error) {
	buf := make([]byte, n)
	if _, err := rand.Read(buf); err != nil {
		return "", fmt.Errorf("generate random bytes: %w", err)
	}
	return base64.StdEncoding.EncodeToString(buf), nil
}

// FillSecrets populates the Draft's generated-secret fields with fresh random
// values. The encryption key is a 32-byte (AES-256) base64 string, matching the
// credentials.encryption keyset format. The other secrets are random tokens
// suitable for local installs. Call once before rendering the config.
//
// Fields that already carry a value are preserved: [ReuseSecrets] populates
// them from an existing config so a reinstall keeps the on-disk data readable
// (a rotated encryption key would silently brick stored credentials). Only
// blank fields are filled here. The `--fresh-secrets` install flag skips the
// reuse step entirely and re-enters this with a zero draft, giving the old
// "always fresh" behavior for deliberate rotation.
func (d *Draft) FillSecrets() error {
	// Each surface secret is an independent 32-byte (256-bit) random token.
	for _, dst := range []*string{
		&d.EncryptionKey,
		&d.AdminJWTSecret,
		&d.AdminInvitePepper,
		&d.ConnectStateSecret,
	} {
		if *dst != "" {
			continue
		}
		v, err := randomBase64(32)
		if err != nil {
			return err
		}
		*dst = v
	}

	// SSO needs an ES256 key for the platform to sign its own ID tokens.
	if d.SSOEnabled && d.IDSigningKeyPEM == "" {
		keyPEM, err := generateES256PEM()
		if err != nil {
			return err
		}
		d.IDSigningKeyPEM = keyPEM
		if d.IDSigningKID == "" {
			d.IDSigningKID = "local-es256"
		}
	}
	return nil
}

// generateES256PEM returns a fresh P-256 ECDSA private key as a PKCS#8 PEM,
// matching the id_signing format the auth surface expects.
func generateES256PEM() (string, error) {
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		return "", fmt.Errorf("generate ES256 key: %w", err)
	}
	der, err := x509.MarshalPKCS8PrivateKey(key)
	if err != nil {
		return "", fmt.Errorf("marshal ES256 key: %w", err)
	}
	return string(pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: der})), nil
}
