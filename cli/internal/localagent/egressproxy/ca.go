package egressproxy

import (
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/sha1" //nolint:gosec // G505: SHA-1 is the RFC 5280 key-identifier method, not a signature.
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/asn1"
	"encoding/pem"
	"errors"
	"fmt"
	"math/big"
	"net"
	"sync"
	"time"
)

const (
	// caValidity bounds the session CA. A session that outlives it loses its
	// governed hosts (their leaves no longer verify), never gains anything.
	caValidity = 7 * 24 * time.Hour
	// leafValidity is each host certificate's lifetime; a cached leaf is
	// re-minted once it is within leafRenewBefore of expiring.
	leafValidity    = 24 * time.Hour
	leafRenewBefore = time.Hour
	// clockSkew backdates notBefore so a client whose clock runs slightly
	// behind the proxy's still accepts a fresh certificate.
	clockSkew = 5 * time.Minute
)

// CA is a per-session certificate authority held in process memory only: its
// private key is never marshalled, written or logged. It signs leaf
// certificates for the governed domains and nothing else: every certificate it
// issues is checked against the same permitted DNS subtrees its critical
// nameConstraints carry, and it refuses to sign an IP address at all.
type CA struct {
	cert      *x509.Certificate
	key       *ecdsa.PrivateKey
	certPEM   []byte
	permitted []string
	now       func() time.Time

	mu     sync.Mutex
	leaves map[string]*tls.Certificate
}

// NewCA generates a session CA (ECDSA P-256) whose critical nameConstraints
// permit exactly the given DNS domains (each with its subdomains) and exclude
// every IPv4 and IPv6 address, with pathlen:0. At least one domain is
// required: a CA with no permitted subtree would be unconstrained.
func NewCA(sessionLabel string, permitted []string) (*CA, error) {
	return newCA(sessionLabel, permitted, time.Now)
}

func newCA(sessionLabel string, permitted []string, now func() time.Time) (*CA, error) {
	if len(permitted) == 0 {
		return nil, errors.New("a session CA needs at least one permitted domain")
	}
	domains := make([]string, 0, len(permitted))
	for _, d := range permitted {
		norm, ok := NormalizeHost(d)
		if !ok {
			return nil, fmt.Errorf("invalid permitted domain %q", d)
		}
		domains = append(domains, norm)
	}
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		return nil, fmt.Errorf("generate the session CA key: %w", err)
	}
	serial, err := randomSerial()
	if err != nil {
		return nil, err
	}
	ski, err := keyID(&key.PublicKey)
	if err != nil {
		return nil, err
	}
	_, all4, _ := net.ParseCIDR("0.0.0.0/0")
	_, all6, _ := net.ParseCIDR("::/0")
	start := now()
	name := pkix.Name{Organization: []string{"Jentic"}, CommonName: "Jentic egress session CA " + sessionLabel}
	tmpl := &x509.Certificate{
		SerialNumber:                serial,
		Subject:                     name,
		NotBefore:                   start.Add(-clockSkew),
		NotAfter:                    start.Add(caValidity),
		KeyUsage:                    x509.KeyUsageCertSign | x509.KeyUsageCRLSign,
		BasicConstraintsValid:       true,
		IsCA:                        true,
		MaxPathLen:                  0,
		MaxPathLenZero:              true,
		SubjectKeyId:                ski,
		PermittedDNSDomainsCritical: true,
		PermittedDNSDomains:         domains,
		ExcludedIPRanges:            []*net.IPNet{all4, all6},
	}
	der, err := x509.CreateCertificate(rand.Reader, tmpl, tmpl, &key.PublicKey, key)
	if err != nil {
		return nil, fmt.Errorf("sign the session CA: %w", err)
	}
	cert, err := x509.ParseCertificate(der)
	if err != nil {
		return nil, err
	}
	return &CA{
		cert:      cert,
		key:       key,
		certPEM:   pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der}),
		permitted: domains,
		now:       now,
		leaves:    map[string]*tls.Certificate{},
	}, nil
}

