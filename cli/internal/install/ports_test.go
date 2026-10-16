package install

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"reflect"
	"testing"
)

func TestHostBindingOf(t *testing.T) {
	tests := map[string]hostBinding{
		"127.0.0.1:5432:5432": {host: "127.0.0.1", port: "5432"},
		"0.0.0.0:8000:8000":   {host: "0.0.0.0", port: "8000"},
		"8900:8900":           {port: "8900"},
		"[::1]:8000:8000":     {host: "::1", port: "8000"},
		"8000:8000/tcp":       {port: "8000"},
		"5432":                {},
		"8000-8001:8000-8001": {},
	}
	for mapping, want := range tests {
		got, ok := hostBindingOf(mapping)
		if got != want || ok != (want.port != "") {
			t.Errorf("hostBindingOf(%q) = (%+v, %v), want %+v", mapping, got, ok, want)
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

func TestComposeHostBindingsFromRenderedCompose(t *testing.T) {
	got, err := composeHostBindings(writeRenderedCompose(t))
	if err != nil {
		t.Fatalf("composeHostBindings: %v", err)
	}
	want := []hostBinding{{"127.0.0.1", "5432"}, {"127.0.0.1", "8000"}, {"127.0.0.1", "8900"}}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("composeHostBindings = %v, want %v", got, want)
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
			return "jentic-db-1\tjentic\t127.0.0.1:5432->5432/tcp\ndev-db-1\tjentic-dev\t0.0.0.0:5432->5432/tcp, [::]:5432->5432/tcp\n", nil
		case "8000":
			return "stray\t\t127.0.0.1:8000->80/tcp\n", nil
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

// ForeignPortHolders compares the holder's published bind address with the
// stack's: a holder on a different, specific address does not conflict with
// the stack's 127.0.0.1 binding; a wildcard holder or an unreadable binding
// does.
func TestForeignPortHoldersComparesBindAddress(t *testing.T) {
	path := writeRenderedCompose(t)
	orig := dockerPublishers
	t.Cleanup(func() { dockerPublishers = orig })
	dockerPublishers = func(_ context.Context, port string) (string, error) {
		switch port {
		case "5432":
			return "lan-db\tother\t192.168.1.10:5432->5432/tcp\nv6-db\tother\t[::1]:5432->5432/tcp\n", nil
		case "8000":
			return "wild\tother\t[::]:8000->8000/tcp\n", nil
		case "8900":
			return "ranged\tother\t127.0.0.1:8890-8910->8890-8910/tcp\nopaque\tother\t\n", nil
		}
		return "", nil
	}

	got, err := ForeignPortHolders(context.Background(), path)
	if err != nil {
		t.Fatalf("ForeignPortHolders: %v", err)
	}
	want := []PortHolder{
		{Port: "8000", Container: "wild", Project: "other"},
		{Port: "8900", Container: "ranged", Project: "other"},
		{Port: "8900", Container: "opaque", Project: "other"},
	}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("ForeignPortHolders = %+v, want %+v", got, want)
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
