// Package auth is the SDK-owned authentication layer: environment-scoped Ed25519
// identity keys, the RFC 7523 JWT-bearer token exchange, cached token state, and
// the request-editor middleware that attaches bearers to generated clients.
//
// It is lifted from the shipped internal/agentkey + internal/agentauth (their
// crypto and tests) and re-scoped per impl/4.1 + 4.2: keys move to
// <identity>_<env>.key under the XDG config dir, tokens to the XDG state dir, and
// the signing path uses go-jose. It is UX-free (no Cobra/theme) so the public SDK
// can import it.
package auth

import (
	"crypto/ed25519"
	"crypto/rand"
	"crypto/x509"
	"encoding/base64"
	"encoding/pem"
	"fmt"
	"os"
	"path/filepath"

	"github.com/jentic/jentic-one/cli/client/config"
)

// IdentityRef identifies the (identity, environment) pair that all env-scoped
// cryptographic material is keyed by. It exists to kill a specific,
// security-relevant bug class: every key/token helper used to take
// (identityName, envName string) — two adjacent strings the compiler happily lets
// you transpose, silently resolving the WRONG key/token file. A single value
// makes that mistake unrepresentable and gives the "<identity>_<env>" filename
// stem one authoritative definition.
//
// Deliberately NOT named "Scope": the control plane has RBAC *scopes* on an agent;
// this is a local storage reference, not an authorization scope.
type IdentityRef struct {
	Identity    string
	Environment string
}

// Stem is the shared filename stem for this ref's on-disk material, e.g.
// "my-agent_prod". Key files append ".key"; token files append "_tokens.json".
//
// SECURITY — path traversal: Identity/Environment are user-supplied names
// interpolated into file paths. Without validation, a name like "../../x" escapes
// the config dir and a name containing "_" could collide two refs' stems. Names
// are validated at creation (env add/identity add, impl/1.3 §3) AND re-checked
// fail-closed here, since config.yaml is user-editable after the fact.
func (r IdentityRef) Stem() (string, error) {
	if !validName(r.Identity) || !validName(r.Environment) {
		return "", fmt.Errorf("invalid identity/environment name for file path: %q/%q", r.Identity, r.Environment)
	}
	return fmt.Sprintf("%s_%s", r.Identity, r.Environment), nil
}

// validName is a thin alias for config.ValidName — the single ^[a-z0-9][a-z0-9-]{0,63}$
// charset check shared with env add/identity add (impl/1.3 §3, impl/4.1 §1). The
// file-less sentinels ("jentic.file-less-agent", impl/1.2 §3) contain a "." so
// they can never pass this check — the file-less path never reaches Stem().
func validName(name string) bool { return config.ValidName(name) }

// keysDir is <config>/keys, created 0700. Kept separate from config.yaml so the
// private keys can hold tight 0600 perms independent of the config file.
func keysDir() (string, error) {
	cfgDir, err := config.ConfigDir()
	if err != nil {
		return "", err
	}
	dir := filepath.Join(cfgDir, "keys")
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return "", fmt.Errorf("creating keys dir: %w", err)
	}
	return dir, nil
}

// KeyPathForImport returns the on-disk path where ref's key file lives, creating
// the keys dir (0700). It exists so the migration path (jentic migrate) can copy
// a validated legacy PKCS#8 PEM key verbatim into the XDG layout — preserving the
// exact key bytes rather than generating a new keypair, which would break the
// already-registered client_id. The returned path is the same one
// GetOrGenerateKey reads/writes, so the copied key is picked up transparently.
func KeyPathForImport(ref IdentityRef) (string, error) {
	dir, err := keysDir()
	if err != nil {
		return "", err
	}
	stem, err := ref.Stem() // path-traversal guard
	if err != nil {
		return "", err
	}
	return filepath.Join(dir, stem+".key"), nil
}

