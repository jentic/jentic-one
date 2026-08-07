package api

import (
	"crypto/ed25519"
	"crypto/x509"
	"encoding/pem"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"time"

	"github.com/spf13/cobra"

	"github.com/jentic/jentic-one/cli/client/auth"
	sdkconfig "github.com/jentic/jentic-one/cli/client/config"
	"github.com/jentic/jentic-one/cli/internal/cli/ux"
	legacyconfig "github.com/jentic/jentic-one/cli/internal/config"
	"github.com/jentic/jentic-one/cli/internal/profile"
)

// migratedMarkerName is dropped in the legacy root after a successful migration
// so a second run (and the pre-activation legacy-read adapter in clictx) knows
// the tree has been migrated (14 BC-1). Its presence never deletes anything —
// deletion is only via --purge-legacy.
const migratedMarkerName = "MIGRATED"

// newMigrateCmd builds `jentic migrate`: copy (never move) the legacy ~/.jentic
// profile store into the XDG Environment × Identity × Context model + XDG
// key/token state (14 BC-1/BC-2, plan Phase 3 item 2).
//
// Deliberately NOT fenced: BC-1 directs agents to run it (they receive an error
// envelope with actionable_step "run jentic migrate"), so fencing it would
// deadlock the very recovery it names. It IS bootstrap-safe so it runs when no
// XDG config exists yet.
//
// Semantics are COPY, not move: idempotent, leaves a MIGRATED marker, never
// deletes the legacy tree by default (so downgrading the binary keeps working);
// --purge-legacy removes the legacy tree once the downgrade path is no longer
// needed. Cached refresh tokens are dropped, not migrated (BC-6).
func newMigrateCmd(app *app) *cobra.Command {
	var purgeLegacy bool
	cmd := &cobra.Command{
		Use:   "migrate",
		Short: "Migrate legacy ~/.jentic profiles to the XDG context model",
		Long: "migrate copies each legacy profile into an Environment + Identity +\n" +
			"Context under ~/.config/jentic, and copies key/token material into the XDG\n" +
			"layout. It is idempotent and non-destructive (the legacy tree survives for\n" +
			"downgrade); --purge-legacy removes the old tree afterwards. Cached refresh\n" +
			"tokens are intentionally dropped.",
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			aud := ux.FromContext(cmd.Context())
			// --purge-legacy is destructive (deletes ~/.jentic). Gate it behind the
			// Audience confirmation so agents must pass --yes and humans get a [y/N].
			if purgeLegacy {
				ok, cerr := aud.AskConfirm("Delete the legacy ~/.jentic tree after migrating? This removes the downgrade path.")
				if cerr != nil {
					return reportCoded(aud, cerr)
				}
				if !ok {
					purgeLegacy = false
				}
			}
			res, err := runMigrate(app, purgeLegacy)
			if err != nil {
				return reportCoded(aud, err)
			}
			aud.Render(ux.Result{
				Status:   ux.StatusUpdated,
				Resource: "migration",
				Message:  res.message(),
				Fields: map[string]any{
					"migrated_contexts": res.contexts,
					"active_context":    res.active,
					"purged_legacy":     res.purged,
					"legacy_root":       res.legacyRoot,
				},
			})
			return nil
		},
	}
	cmd.Flags().BoolVar(&purgeLegacy, "purge-legacy", false, "Delete the legacy ~/.jentic profile tree after a successful migration")
	// --yes is consumed by the root interceptor (Audience assumeYes); bind it so
	// the lookup finds it, without a dead local (F8-23).
	cmd.Flags().BoolP("yes", "y", false, "Proceed without interactive confirmation")
	return cmd
}

// migrateResult summarizes what a migration run did, for the rendered envelope.
type migrateResult struct {
	contexts   []string
	active     string
	purged     bool
	legacyRoot string
}

