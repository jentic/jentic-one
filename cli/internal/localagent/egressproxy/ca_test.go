package egressproxy

import (
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/asn1"
	"math/big"
	"net"
	"strings"
	"testing"
	"time"
)

var (
	oidBasicConstraints = asn1.ObjectIdentifier{2, 5, 29, 19}
	oidNameConstraints  = asn1.ObjectIdentifier{2, 5, 29, 30}
	oidKeyUsage         = asn1.ObjectIdentifier{2, 5, 29, 15}
)

func testCA(t *testing.T, domains ...string) *CA {
	t.Helper()
	if len(domains) == 0 {
		domains = []string{"github.com", "api.cloudflare.com", "amazonaws.com"}
	}
	ca, err := NewCA("test", domains)
	if err != nil {
		t.Fatal(err)
	}
	return ca
}

func extension(t *testing.T, c *x509.Certificate, oid asn1.ObjectIdentifier) pkix.Extension {
	t.Helper()
	for _, e := range c.Extensions {
		if e.Id.Equal(oid) {
			return e
		}
	}
	t.Fatalf("certificate has no extension %v", oid)
	return pkix.Extension{}
}

func TestCAConstraints(t *testing.T) {
	ca := testCA(t)
	c := ca.Certificate()
	if !c.IsCA || !c.BasicConstraintsValid || c.MaxPathLen != 0 || !c.MaxPathLenZero {
		t.Errorf("basic constraints: IsCA=%v MaxPathLen=%d MaxPathLenZero=%v, want a CA with pathlen:0", c.IsCA, c.MaxPathLen, c.MaxPathLenZero)
	}
	for _, oid := range []asn1.ObjectIdentifier{oidBasicConstraints, oidNameConstraints, oidKeyUsage} {
		if !extension(t, c, oid).Critical {
			t.Errorf("extension %v must be critical", oid)
		}
	}
	if !c.PermittedDNSDomainsCritical {
		t.Error("nameConstraints must be critical")
	}
	if got := strings.Join(c.PermittedDNSDomains, ","); got != "github.com,api.cloudflare.com,amazonaws.com" {
		t.Errorf("permitted DNS = %s", got)
	}
	excluded := map[string]bool{}
	for _, n := range c.ExcludedIPRanges {
		excluded[n.String()] = true
	}
	if !excluded["0.0.0.0/0"] || !excluded["::/0"] || len(excluded) != 2 {
		t.Errorf("excluded IP ranges = %v, want exactly 0.0.0.0/0 and ::/0", excluded)
	}
	if c.KeyUsage != x509.KeyUsageCertSign|x509.KeyUsageCRLSign {
		t.Errorf("key usage = %v", c.KeyUsage)
	}
	if len(c.SubjectKeyId) == 0 {
		t.Error("the CA needs a subject key identifier")
	}
	pub, ok := c.PublicKey.(*ecdsa.PublicKey)
	if !ok || pub.Curve != elliptic.P256() {
		t.Errorf("CA key is %T, want ECDSA P-256", c.PublicKey)
	}
	if c.NotAfter.Sub(c.NotBefore) > caValidity+clockSkew {
		t.Errorf("CA validity %s exceeds %s", c.NotAfter.Sub(c.NotBefore), caValidity)
	}
}

func TestNewCARefusesAnUnconstrainedCA(t *testing.T) {
	if _, err := NewCA("x", nil); err == nil {
		t.Fatal("a CA with no permitted domain must be refused")
	}
	for _, bad := range []string{"10.0.0.1", "*.github.com", "com", ""} {
		if _, err := NewCA("x", []string{bad}); err == nil {
			t.Errorf("permitted domain %q must be refused", bad)
		}
	}
}

