package install

import (
	"fmt"
	"path/filepath"
	"strconv"

	"gopkg.in/yaml.v3"
)

// The typed output structs below mirror the relevant subset of AppConfig
// (src/jentic_one/shared/config.py). Using structs (rather than maps) keeps the
// generated YAML field order deterministic and readable.

type dbEntry struct {
	Backend    string `yaml:"backend,omitempty"`
	Host       string `yaml:"host,omitempty"`
	Port       int    `yaml:"port,omitempty"`
	Name       string `yaml:"name,omitempty"`
	User       string `yaml:"user,omitempty"`
	Password   string `yaml:"password,omitempty"`
	Path       string `yaml:"path,omitempty"`
	SchemaName string `yaml:"schema_name"`
}

type databasesOut struct {
	Registry dbEntry `yaml:"registry"`
	Control  dbEntry `yaml:"control"`
	Admin    dbEntry `yaml:"admin"`
}

type runtimeOut struct {
	Debug    bool   `yaml:"debug"`
	LogLevel string `yaml:"log_level"`
}

type serverOut struct {
	Host   string `yaml:"host"`
	Port   int    `yaml:"port"`
	Reload bool   `yaml:"reload"`
}

type loggingOut struct {
	FileEnabled bool   `yaml:"file_enabled"`
	FileDir     string `yaml:"file_dir"`
	FileName    string `yaml:"file_name"`
}

type idSigningOut struct {
	KID           string `yaml:"kid"`
	PrivateKeyPEM string `yaml:"private_key_pem"`
}

type idpOut struct {
	Enabled               bool     `yaml:"enabled"`
	Provider              string   `yaml:"provider"`
	Issuer                string   `yaml:"issuer"`
	ClientID              string   `yaml:"client_id"`
	ClientSecret          string   `yaml:"client_secret"`
	Scopes                []string `yaml:"scopes"`
	AuthorizationEndpoint string   `yaml:"authorization_endpoint"`
	ExchangeEndpoint      string   `yaml:"exchange_endpoint"`
	UserinfoEndpoint      string   `yaml:"userinfo_endpoint"`
}

type authOut struct {
	CanonicalBaseURL string         `yaml:"canonical_base_url"`
	IDSigning        []idSigningOut `yaml:"id_signing,omitempty"`
	IDP              *idpOut        `yaml:"idp,omitempty"`
}

type adminAuthOut struct {
	JWTSecret string `yaml:"jwt_secret"`
}

type adminInviteOut struct {
	Pepper string `yaml:"pepper"`
}

type adminOut struct {
	Auth   adminAuthOut   `yaml:"auth"`
	Invite adminInviteOut `yaml:"invite"`
}

type encryptionEntryOut struct {
	ID       string `yaml:"id"`
	Material string `yaml:"material"`
}

type encryptionOut struct {
	ActiveID string               `yaml:"active_id"`
	Entries  []encryptionEntryOut `yaml:"entries"`
}

type connectOut struct {
	StateSecret string `yaml:"state_secret"`
}

type directOAuth2Out struct {
	Kind        string `yaml:"kind"`
	RedirectURI string `yaml:"redirect_uri"`
}

type providersOut struct {
	DirectOAuth2 directOAuth2Out `yaml:"direct_oauth2"`
}

type credentialsOut struct {
	Encryption encryptionOut `yaml:"encryption"`
	Providers  providersOut  `yaml:"providers"`
	Connect    connectOut    `yaml:"connect"`
}

type exporterOut struct {
	Exporter string `yaml:"exporter"`
}

type observabilityOut struct {
	Metrics exporterOut `yaml:"metrics"`
	Tracing exporterOut `yaml:"tracing"`
}

type searchOut struct {
	Enabled       bool   `yaml:"enabled"`
	SearchEnabled bool   `yaml:"search_enabled"`
	SearchMode    string `yaml:"search_mode"`
}

// telemetryOut mirrors the backend's AppConfig.telemetry gate. Rendering it
// explicitly (rather than omitting the block) is what bridges the install-time
// consent decision to the running app: the Python side reads telemetry.enabled
// from this generated jentic-one.yaml, so an absent block would leave telemetry
// OFF regardless of what the user answered. instance_id is written only when the
// user opted in (seeding the durable admin-DB identity row on first boot).
type telemetryOut struct {
	Enabled    bool   `yaml:"enabled"`
	InstanceID string `yaml:"instance_id,omitempty"`
}

type configOut struct {
	Databases     databasesOut     `yaml:"databases"`
	Runtime       runtimeOut       `yaml:"runtime"`
	Logging       *loggingOut      `yaml:"logging,omitempty"`
	Server        serverOut        `yaml:"server"`
	Apps          []string         `yaml:"apps,omitempty"`
	Auth          authOut          `yaml:"auth"`
	Admin         adminOut         `yaml:"admin"`
	Credentials   credentialsOut   `yaml:"credentials"`
	Observability observabilityOut `yaml:"observability"`
	Search        *searchOut       `yaml:"search,omitempty"`
	Telemetry     telemetryOut     `yaml:"telemetry"`
}

// encryptionOut builds the credentials.encryption block. When ReuseSecrets
// carried a keyset over from an existing config, render it verbatim so a
// hand-rotated multi-key keyset survives the rewrite; otherwise emit the
// default single-v1 layout populated by FillSecrets.
func (d *Draft) encryptionOut() encryptionOut {
	if d.EncryptionKeyset != nil {
		return *d.EncryptionKeyset
	}
	return encryptionOut{
		ActiveID: "v1",
		Entries:  []encryptionEntryOut{{ID: "v1", Material: d.EncryptionKey}},
	}
}