// CertPEM is the CA certificate, PEM-encoded: the only part of the CA that
// leaves the process.
func (c *CA) CertPEM() []byte { return append([]byte(nil), c.certPEM...) }

// Certificate is the parsed CA certificate.
func (c *CA) Certificate() *x509.Certificate { return c.cert }

// Permits reports whether host falls inside the CA's permitted subtrees.
func (c *CA) Permits(host string) bool {
	norm, ok := NormalizeHost(host)
	if !ok {
		return false
	}
	return matchesDomain(norm, c.permitted)
}

// Leaf returns a TLS certificate for host, minted on first use and cached
// until it nears expiry. It refuses a host outside the permitted subtrees and
// any IP literal, so the proxy can never present a certificate for a host it
// must not decrypt.
func (c *CA) Leaf(host string) (*tls.Certificate, error) {
	norm, ok := NormalizeHost(host)
	if !ok || !matchesDomain(norm, c.permitted) {
		return nil, fmt.Errorf("the session CA does not sign %q", host)
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	if leaf, ok := c.leaves[norm]; ok && c.now().Add(leafRenewBefore).Before(leaf.Leaf.NotAfter) {
		return leaf, nil
	}
	leaf, err := c.mintLeaf(norm)
	if err != nil {
		return nil, err
	}
	c.leaves[norm] = leaf
	return leaf, nil
}

func (c *CA) mintLeaf(host string) (*tls.Certificate, error) {
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		return nil, fmt.Errorf("generate a leaf key: %w", err)
	}
	serial, err := randomSerial()
	if err != nil {
		return nil, err
	}
	ski, err := keyID(&key.PublicKey)
	if err != nil {
		return nil, err
	}
	start := c.now()
	notAfter := start.Add(leafValidity)
	if notAfter.After(c.cert.NotAfter) {
		notAfter = c.cert.NotAfter
	}
	tmpl := &x509.Certificate{
		SerialNumber:          serial,
		Subject:               pkix.Name{CommonName: host},
		NotBefore:             start.Add(-clockSkew),
		NotAfter:              notAfter,
		KeyUsage:              x509.KeyUsageDigitalSignature,
		ExtKeyUsage:           []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth},
		BasicConstraintsValid: true,
		IsCA:                  false,
		SubjectKeyId:          ski,
		AuthorityKeyId:        c.cert.SubjectKeyId,
		DNSNames:              []string{host},
	}
	der, err := x509.CreateCertificate(rand.Reader, tmpl, c.cert, &key.PublicKey, c.key)
	if err != nil {
		return nil, fmt.Errorf("sign the leaf for %s: %w", host, err)
	}
	parsed, err := x509.ParseCertificate(der)
	if err != nil {
		return nil, err
	}
	return &tls.Certificate{
		Certificate: [][]byte{der, c.cert.Raw},
		PrivateKey:  key,
		Leaf:        parsed,
	}, nil
}

// randomSerial is a positive 127-bit serial: unpredictable, and well inside
// RFC 5280's 20-octet limit.
func randomSerial() (*big.Int, error) {
	serial, err := rand.Int(rand.Reader, new(big.Int).Lsh(big.NewInt(1), 127))
	if err != nil {
		return nil, fmt.Errorf("generate a serial number: %w", err)
	}
	return serial.Add(serial, big.NewInt(1)), nil
}

// keyID is the RFC 5280 method-1 key identifier: SHA-1 of the subject public
// key bit string.
func keyID(pub *ecdsa.PublicKey) ([]byte, error) {
	der, err := x509.MarshalPKIXPublicKey(pub)
	if err != nil {
		return nil, err
	}
	var spki struct {
		Algorithm pkix.AlgorithmIdentifier
		PublicKey asn1.BitString
	}
	if _, err := asn1.Unmarshal(der, &spki); err != nil {
		return nil, err
	}
	sum := sha1.Sum(spki.PublicKey.Bytes) //nolint:gosec // G401: RFC 5280 key identifier.
	return sum[:], nil
}