func TestLeafIsStandardsStrict(t *testing.T) {
	ca := testCA(t)
	leaf, err := ca.Leaf("API.GitHub.com.")
	if err != nil {
		t.Fatal(err)
	}
	l := leaf.Leaf
	if len(l.DNSNames) != 1 || l.DNSNames[0] != "api.github.com" || len(l.IPAddresses) != 0 {
		t.Errorf("SAN = %v %v, want exactly DNS:api.github.com", l.DNSNames, l.IPAddresses)
	}
	if l.IsCA || !l.BasicConstraintsValid {
		t.Error("a leaf must carry CA:FALSE")
	}
	if len(l.ExtKeyUsage) != 1 || l.ExtKeyUsage[0] != x509.ExtKeyUsageServerAuth {
		t.Errorf("EKU = %v, want serverAuth only", l.ExtKeyUsage)
	}
	if l.KeyUsage != x509.KeyUsageDigitalSignature {
		t.Errorf("key usage = %v", l.KeyUsage)
	}
	if len(l.SubjectKeyId) == 0 || string(l.AuthorityKeyId) != string(ca.Certificate().SubjectKeyId) {
		t.Error("a leaf needs an SKI and an AKI naming the CA's SKI")
	}
	if l.NotAfter.Sub(l.NotBefore) > leafValidity+clockSkew {
		t.Errorf("leaf validity %s exceeds %s", l.NotAfter.Sub(l.NotBefore), leafValidity)
	}
	if l.SerialNumber.Sign() <= 0 || len(l.SerialNumber.Bytes()) > 20 {
		t.Errorf("serial %v must be positive and at most 20 octets", l.SerialNumber)
	}
	if len(leaf.Certificate) != 2 {
		t.Errorf("the chain must be leaf + CA, got %d certificates", len(leaf.Certificate))
	}
	pool := x509.NewCertPool()
	pool.AddCert(ca.Certificate())
	if _, err := l.Verify(x509.VerifyOptions{DNSName: "api.github.com", Roots: pool}); err != nil {
		t.Errorf("Go does not verify the leaf: %v", err)
	}
	again, _ := ca.Leaf("api.github.com")
	if again != leaf {
		t.Error("a leaf is minted once per host and cached")
	}
}

func TestLeafRefusesHostsOutsideTheConstraints(t *testing.T) {
	ca := testCA(t, "github.com")
	for _, host := range []string{"example.com", "github.com.evil.com", "notgithub.com", "140.82.112.3", "::1", "localhost"} {
		if _, err := ca.Leaf(host); err == nil {
			t.Errorf("Leaf(%q) must be refused", host)
		}
	}
}

// TestNameConstraintsBindVerifiers pins why the constraints exist: even a
// certificate the CA's own key signs for another name, or for an IP address,
// does not verify.
func TestNameConstraintsBindVerifiers(t *testing.T) {
	ca := testCA(t, "github.com")
	pool := x509.NewCertPool()
	pool.AddCert(ca.Certificate())
	forge := func(dns []string, ips []net.IP) *x509.Certificate {
		key, _ := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
		tmpl := &x509.Certificate{
			SerialNumber: big.NewInt(7), Subject: pkix.Name{CommonName: "forged"},
			NotBefore: time.Now().Add(-time.Minute), NotAfter: time.Now().Add(time.Hour),
			ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth}, DNSNames: dns, IPAddresses: ips,
		}
		der, err := x509.CreateCertificate(rand.Reader, tmpl, ca.cert, &key.PublicKey, ca.key)
		if err != nil {
			t.Fatal(err)
		}
		c, _ := x509.ParseCertificate(der)
		return c
	}
	if _, err := forge([]string{"example.com"}, nil).Verify(x509.VerifyOptions{DNSName: "example.com", Roots: pool}); err == nil {
		t.Error("a CA-signed certificate outside the permitted DNS subtree must not verify")
	}
	if _, err := forge(nil, []net.IP{net.ParseIP("140.82.112.3")}).Verify(x509.VerifyOptions{DNSName: "140.82.112.3", Roots: pool}); err == nil {
		t.Error("a CA-signed IPv4 certificate must not verify (excluded 0.0.0.0/0)")
	}
	if _, err := forge(nil, []net.IP{net.ParseIP("2001:db8::1")}).Verify(x509.VerifyOptions{DNSName: "2001:db8::1", Roots: pool}); err == nil {
		t.Error("a CA-signed IPv6 certificate must not verify (excluded ::/0)")
	}
	if _, err := forge([]string{"uploads.github.com"}, nil).Verify(x509.VerifyOptions{DNSName: "uploads.github.com", Roots: pool}); err != nil {
		t.Errorf("a subdomain of a permitted domain must verify: %v", err)
	}
}

func TestLeafIsReMintedNearExpiry(t *testing.T) {
	now := time.Now()
	ca, err := newCA("t", []string{"github.com"}, func() time.Time { return now })
	if err != nil {
		t.Fatal(err)
	}
	first, _ := ca.Leaf("api.github.com")
	now = now.Add(leafValidity - leafRenewBefore + time.Minute)
	second, _ := ca.Leaf("api.github.com")
	if first == second {
		t.Error("a leaf within leafRenewBefore of expiry must be re-minted")
	}
}
