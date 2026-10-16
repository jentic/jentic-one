package install

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"reflect"
	"testing"
)

func TestHostPortOf(t *testing.T) {
	tests := map[string]string{
		"127.0.0.1:5432:5432": "5432",
		"0.0.0.0:8000:8000":   "8000",
		"8900:8900":           "8900",
		"[::1]:8000:8000":     "8000",
		"8000:8000/tcp":       "8000",
		"5432":                "",
		"8000-8001:8000-8001": "",
	}
	for mapping, want := range tests {
		if got := hostPortOf(mapping); got != want {
			t.Errorf("hostPortOf(%q) = %q, want %q", mapping, got, want)
		}
	}
}

// writeRenderedCompose renders the real compose template for a Postgres
// install with the db port published, so the port parser is pinned against
// what `jenticctl install` actually writes.
func writeRenderedCompose(t *testing.T) string {
	t.Helper()
	d := &Draft{
		RuntimePath: RuntimeDocker, DBBackend: BackendPostgres, Apps: []string{"control"},
		ServerPort: "8000", BrokerPort: "8900", PGPort: "5432", PGExposeHostPort: true,
		PGUser: "jentic", PGName: "jentic", PGPassword: "pw", ServerHost: "127.0.0.1",
	}
	out, err := RenderCompose(d, ComposeConfig{ConfigHostPath: "/c", LogsHostDir: "/l"})
	if err != nil {
		t.Fatalf("render compose: %v", err)
	}
	path := filepath.Join(t.TempDir(), "docker-compose.yaml")
	if err := os.WriteFile(path, out, 0o600); err != nil {
		t.Fatal(err)
	}
	return path
}

func TestComposeHostPortsFromRenderedCompose(t *testing.T) {
	got, err := composeHostPorts(writeRenderedCompose(t))
	if err != nil {
		t.Fatalf("composeHostPorts: %v", err)
	}
	if want := []string{"5432", "8000", "8900"}; !reflect.DeepEqual(got, want) {
		t.Errorf("composeHostPorts = %v, want %v", got, want)
	}
}

// ForeignPortHolders reports other projects' containers and ignores the
// jentic project's own (the update recreates those).
func TestForeignPortHoldersExcludesOwnProject(t *testing.T) {
	path := writeRenderedCompose(t)
	orig := dockerPublishers
	t.Cleanup(func() { dockerPublishers = orig })
	dockerPublishers = func(_ context.Context, port string) (string, error) {
		switch port {
		case "5432":
			return "jentic-db-1\tjentic\ndev-db-1\tjentic-dev\n", nil
		case "8000":
			return "stray\t\n", nil
		}
		return "", nil
	}

	got, err := ForeignPortHolders(context.Background(), path)
	if err != nil {
		t.Fatalf("ForeignPortHolders: %v", err)
	}
	want := []PortHolder{
		{Port: "5432", Container: "dev-db-1", Project: "jentic-dev"},
		{Port: "8000", Container: "stray"},
	}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("ForeignPortHolders = %+v, want %+v", got, want)
	}
	if s := got[0].String(); s != `host port 5432 is already allocated by container dev-db-1 (compose project "jentic-dev")` {
		t.Errorf("holder string = %q", s)
	}
}

func TestForeignPortHoldersSurfacesLookupError(t *testing.T) {
	path := writeRenderedCompose(t)
	orig := dockerPublishers
	t.Cleanup(func() { dockerPublishers = orig })
	boom := errors.New("cannot connect to the docker daemon")
	dockerPublishers = func(context.Context, string) (string, error) { return "", boom }

	if _, err := ForeignPortHolders(context.Background(), path); !errors.Is(err, boom) {
		t.Errorf("err = %v, want the lookup error", err)
	}
}

func TestDetectPortConflict(t *testing.T) {
	tests := []struct {
		name, output, port string
		ok                 bool
	}{
		{"linux daemon", "Error response from daemon: driver failed programming external connectivity on endpoint jentic-db-1 (x): Bind for 0.0.0.0:5432 failed: port is already allocated", "5432", true},
		{"docker desktop", "Error response from daemon: Ports are not available: exposing port TCP 127.0.0.1:8000 -> 0.0.0.0:0: listen tcp 127.0.0.1:8000: bind: address already in use", "8000", true},
		{"host process", "listen tcp4 0.0.0.0:8900: bind: address already in use", "8900", true},
		{"unrelated failure", "alembic.util.exc.CommandError: Can't locate revision", "", false},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			port, ok := DetectPortConflict(tt.output)
			if port != tt.port || ok != tt.ok {
				t.Errorf("DetectPortConflict = (%q, %v), want (%q, %v)", port, ok, tt.port, tt.ok)
			}
		})
	}
}
