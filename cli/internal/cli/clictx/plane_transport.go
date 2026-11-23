package clictx

import (
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	"fmt"
	"net/http"
	"os"
	"sync"
)

const planeTransportCacheMax = 4

var (
	planeTransportMu    sync.Mutex
	planeTransports     = make(map[[sha256.Size]byte]*http.Transport, planeTransportCacheMax)
	planeTransportOrder = make([][sha256.Size]byte, 0, planeTransportCacheMax)
)

// planeTransport returns the shared RoundTripper for a CA bundle. The PEM is
// read and parsed on EVERY call so a deleted/corrupt bundle still fails closed
// (SEC-20); only the *http.Transport is cached, keyed by sha256(PEM).
func planeTransport(path string) (http.RoundTripper, error) {
	pem, err := os.ReadFile(path) //nolint:gosec // path is the operator-configured ca_cert_path from their own config.
	if err != nil {
		return nil, fmt.Errorf("ca_cert_path %q is set but could not be read: %w", path, err)
	}
	pool := x509.NewCertPool()
	if !pool.AppendCertsFromPEM(pem) {
		return nil, fmt.Errorf("ca_cert_path %q contains no usable PEM certificate", path)
	}

	digest := sha256.Sum256(pem)

	planeTransportMu.Lock()
	defer planeTransportMu.Unlock()

	if tr, ok := planeTransports[digest]; ok {
		return tr, nil
	}
	if len(planeTransports) >= planeTransportCacheMax {
		oldest := planeTransportOrder[0]
		planeTransportOrder = planeTransportOrder[1:]
		if evicted, ok := planeTransports[oldest]; ok {
			delete(planeTransports, oldest)
			evicted.CloseIdleConnections()
		}
	}

	tr := http.DefaultTransport.(*http.Transport).Clone()
	tr.TLSClientConfig = &tls.Config{
		RootCAs:    pool,
		MinVersion: tls.VersionTLS12,
	}
	planeTransports[digest] = tr
	planeTransportOrder = append(planeTransportOrder, digest)
	return tr, nil
}

// NoFollowRedirects is the CheckRedirect for every raw plane client (#1207).
func NoFollowRedirects(*http.Request, []*http.Request) error {
	return http.ErrUseLastResponse
}
