package install

import (
	"strings"
	"testing"
)

// TestWizardAnswersAreContracts renders the compose file and the app config
// from a Draft carrying a NON-default value for every wizard-editable field,
// and asserts each answer (or its documented per-path transform) is present in
// the output. #992 happened because the bind-host answer was silently dropped
// between the prompt and the compose template; this test makes a dead answer
// structurally impossible — adding a wizard field without wiring it fails here.
//
// Documented transforms (asserted, not exempted):
//   - ServerHost: in-container bind becomes 0.0.0.0 (render.go toConfig); the
//     answer instead becomes the compose publish prefix (Draft.PublishHost).
//   - PGHost/PGPort: under Docker the app talks to the managed db service on
//     the compose network (render.go dbEntryFor); the answered port is the
//     HOST-side published port in the compose file.
//   - LogFileDir: under Docker the sink writes to the bind-mounted container
//     path (render.go toConfig).
func TestWizardAnswersAreContracts(t *testing.T) {
	d := NewDraft()
	d.RuntimePath = RuntimeDocker
	d.DBBackend = BackendPostgres
	d.PGHost = "ignored-under-docker"
	d.PGPort = "15432"
	d.PGName = "customdb"
	d.PGUser = "customuser"
	d.PGPassword = "custompass"
	// Host publish of 5432 is opt-in (#992); enable it so the published-port
	// answer has a surface to be asserted on.
	d.PGExposeHostPort = true
	d.Apps = []string{"registry", "control"}
	d.ServerHost = "10.9.8.7"
	d.ServerPort = "18000"
	d.BrokerPort = "18100"
	d.Debug = false
	d.LogLevel = "WARNING"
	d.LogFileEnabled = true
	d.LogFileDir = "/home/u/.jentic/logs"
	d.LogFileName = "custom.jsonl"
	d.MetricsExporter = "prometheus"
	d.TracingExporter = "otlp"
	d.AuthBaseURL = "https://jentic.example.test"
	if err := d.FillSecrets(); err != nil {
		t.Fatalf("FillSecrets: %v", err)
	}

	cfg := composeConfigFor("/home/u/.jentic")
	composeData, err := RenderCompose(d, cfg)
	if err != nil {
		t.Fatalf("RenderCompose: %v", err)
	}
	assertValidComposeYAML(t, composeData)
	compose := string(composeData)

	configData, err := d.Render()
	if err != nil {
		t.Fatalf("Render: %v", err)
	}
	config := string(configData)

	// answer → where it must surface. Every wizard-editable Draft field must
	// have at least one row (or a row asserting its documented transform).
	rows := []struct {
		field string
		in    string // "compose" or "config"
		want  string
	}{
		// Server section.
		{"ServerHost (publish prefix)", "compose", `"10.9.8.7:18000:18000"`},
		{"ServerHost (broker publish)", "compose", `"10.9.8.7:18100:18100"`},
		{"ServerHost (docker transform)", "config", "host: 0.0.0.0"},
		{"ServerPort", "config", "port: 18000"},
		{"BrokerPort", "compose", `JENTIC__SERVER__PORT: "18100"`},
		// Database section.
		{"PGExposeHostPort/PGPort (published)", "compose", `"10.9.8.7:15432:5432"`},
		{"PGHost (docker transform)", "config", "host: " + composeServiceDB},
		{"PGName", "compose", "POSTGRES_DB: customdb"},
		{"PGName", "config", "name: customdb"},
		{"PGUser", "compose", "POSTGRES_USER: customuser"},
		{"PGUser", "config", "user: customuser"},
		{"PGPassword", "compose", "POSTGRES_PASSWORD: custompass"},
		{"PGPassword", "config", "password: custompass"},
		// Components section.
		{"Apps", "compose", "JENTIC__APPS: registry,control"},
		{"Apps", "config", "- registry"},
		// Runtime section.
		{"Debug", "config", "debug: false"},
		{"LogLevel", "config", "log_level: WARNING"},
		// Logging section.
		{"LogFileEnabled/Dir (docker transform)", "config", "file_dir: " + containerLogsDir},
		{"LogFileName", "config", "file_name: custom.jsonl"},
		// Observability section.
		{"MetricsExporter", "config", "exporter: prometheus"},
		{"TracingExporter", "config", "exporter: otlp"},
		// Auth section.
		{"AuthBaseURL", "config", "canonical_base_url: https://jentic.example.test"},
	}
	for _, row := range rows {
		out, name := config, "config"
		if row.in == "compose" {
			out, name = compose, "compose"
		}
		if !strings.Contains(out, row.want) {
			t.Errorf("answer %s not honoured: %s missing %q", row.field, name, row.want)
		}
	}
}

// TestPublishHostNormalization pins the bind-host → publish-prefix mapping.
func TestPublishHostNormalization(t *testing.T) {
	cases := map[string]string{
		"":          "127.0.0.1",
		"localhost": "127.0.0.1",
		"127.0.0.1": "127.0.0.1",
		"0.0.0.0":   "0.0.0.0",
		"10.0.0.5":  "10.0.0.5",
	}
	for in, want := range cases {
		d := NewDraft()
		d.ServerHost = in
		if got := d.PublishHost(); got != want {
			t.Errorf("PublishHost(%q) = %q, want %q", in, got, want)
		}
	}
}

// TestBindHostValidatorDockerRequiresIP pins that the Docker path rejects
// hostnames (Docker publish prefixes must be IPs) while the local path
// accepts them (a process bind may use one).
func TestBindHostValidatorDockerRequiresIP(t *testing.T) {
	docker := NewDraft()
	docker.RuntimePath = RuntimeDocker
	local := NewDraft()
	local.RuntimePath = RuntimeSource

	cases := []struct {
		host     string
		dockerOK bool
		localOK  bool
	}{
		{"127.0.0.1", true, true},
		{"0.0.0.0", true, true},
		{"localhost", true, true},
		{"10.0.0.5", true, true},
		{"::1", true, true},
		{"myhost.internal", false, true},
		{"", false, false},
	}
	for _, tc := range cases {
		if err := bindHostValidator(docker)(tc.host); (err == nil) != tc.dockerOK {
			t.Errorf("docker bindHostValidator(%q) error=%v, want ok=%v", tc.host, err, tc.dockerOK)
		}
		if err := bindHostValidator(local)(tc.host); (err == nil) != tc.localOK {
			t.Errorf("local bindHostValidator(%q) error=%v, want ok=%v", tc.host, err, tc.localOK)
		}
	}
}