// authOut builds the auth config block, adding the SSO idp + id_signing key when
// SSO is enabled (mirrors config/local-sso.yaml).
func (d *Draft) authOut() authOut {
	out := authOut{CanonicalBaseURL: d.CanonicalBaseURL()}
	if !d.SSOEnabled {
		return out
	}
	out.IDSigning = []idSigningOut{{KID: d.IDSigningKID, PrivateKeyPEM: d.IDSigningKeyPEM}}
	out.IDP = &idpOut{
		Enabled:               true,
		Provider:              "google",
		Issuer:                "https://accounts.google.com",
		ClientID:              d.SSOClientID,
		ClientSecret:          d.SSOClientSecret,
		Scopes:                []string{"openid", "email", "profile"},
		AuthorizationEndpoint: "https://accounts.google.com/o/oauth2/v2/auth",
		ExchangeEndpoint:      "https://oauth2.googleapis.com/token",
		UserinfoEndpoint:      "https://openidconnect.googleapis.com/v1/userinfo",
	}
	return out
}

func atoiOr(s string, fallback int) int {
	n, err := strconv.Atoi(s)
	if err != nil {
		return fallback
	}
	return n
}

func (d *Draft) dbEntryFor(schema string) dbEntry {
	if d.IsPostgres() {
		// In the Docker path the app talks to the managed Postgres container over
		// the compose network: a fixed service host on the standard in-container
		// port (the user-chosen PGPort is published to the host, not used here).
		host, port := d.PGHost, atoiOr(d.PGPort, 5432)
		if d.IsDocker() {
			host, port = composeServiceDB, 5432
		}
		return dbEntry{
			Host:       host,
			Port:       port,
			Name:       d.PGName,
			User:       d.PGUser,
			Password:   d.PGPassword,
			SchemaName: schema,
		}
	}
	// SQLite files live on a host directory bind-mounted into the container under
	// the Docker path, so the config must reference the container-side path.
	dir := d.SQLiteDir
	if d.IsDocker() {
		dir = containerDataDir
	}
	return dbEntry{
		Backend:    BackendSQLite,
		Path:       filepath.ToSlash(filepath.Join(dir, schema+".db")),
		SchemaName: schema,
	}
}

// toConfig translates the Draft into the typed output config.
func (d *Draft) toConfig() configOut {
	// The container must bind all interfaces so the published port is reachable
	// from the host; the local path keeps the user-chosen bind host.
	serverHost := d.ServerHost
	if d.IsDocker() {
		serverHost = "0.0.0.0"
	}

	out := configOut{
		Databases: databasesOut{
			Registry: d.dbEntryFor("registry"),
			Control:  d.dbEntryFor("control"),
			Admin:    d.dbEntryFor("admin"),
		},
		Runtime: runtimeOut{Debug: d.Debug, LogLevel: d.LogLevel},
		Server: serverOut{
			Host:   serverHost,
			Port:   atoiOr(d.ServerPort, 8000),
			Reload: d.Debug,
		},
		Apps: d.Apps,
		Auth: d.authOut(),
		Admin: adminOut{
			Auth:   adminAuthOut{JWTSecret: d.AdminJWTSecret},
			Invite: adminInviteOut{Pepper: d.AdminInvitePepper},
		},
		Credentials: credentialsOut{
			Encryption: d.encryptionOut(),
			Providers: providersOut{
				DirectOAuth2: directOAuth2Out{
					Kind:        "direct_oauth2",
					RedirectURI: d.OAuthCallbackURL(),
				},
			},
			Connect: connectOut{StateSecret: d.ConnectStateSecret},
		},
		Observability: observabilityOut{
			Metrics: exporterOut{Exporter: d.MetricsExporter},
			Tracing: exporterOut{Exporter: d.TracingExporter},
		},
		Telemetry: telemetryOut{
			Enabled:    d.TelemetryEnabled,
			InstanceID: d.TelemetryInstanceID,
		},
	}

	if d.LogFileEnabled {
		dir := d.LogFileDir
		if dir == "" {
			dir = ".jentic/logs"
		}
		// Under Docker the log sink writes to a bind-mounted container directory.
		if d.IsDocker() {
			dir = containerLogsDir
		}
		name := d.LogFileName
		if name == "" {
			name = "app.jsonl"
		}
		out.Logging = &loggingOut{FileEnabled: true, FileDir: dir, FileName: name}
	}

	// Search is lexical (full-text / BM25) on both backends.
	out.Search = &searchOut{
		Enabled:       true,
		SearchEnabled: true,
		SearchMode:    "lexical",
	}

	return out
}

const configHeader = `# Generated by 'jenticctl install'.
# Mirrors jentic_one.shared.config.AppConfig. Secrets below are freshly
# generated for a first-time install and reused from an existing config on
# a reinstall (so encrypted data stays readable); treat them as sensitive
# and do not commit them to a deployed environment.
`

// Render returns the jentic-one.yaml bytes for this Draft. Call FillSecrets
// first so the secret fields are populated.
func (d *Draft) Render() ([]byte, error) {
	body, err := yaml.Marshal(d.toConfig())
	if err != nil {
		return nil, fmt.Errorf("marshal config: %w", err)
	}
	return append([]byte(configHeader), body...), nil
}