func (r migrateResult) message() string {
	if len(r.contexts) == 0 {
		return "nothing to migrate (no legacy profiles found)"
	}
	msg := fmt.Sprintf("migrated %d context(s); key material was COPIED (legacy originals kept). "+
		"Re-run with --purge-legacy to remove the old tree once you no longer need to downgrade.", len(r.contexts))
	if r.purged {
		msg = fmt.Sprintf("migrated %d context(s) and purged the legacy tree.", len(r.contexts))
	}
	return msg
}

// runMigrate is the engine: it walks the operator's legacy profiles (and the
// agent account's own profiles, BC-2) and writes them into the XDG model. It is
// idempotent — re-running overlays the same entries and re-copies key material.
func runMigrate(app *app, purgeLegacy bool) (migrateResult, error) {
	var res migrateResult
	res.legacyRoot = app.Paths.Root

	cfg, err := legacyconfig.Load(app.Paths)
	if err != nil {
		return res, fmt.Errorf("reading legacy config: %w", err)
	}

	// Collect every legacy profile visible: the operator's own, plus the shared
	// agent account's (its identity must not be stranded — BC-2).
	sources := []legacyconfig.Paths{app.Paths}
	if acct, ok := cfg.AgentAccount(); ok && acct.ConfigDir != "" && acct.ConfigDir != app.Paths.Root {
		sources = append(sources, legacyconfig.Paths{Root: acct.ConfigDir})
	}

	defaultProfile := cfg.ResolvedDefaultProfile()
	broker := brokerURLFromLegacy(cfg)

	// Accumulate the mutations, then apply them in a single MutateConfig so a
	// failure leaves config.yaml untouched.
	type pending struct {
		context  string
		env      string
		envURL   string
		identity string
		idType   string
		isActive bool
		copyKey  func() error
	}
	var plans []pending

	seenContexts := map[string]bool{}
	for _, src := range sources {
		names, lerr := profile.List(src)
		if lerr != nil {
			return res, fmt.Errorf("listing legacy profiles in %s: %w", src.Root, lerr)
		}
		for _, name := range names {
			p, oerr := profile.Open(src, name)
			if oerr != nil {
				return res, fmt.Errorf("opening legacy profile %q: %w", name, oerr)
			}
			meta, merr := p.LoadMeta()
			if merr != nil {
				return res, fmt.Errorf("reading legacy profile %q: %w", name, merr)
			}

			ctxName := sanitizeName(name)
			// Collisions across sources (operator + agent) keep the first; a true
			// name clash is rare and merging would be worse than skipping.
			if seenContexts[ctxName] {
				continue
			}
			seenContexts[ctxName] = true

			baseURL := meta.BaseURL
			if baseURL == "" {
				baseURL = cfg.ResolvedBaseURL()
			}
			envName := envNameFromURL(baseURL)
			identName := ctxName
			idType := "agent"
			if meta.IsAPIKey() {
				idType = "user"
			}

			ref := auth.IdentityRef{Identity: identName, Environment: envName}
			pCopy := p // capture
			plans = append(plans, pending{
				context:  ctxName,
				env:      envName,
				envURL:   baseURL,
				identity: identName,
				idType:   idType,
				isActive: name == defaultProfile,
				copyKey:  func() error { return copyProfileMaterial(pCopy, meta, ref) },
			})
		}
	}

	if len(plans) == 0 {
		// Still drop the marker so the legacy-read adapter stops firing and the
		// run is idempotent.
		_ = writeMigratedMarker(app.Paths.Root)
		return res, nil
	}

	// Copy key material first (outside the lock) — it is idempotent and a failure
	// here should not leave a half-written config.yaml.
	for _, pl := range plans {
		if err := pl.copyKey(); err != nil {
			return res, fmt.Errorf("copying material for %q: %w", pl.context, err)
		}
	}

	if err := sdkconfig.MutateConfig(func(x *sdkconfig.Config) error {
		for _, pl := range plans {
			x.Environments[pl.env] = mergeEnv(x.Environments[pl.env], pl.envURL, broker)
			x.Identities[pl.identity] = sdkconfig.Identity{Type: pl.idType}
			x.Contexts[pl.context] = sdkconfig.Context{
				Environment: pl.env,
				Identity:    pl.identity,
				Mode:        "human", // migrated contexts default to human; agent contexts are opt-in
			}
			if pl.isActive && x.ActiveContext == "" {
				x.ActiveContext = pl.context
			}
			res.contexts = append(res.contexts, pl.context)
		}
		// If no profile was the legacy default, fall back to the first migrated
		// context so the config always has a usable active_context.
		//
		// NOTE (telemetry consent): the V2 config schema does not yet model
		// telemetry (impl/1.2 §2 lists it as a migration target but the field is
		// not in schema.go). Carrying consent byte-for-byte (BC-2) therefore lands
		// with the telemetry field itself in a later phase; until then migration
		// does not touch consent, and the legacy tree (which still holds it) is
		// preserved by default, so nothing is lost.
		if x.ActiveContext == "" && len(res.contexts) > 0 {
			sort.Strings(res.contexts)
			x.ActiveContext = res.contexts[0]
		}
		res.active = x.ActiveContext
		return nil
	}); err != nil {
		return res, err
	}

	sort.Strings(res.contexts)

	if err := writeMigratedMarker(app.Paths.Root); err != nil {
		return res, fmt.Errorf("writing migration marker: %w", err)
	}

	if purgeLegacy {
		if err := os.RemoveAll(app.Paths.Root); err != nil {
			return res, fmt.Errorf("purging legacy tree: %w", err)
		}
		res.purged = true
	}

	return res, nil
}

