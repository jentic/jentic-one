package egressproxy

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"io"
	"log/slog"
	"net"
	"net/http"
	"net/url"
	"os"
	"os/signal"
	"path/filepath"
	"strings"
	"syscall"
	"time"
)

// Main is the jentic-egress-proxy entrypoint: `serve`, `set-tokens` and
// `ready`. It returns the process exit code.
func Main(args []string, stdin io.Reader, stderr io.Writer) int {
	if len(args) == 0 {
		fmt.Fprintln(stderr, "usage: jentic-egress-proxy serve|set-tokens|ready [flags]")
		return 2
	}
	var err error
	switch args[0] {
	case "serve":
		err = runServe(args[1:], stderr)
	case "set-tokens":
		err = runSetTokens(args[1:], stdin, stderr)
	case "ready":
		err = runReady(args[1:], stderr)
	default:
		fmt.Fprintf(stderr, "jentic-egress-proxy: unknown command %q\n", args[0])
		return 2
	}
	if err == nil {
		return 0
	}
	fmt.Fprintln(stderr, "jentic-egress-proxy:", err)
	var usage usageError
	if errors.As(err, &usage) {
		return 2
	}
	return 1
}

type usageError struct{ error }

func newFlagSet(name string, stderr io.Writer) *flag.FlagSet {
	fs := flag.NewFlagSet(name, flag.ContinueOnError)
	fs.SetOutput(stderr)
	return fs
}

func splitList(s string) []string {
	var out []string
	for _, item := range strings.Split(s, ",") {
		if item = strings.TrimSpace(item); item != "" {
			out = append(out, item)
		}
	}
	return out
}

// ServeOptions are the `serve` flags.
type ServeOptions struct {
	Listen       string
	BrokerURL    string
	BrokerSocket string
	CADir        string
	SystemRoots  string
	TokenFile    string
	Nonce        string
	Session      string
	Govern       []string
	Tunnel       []string
}

func parseServe(args []string, stderr io.Writer) (ServeOptions, error) {
	var o ServeOptions
	var govern, tunnel string
	fs := newFlagSet("serve", stderr)
	fs.StringVar(&o.Listen, "listen", ":3128", "address to listen on")
	fs.StringVar(&o.BrokerURL, "broker-url", "", "the broker's base URL as the CLI's context names it")
	fs.StringVar(&o.BrokerSocket, "broker-socket", "", "unix socket that reaches the broker (the session relay); empty dials the broker URL")
	fs.StringVar(&o.CADir, "ca-dir", "", "directory to publish the CA files to")
	fs.StringVar(&o.SystemRoots, "system-roots", "/etc/ssl/certs/ca-certificates.crt", "PEM bundle of the system roots")
	fs.StringVar(&o.TokenFile, "token-file", "", "the session token file")
	fs.StringVar(&o.Nonce, "nonce", "", "the session's readiness nonce")
	fs.StringVar(&o.Session, "session", "", "the session id (CA name only)")
	fs.StringVar(&govern, "govern", "", "comma-separated vendors to govern")
	fs.StringVar(&tunnel, "tunnel", "", "comma-separated tunnel host patterns")
	if err := fs.Parse(args); err != nil {
		return o, usageError{err}
	}
	o.Govern, o.Tunnel = splitList(govern), splitList(tunnel)
	if o.BrokerURL == "" || o.CADir == "" || o.TokenFile == "" || !ValidNonce(o.Nonce) {
		return o, usageError{errors.New("serve needs --broker-url, --ca-dir, --token-file and a hex --nonce")}
	}
	return o, nil
}

func runServe(args []string, stderr io.Writer) error {
	o, err := parseServe(args, stderr)
	if err != nil {
		return err
	}
	logger := slog.New(slog.NewJSONHandler(stderr, &slog.HandlerOptions{Level: slog.LevelInfo}))
	srv, ln, err := Start(o, logger)
	if err != nil {
		return err
	}
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGTERM, os.Interrupt)
	defer stop()
	go func() {
		<-ctx.Done()
		_ = srv.Close()
	}()
	logger.Info("egress proxy serving", "listen", ln.Addr().String(), "governed", o.Govern, "tunnel", o.Tunnel)
	return srv.Serve(ln)
}

// Start builds the session CA (when a vendor is governed), binds the
// listener and only then publishes the CA files and the ready file, so a
// ready session always has a proxy accepting connections.
func Start(o ServeOptions, logger *slog.Logger) (*Server, net.Listener, error) {
	policy, err := NewPolicy(o.Govern, o.Tunnel)
	if err != nil {
		return nil, nil, err
	}
	var ca *CA
	if domains := policy.GovernedDomains(); len(domains) > 0 {
		if ca, err = NewCA(o.Session, domains); err != nil {
			return nil, nil, err
		}
	}
	broker, err := url.Parse(o.BrokerURL)
	if err != nil {
		return nil, nil, fmt.Errorf("invalid broker URL: %w", err)
	}
	transport := http.DefaultTransport.(*http.Transport).Clone()
	transport.Proxy = nil
	if o.BrokerSocket != "" {
		socket := o.BrokerSocket
		dialer := &net.Dialer{Timeout: 10 * time.Second}
		transport.DialContext = func(ctx context.Context, _, _ string) (net.Conn, error) {
			return dialer.DialContext(ctx, "unix", socket)
		}
	}
	roots, err := os.ReadFile(o.SystemRoots)
	if err != nil {
		return nil, nil, fmt.Errorf("read the system roots: %w", err)
	}
	srv, err := New(Config{
		Policy:          policy,
		CA:              ca,
		Tokens:          NewTokenStore(o.TokenFile),
		BrokerURL:       broker,
		BrokerTransport: transport,
		Logger:          logger,
	})
	if err != nil {
		return nil, nil, err
	}
	ln, err := net.Listen("tcp", o.Listen)
	if err != nil {
		return nil, nil, err
	}
	if err := Publish(o.CADir, ca, roots, o.Nonce); err != nil {
		_ = ln.Close()
		return nil, nil, fmt.Errorf("publish the session CA: %w", err)
	}
	return srv, ln, nil
}

func runSetTokens(args []string, stdin io.Reader, stderr io.Writer) error {
	fs := newFlagSet("set-tokens", stderr)
	path := fs.String("token-file", "", "the session token file")
	if err := fs.Parse(args); err != nil {
		return usageError{err}
	}
	if *path == "" || !filepath.IsAbs(*path) {
		return usageError{errors.New("set-tokens needs an absolute --token-file")}
	}
	return WriteTokenFile(*path, stdin)
}

func runReady(args []string, stderr io.Writer) error {
	fs := newFlagSet("ready", stderr)
	dir := fs.String("ca-dir", "", "directory the CA files are published to")
	nonce := fs.String("nonce", "", "the session's readiness nonce")
	listen := fs.String("listen", "127.0.0.1:3128", "the proxy's address to probe")
	tokenFile := fs.String("token-file", "", "require this session token file to exist")
	if err := fs.Parse(args); err != nil {
		return usageError{err}
	}
	if err := CheckReady(*dir, *nonce); err != nil {
		return err
	}
	if *tokenFile != "" {
		if _, err := os.Stat(*tokenFile); err != nil {
			return fmt.Errorf("the session tokens are not in place: %w", err)
		}
	}
	conn, err := net.DialTimeout("tcp", *listen, 2*time.Second)
	if err != nil {
		return fmt.Errorf("the proxy is not accepting connections: %w", err)
	}
	return conn.Close()
}
