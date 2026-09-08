package config

import (
	"os"
	"path/filepath"
	"testing"
)

func TestNewPathsHonoursHomeEnv(t *testing.T) {
	t.Setenv(HomeEnv, "/tmp/custom-jentic")
	p, err := NewPaths()
	if err != nil {
		t.Fatalf("NewPaths: %v", err)
	}
	if p.Root != "/tmp/custom-jentic" {
		t.Fatalf("Root = %q, want /tmp/custom-jentic", p.Root)
	}
}

func TestNewPathsDefaultsToHome(t *testing.T) {
	t.Setenv(HomeEnv, "")
	home := t.TempDir()
	t.Setenv("HOME", home)
	p, err := NewPaths()
	if err != nil {
		t.Fatalf("NewPaths: %v", err)
	}
	if want := filepath.Join(home, dirName); p.Root != want {
		t.Fatalf("Root = %q, want %q", p.Root, want)
	}
}

func TestPathsLocations(t *testing.T) {
	p := Paths{Root: "/root"}
	cases := map[string]string{
		"Dir":               p.Dir(),
		"DataDir":           p.DataDir(),
		"LogsDir":           p.LogsDir(),
		"VenvPath":          p.VenvPath(),
		"SrcPath":           p.SrcPath(),
		"CACertPath":        p.CACertPath(),
		"CATrustedMarker":   p.CATrustedMarkerPath(),
		"CAKeyPath":         p.CAKeyPath(),
		"ConfigPath":        p.ConfigPath(),
		"InstallConfigPath": p.InstallConfigPath(),
		"AppPIDPath":        p.AppPIDPath(),
		"ComposePath":       p.ComposePath(),
		"ProfilesDir":       p.ProfilesDir(),
	}
	want := map[string]string{
		"Dir":               "/root",
		"DataDir":           "/root/data",
		"LogsDir":           "/root/logs",
		"VenvPath":          "/root/venv",
		"SrcPath":           "/root/src",
		"CACertPath":        "/root/ca.pem",
		"CATrustedMarker":   "/root/ca.trusted",
		"CAKeyPath":         "/root/ca.key",
		"ConfigPath":        "/root/config.yaml",
		"InstallConfigPath": "/root/jentic-one.yaml",
		"AppPIDPath":        "/root/app.pid",
		"ComposePath":       "/root/docker-compose.yaml",
		"ProfilesDir":       "/root/profiles",
	}
	for name, got := range cases {
		if got != want[name] {
			t.Errorf("%s = %q, want %q", name, got, want[name])
		}
	}
}

func TestEnsureCreatesDir(t *testing.T) {
	p := Paths{Root: t.TempDir()}
	dir, err := p.Ensure(p.DataDir())
	if err != nil {
		t.Fatalf("Ensure: %v", err)
	}
	if dir != p.DataDir() {
		t.Fatalf("Ensure returned %q, want %q", dir, p.DataDir())
	}
	info, err := os.Stat(dir)
	if err != nil || !info.IsDir() {
		t.Fatalf("expected %q to be created as a directory (err=%v)", dir, err)
	}
}

func TestBackupName(t *testing.T) {
	cases := map[string]string{
		"jentic-one.yaml": "jentic-one-old.yaml",
		"config.yaml":     "config-old.yaml",
		"noext":           "noext-old",
	}
	for in, want := range cases {
		if got := BackupName(in); got != want {
			t.Errorf("BackupName(%q) = %q, want %q", in, got, want)
		}
	}
}

func TestBackupNextTo(t *testing.T) {
	cases := map[string]string{
		"":                            "",
		"/root/jentic-one.yaml":       "/root/jentic-one-old.yaml",
		"jentic-one.yaml":             "jentic-one-old.yaml",
		"/etc/jentic/jentic-one.yaml": "/etc/jentic/jentic-one-old.yaml",
	}
	for in, want := range cases {
		if got := BackupNextTo(in); got != want {
			t.Errorf("BackupNextTo(%q) = %q, want %q", in, got, want)
		}
	}
}

func TestAppURL(t *testing.T) {
	cases := []struct {
		name    string
		baseURL string
		route   string
		want    string
	}{
		{"login", "http://127.0.0.1:8000", "login", "http://127.0.0.1:8000/app/login"},
		{"setup", "http://127.0.0.1:8000", "setup", "http://127.0.0.1:8000/app/setup"},
		{"agent deep link", "http://127.0.0.1:8000", "agents/agnt_123", "http://127.0.0.1:8000/app/agents/agnt_123"},
		{"trailing slash base", "http://127.0.0.1:8000/", "login", "http://127.0.0.1:8000/app/login"},
		{"leading slash route", "http://127.0.0.1:8000", "/login", "http://127.0.0.1:8000/app/login"},
		{"empty route is SPA root", "http://127.0.0.1:8000", "", "http://127.0.0.1:8000/app"},
		{"whitespace trimmed", " http://127.0.0.1:8000 ", " login ", "http://127.0.0.1:8000/app/login"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := AppURL(tc.baseURL, tc.route); got != tc.want {
				t.Errorf("AppURL(%q, %q) = %q, want %q", tc.baseURL, tc.route, got, tc.want)
			}
		})
	}
}