// mergeEnv folds a discovered base/broker URL into any existing Env entry,
// filling only empty fields so a re-run (or a second profile sharing the env)
// never clobbers a value already set.
func mergeEnv(existing sdkconfig.Env, baseURL, broker string) sdkconfig.Env {
	if existing.BaseURL == "" {
		existing.BaseURL = baseURL
	}
	if existing.BrokerURL == "" {
		existing.BrokerURL = broker
	}
	return existing
}

// copyProfileMaterial copies one legacy profile's secret material into the XDG
// layout: the Ed25519 key -> keys/<stem>.key, the API key -> <stem>.apikey, and
// the access token -> <stem>_tokens.json (dropping the refresh token, BC-6). All
// copies are idempotent (overwrite in place) and skip absent material.
func copyProfileMaterial(p *profile.Profile, meta *profile.Meta, ref auth.IdentityRef) error {
	// Ed25519 signing key (DCR identities).
	if !meta.IsAPIKey() {
		if err := copyLegacyKey(p, ref); err != nil {
			return err
		}
		if err := copyLegacyAccessToken(p, ref); err != nil {
			return err
		}
		return nil
	}
	// API-key identities: copy the jak_* credential.
	key, err := p.LoadAPIKey()
	if err != nil {
		return fmt.Errorf("reading legacy API key: %w", err)
	}
	if key == "" {
		return nil
	}
	if err := auth.SaveAPIKey(ref, key); err != nil {
		return err
	}
	return nil
}

// copyLegacyKey reads the legacy PKCS#8 PEM agent.key, validates it is Ed25519,
// and writes it under the XDG keys dir via the SDK helper so the destination
// stem and perms match what auth reads back.
func copyLegacyKey(p *profile.Profile, ref auth.IdentityRef) error {
	data, err := os.ReadFile(p.KeyPath())
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return nil // unregistered profile with no key — nothing to copy
		}
		return fmt.Errorf("reading legacy key: %w", err)
	}
	block, _ := pem.Decode(data)
	if block == nil {
		return errors.New("legacy key is not valid PEM")
	}
	parsed, err := x509.ParsePKCS8PrivateKey(block.Bytes)
	if err != nil {
		return fmt.Errorf("parsing legacy key: %w", err)
	}
	if _, ok := parsed.(ed25519.PrivateKey); !ok {
		return errors.New("legacy key is not Ed25519")
	}
	dst, err := auth.KeyPathForImport(ref)
	if err != nil {
		return err
	}
	//nolint:gosec // dst is <config>/keys/<validated-stem>.key (KeyPathForImport re-validates via Stem's path-traversal guard); the 0600 key is the migration target.
	if err := os.WriteFile(dst, data, 0o600); err != nil {
		return fmt.Errorf("writing migrated key: %w", err)
	}
	return nil
}

