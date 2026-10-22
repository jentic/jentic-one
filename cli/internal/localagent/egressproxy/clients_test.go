package egressproxy

import (
	"bytes"
	"context"
	"crypto/tls"
	"encoding/pem"
	"io"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// The leaves must satisfy the strictest verifiers the agent's tools use. Each
// check runs only when the client is installed on the test machine; the
// container integration test repeats them inside the agent image.

// leafServer serves HTTPS on loopback with the CA's leaf for host.
func leafServer(t *testing.T, ca *CA, host string) string {
	t.Helper()
	leaf, err := ca.Leaf(host)
	if err != nil {
		t.Fatal(err)
	}
	ln, err := tls.Listen("tcp", "127.0.0.1:0", &tls.Config{Certificates: []tls.Certificate{*leaf}, NextProtos: []string{"http/1.1"}})
	if err != nil {
		t.Fatal(err)
	}
	srv := &http.Server{ReadHeaderTimeout: 5 * time.Second, Handler: http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		_, _ = io.WriteString(w, "verified")
	})}
	go func() { _ = srv.Serve(ln) }()
	t.Cleanup(func() { _ = srv.Close() })
	_, port, _ := net.SplitHostPort(ln.Addr().String())
	return port
}

func writeCA(t *testing.T, ca *CA) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "ca.pem")
	if err := os.WriteFile(path, ca.CertPEM(), 0o600); err != nil {
		t.Fatal(err)
	}
	return path
}

func runClient(t *testing.T, name string, args ...string) string {
	t.Helper()
	bin, err := exec.LookPath(name)
	if err != nil {
		t.Skipf("%s is not installed", name)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	out, err := exec.CommandContext(ctx, bin, args...).CombinedOutput()
	if err != nil {
		t.Fatalf("%s: %v\n%s", name, err, out)
	}
	return string(out)
}

func TestLeafAcceptedByPython313StrictMode(t *testing.T) {
	ca := testCA(t)
	port := leafServer(t, ca, "api.github.com")
	script := `
import socket, ssl, sys
ctx = ssl.create_default_context(cafile=sys.argv[1])
assert ctx.verify_flags & ssl.VERIFY_X509_STRICT, "strict mode is not on"
with socket.create_connection(("127.0.0.1", int(sys.argv[2]))) as s:
    with ctx.wrap_socket(s, server_hostname="api.github.com") as t:
        print("ok", t.version())
`
	if out := runClient(t, "python3.13", "-I", "-c", script, writeCA(t, ca), port); !strings.HasPrefix(out, "ok") {
		t.Errorf("python3.13: %s", out)
	}
}

func TestLeafAcceptedByOpenSSLStrict(t *testing.T) {
	ca := testCA(t)
	leaf, _ := ca.Leaf("api.cloudflare.com")
	leafPath := filepath.Join(t.TempDir(), "leaf.pem")
	_ = os.WriteFile(leafPath, pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: leaf.Certificate[0]}), 0o600)
	out := runClient(t, "openssl", "verify", "-x509_strict", "-purpose", "sslserver",
		"-verify_hostname", "api.cloudflare.com", "-CAfile", writeCA(t, ca), leafPath)
	if !strings.Contains(out, ": OK") {
		t.Errorf("openssl verify: %s", out)
	}
}

func TestLeafAcceptedByGnuTLS(t *testing.T) {
	ca := testCA(t)
	port := leafServer(t, ca, "github.com")
	bin, err := exec.LookPath("gnutls-cli")
	if err != nil {
		t.Skip("gnutls-cli is not installed")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, bin, "--x509cafile="+writeCA(t, ca), "--sni-hostname=github.com",
		"--verify-hostname=github.com", "-p", port, "127.0.0.1")
	cmd.Stdin = strings.NewReader("")
	out, err := cmd.CombinedOutput()
	if err != nil || !bytes.Contains(out, []byte("Handshake was completed")) || bytes.Contains(out, []byte("NOT trusted")) {
		t.Errorf("gnutls-cli: %v\n%s", err, out)
	}
}

func TestLeafAcceptedByNode(t *testing.T) {
	ca := testCA(t)
	port := leafServer(t, ca, "api.github.com")
	script := `
const tls = require("tls"), fs = require("fs");
const s = tls.connect({host: "127.0.0.1", port: +process.argv[2], servername: "api.github.com",
  ca: fs.readFileSync(process.argv[1])}, () => { console.log("ok", s.authorized); s.end(); });
s.on("error", e => { console.error(e.message); process.exit(1); });
`
	if out := runClient(t, "node", "-e", script, writeCA(t, ca), port); !strings.HasPrefix(out, "ok true") {
		t.Errorf("node: %s", out)
	}
}
