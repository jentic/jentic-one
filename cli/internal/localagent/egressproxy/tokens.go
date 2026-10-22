package egressproxy

import (
	"bytes"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"sync"
	"time"
)

// invocationTokenRe is a `cit_` invocation token as the broker mints it.
var invocationTokenRe = regexp.MustCompile(`^cit_[A-Za-z0-9_-]{32,256}$`)

// containsTokenRe finds a `cit_` token anywhere in a header value.
var containsTokenRe = regexp.MustCompile(`(?:^|[^A-Za-z0-9_])cit_[A-Za-z0-9_-]{16,}`)

// ValidToken reports whether s has the shape of a `cit_` invocation token.
func ValidToken(s string) bool { return invocationTokenRe.MatchString(s) }

// maxTokenFile bounds the session token file the proxy reads.
const maxTokenFile = 64 << 10

// TokenFile is the session token file: one invocation token per governed
// vendor, written by the host-side CLI (through WriteTokenFile, run inside
// the proxy container) and read by the proxy. It lives on a tmpfs only the
// proxy container mounts.
type TokenFile struct {
	Tokens map[string]string `json:"tokens"`
}

// ParseTokenFile decodes and validates a token file.
func ParseTokenFile(data []byte) (TokenFile, error) {
	var tf TokenFile
	dec := json.NewDecoder(bytes.NewReader(data))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&tf); err != nil {
		return TokenFile{}, fmt.Errorf("decode the session token file: %w", err)
	}
	for vendor, token := range tf.Tokens {
		if VendorDomain(vendor) == "" {
			return TokenFile{}, fmt.Errorf("session token for unknown vendor %q", vendor)
		}
		if !ValidToken(token) {
			return TokenFile{}, fmt.Errorf("session token for %s is not an invocation token", vendor)
		}
	}
	return tf, nil
}

// WriteTokenFile validates the token file read from r and replaces path with
// it atomically (a temp file in the same directory, mode 0600, renamed over
// path), so the proxy never reads a half-written file.
func WriteTokenFile(path string, r io.Reader) error {
	data, err := io.ReadAll(io.LimitReader(r, maxTokenFile+1))
	if err != nil {
		return err
	}
	if len(data) > maxTokenFile {
		return errors.New("session token file too large")
	}
	if _, err := ParseTokenFile(data); err != nil {
		return err
	}
	tmp, err := os.CreateTemp(filepath.Dir(path), ".tokens-*")
	if err != nil {
		return err
	}
	defer os.Remove(tmp.Name())
	if err := tmp.Chmod(0o600); err != nil {
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
	return os.Rename(tmp.Name(), path)
}

// TokenStore serves the session tokens from the token file, re-reading it
// whenever its modification time or size changes. A file that cannot be read
// or parsed keeps the last good tokens (the host-side heartbeat rewrites it).
type TokenStore struct {
	path string

	mu      sync.Mutex
	modTime time.Time
	size    int64
	tokens  map[string]string
}

// NewTokenStore reads session tokens from path.
func NewTokenStore(path string) *TokenStore {
	return &TokenStore{path: path, tokens: map[string]string{}}
}

// Session is the session token for vendor, or "".
func (s *TokenStore) Session(vendor string) string {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.reload()
	return s.tokens[vendor]
}

func (s *TokenStore) reload() {
	info, err := os.Stat(s.path)
	if err != nil || (info.ModTime().Equal(s.modTime) && info.Size() == s.size) {
		return
	}
	data, err := os.ReadFile(s.path)
	if err != nil || len(data) > maxTokenFile {
		return
	}
	tf, err := ParseTokenFile(data)
	if err != nil {
		return
	}
	s.modTime, s.size = info.ModTime(), info.Size()
	s.tokens = tf.Tokens
	if s.tokens == nil {
		s.tokens = map[string]string{}
	}
}

// proxyCredentialToken reads a per-command invocation token from a
// Proxy-Authorization header: Basic credentials whose user name is the token
// (`HTTPS_PROXY=http://cit_…:x@proxy:3128`). ok is false when the header is
// absent; err is set when it is present but is not such a credential.
func proxyCredentialToken(header string) (token string, ok bool, err error) {
	if header == "" {
		return "", false, nil
	}
	scheme, value, found := strings.Cut(header, " ")
	if !found || !strings.EqualFold(scheme, "Basic") {
		return "", true, errors.New("proxy credentials must be Basic")
	}
	raw, decErr := base64.StdEncoding.DecodeString(strings.TrimSpace(value))
	if decErr != nil {
		return "", true, errors.New("proxy credentials are not valid base64")
	}
	user, _, _ := strings.Cut(string(raw), ":")
	if !ValidToken(user) {
		return "", true, errors.New("the proxy user name is not an invocation token")
	}
	return user, true, nil
}