// copyLegacyAccessToken copies the (non-expired-or-not) access token into the
// XDG token state, DROPPING the refresh token (BC-6). A missing/empty token is
// not an error — the JWT-bearer grant re-mints from the key on next use.
func copyLegacyAccessToken(p *profile.Profile, ref auth.IdentityRef) error {
	toks, err := p.LoadTokens()
	if err != nil {
		return fmt.Errorf("reading legacy tokens: %w", err)
	}
	if toks == nil || toks.AccessToken == "" {
		return nil
	}
	return auth.SaveTokens(ref, &auth.TokenSet{
		AccessToken: toks.AccessToken,
		ExpiresAt:   toks.AccessExpiresAt,
	})
}

// brokerURLFromLegacy composes the legacy broker.{scheme,host} into a single
// broker_url (BC-4). Empty host yields "" (no broker configured).
func brokerURLFromLegacy(cfg *legacyconfig.FileConfig) string {
	host := cfg.Broker.Host
	if host == "" {
		return ""
	}
	scheme := cfg.Broker.Scheme
	if scheme == "" {
		scheme = legacyconfig.DefaultBrokerScheme
	}
	return scheme + "://" + host
}

// writeMigratedMarker drops the MIGRATED marker in the legacy root (idempotent).
func writeMigratedMarker(legacyRoot string) error {
	if legacyRoot == "" {
		return nil
	}
	if err := os.MkdirAll(legacyRoot, 0o700); err != nil {
		return err
	}
	marker := filepath.Join(legacyRoot, migratedMarkerName)
	return os.WriteFile(marker, []byte(time.Now().UTC().Format(time.RFC3339)+"\n"), 0o600)
}

var nonNameChars = regexp.MustCompile(`[^a-z0-9-]+`)

// sanitizeName maps an arbitrary legacy profile name to the V2 charset
// (^[a-z0-9][a-z0-9-]{0,63}$): lowercase, non-charset runs -> "-", trimmed, and
// prefixed with "x" if it does not start with an alnum. Idempotent on names that
// already satisfy the charset.
func sanitizeName(name string) string {
	s := strings.ToLower(strings.TrimSpace(name))
	s = nonNameChars.ReplaceAllString(s, "-")
	s = strings.Trim(s, "-")
	if s == "" {
		s = "default"
	}
	if c := s[0]; (c < 'a' || c > 'z') && (c < '0' || c > '9') {
		s = "x" + s
	}
	if len(s) > 64 {
		s = s[:64]
		s = strings.TrimRight(s, "-")
	}
	return s
}

// envNameFromURL derives a sanitized environment name from a base URL's host
// (BC-1: e.g. api.jentic.com -> api-jentic-com). Falls back to "default" when
// the URL has no usable host.
func envNameFromURL(baseURL string) string {
	host := baseURL
	if i := strings.Index(host, "://"); i != -1 {
		host = host[i+len("://"):]
	}
	if i := strings.IndexAny(host, "/?#"); i != -1 {
		host = host[:i]
	}
	// Drop a port — env names are hosts, not host:port.
	if i := strings.LastIndex(host, ":"); i != -1 {
		host = host[:i]
	}
	if host == "" {
		return "default"
	}
	return sanitizeName(host)
}