// GetOrGenerateKey resolves the env-scoped Ed25519 private key for ref, generating
// and persisting (PKCS#8 PEM, 0600) a fresh one on first use. This is the only
// retained lazy side effect (impl/4.1 §2): it is local-only and never contacts the
// server, so it is safe for the auth middleware to call during a token exchange.
// The file-less path short-circuits before ever reaching here.
func GetOrGenerateKey(ref IdentityRef) (ed25519.PrivateKey, error) {
	dir, err := keysDir()
	if err != nil {
		return nil, err
	}
	stem, err := ref.Stem() // re-validates names fail-closed (path-traversal guard)
	if err != nil {
		return nil, err
	}
	keyPath := filepath.Join(dir, stem+".key")

	data, err := os.ReadFile(keyPath) //nolint:gosec // keyPath is <config>/keys/<validated-stem>.key, not user input.
	switch {
	case err == nil:
		block, _ := pem.Decode(data)
		if block == nil || block.Type != "PRIVATE KEY" {
			return nil, fmt.Errorf("invalid PEM block in %s", keyPath)
		}
		parsed, perr := x509.ParsePKCS8PrivateKey(block.Bytes)
		if perr != nil {
			return nil, fmt.Errorf("parsing key %s: %w", keyPath, perr)
		}
		priv, ok := parsed.(ed25519.PrivateKey)
		if !ok {
			return nil, fmt.Errorf("key %s is not Ed25519", keyPath)
		}
		return priv, nil
	case os.IsNotExist(err):
		// fall through to generation
	default:
		return nil, fmt.Errorf("reading key %s: %w", keyPath, err)
	}

	_, priv, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		return nil, fmt.Errorf("generating key: %w", err)
	}
	der, err := x509.MarshalPKCS8PrivateKey(priv)
	if err != nil {
		return nil, fmt.Errorf("marshaling key: %w", err)
	}
	pemBytes := pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: der})
	if err := writeFileAtomic(keyPath, pemBytes); err != nil {
		return nil, fmt.Errorf("writing key %s: %w", keyPath, err)
	}
	return priv, nil
}

// PurgeMaterial removes ALL on-disk secret material for ref — the Ed25519 key
// (<config>/keys/<stem>.key), the cached tokens (<state>/<stem>_tokens.json), and
// the API-key credential (<state>/<stem>.apikey). It is called by `identity
// delete` / `context delete --identity` so deleting an identity does not leave
// its private key and tokens orphaned on disk after the config entry is gone
// (impl/1.3 §4a "delete removes its key/token files"; F8-34).
//
// A missing file is not an error (nothing to remove). It aggregates removal
// errors so a permission problem on one file still attempts the others, and
// reports the first failure. It never touches config.yaml — the caller owns the
// config-map deletion via MutateConfig.
func PurgeMaterial(ref IdentityRef) error {
	// Compute paths WITHOUT creating the dirs: reuse the path builders but tolerate
	// their MkdirAll (harmless — the dir already exists if any material does). If
	// the stem itself is invalid, surface that (a corrupt config entry).
	if _, err := ref.Stem(); err != nil {
		return err
	}
	keyPath, kerr := KeyPathForImport(ref)
	tokPath, terr := getTokenPath(ref)
	akPath, aerr := apiKeyPath(ref)
	for _, e := range []error{kerr, terr, aerr} {
		if e != nil {
			return e
		}
	}

	var firstErr error
	for _, path := range []string{keyPath, tokPath, akPath} {
		if err := os.Remove(path); err != nil && !os.IsNotExist(err) && firstErr == nil {
			firstErr = fmt.Errorf("removing identity material %s: %w", path, err)
		}
	}
	return firstErr
}

// JWK is a single JSON Web Key for an Ed25519 public key (OKP).
type JWK struct {
	Kty string `json:"kty"`
	Crv string `json:"crv"`
	X   string `json:"x"`
	Use string `json:"use"`
	Alg string `json:"alg"`
}

// JWKS is a JSON Web Key Set.
type JWKS struct {
	Keys []JWK `json:"keys"`
}

// PublicKeyToJWKS wraps an Ed25519 public key in a single-key JWKS, as submitted
// during RFC 7591 Dynamic Client Registration (impl/4.1 §2). Mirrors the shipped
// agentkey.JWKS() wire shape.
func PublicKeyToJWKS(pub ed25519.PublicKey) JWKS {
	return JWKS{Keys: []JWK{{
		Kty: "OKP",
		Crv: "Ed25519",
		X:   base64.RawURLEncoding.EncodeToString(pub),
		Use: "sig",
		Alg: "EdDSA",
	}}}
}

// clientIDFor reads the client_id persisted during registration (impl/4.1 §2)
// from config.yaml for the given ref.
func clientIDFor(ref IdentityRef) (string, error) {
	cfg, err := config.Load()
	if err != nil {
		return "", err
	}
	ident, ok := cfg.Identities[ref.Identity]
	if !ok {
		return "", fmt.Errorf("identity %q not found", ref.Identity)
	}
	reg, ok := ident.Environments[ref.Environment]
	if !ok || reg.ClientID == "" {
		return "", fmt.Errorf("no client_id for %s in %s", ref.Identity, ref.Environment)
	}
	return reg.ClientID, nil
}
