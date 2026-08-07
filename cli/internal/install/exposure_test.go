package install

import (
	"strings"
	"testing"
)

func TestUnqualifiedPublishes(t *testing.T) {
	compose := `
services:
  db:
    image: postgres:16
    ports:
      - "5432:5432"
  app:
    image: jentic-one/app:jentic-cli
    ports:
      - "127.0.0.1:8000:8000"
  broker:
    image: jentic-one/app:jentic-cli
    ports:
      - "8100:8100/tcp"
  v6:
    image: jentic-one/app:jentic-cli
    ports:
      - "[::1]:9000:9000"
  bare:
    image: jentic-one/app:jentic-cli
    ports:
      - "9100"
  none:
    image: jentic-one/app:jentic-cli
`
	got, err := UnqualifiedPublishes([]byte(compose))
	if err != nil {
		t.Fatalf("UnqualifiedPublishes: %v", err)
	}
	// A bare container port publishes an ephemeral host port on all
	// interfaces too, so it counts as unqualified alongside the host:container
	// pairs; prefixed mappings (IPv4 and bracketed IPv6) do not.
	want := []string{"bare 9100", "broker 8100:8100/tcp", "db 5432:5432"}
	if len(got) != len(want) {
		t.Fatalf("UnqualifiedPublishes = %v, want %v", got, want)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Errorf("UnqualifiedPublishes[%d] = %q, want %q", i, got[i], want[i])
		}
	}
}

// The compose files this CLI generates (post-#992) must never trip the check.
func TestUnqualifiedPublishesCleanOnGeneratedCompose(t *testing.T) {
	for _, backend := range []string{BackendPostgres, BackendSQLite} {
		d := NewDraft()
		d.RuntimePath = RuntimeDocker
		d.DBBackend = backend
		data, err := RenderCompose(d, composeConfigFor("/home/u/.jentic"))
		if err != nil {
			t.Fatalf("RenderCompose(%s): %v", backend, err)
		}
		got, err := UnqualifiedPublishes(data)
		if err != nil {
			t.Fatalf("UnqualifiedPublishes(%s): %v", backend, err)
		}
		if len(got) != 0 {
			t.Errorf("generated %s compose has unqualified publishes: %v", backend, got)
		}
	}
}

func TestUnqualifiedPublishesRejectsGarbage(t *testing.T) {
	if _, err := UnqualifiedPublishes([]byte(":\nnot yaml")); err == nil ||
		!strings.Contains(err.Error(), "parse compose file") {
		t.Errorf("expected a parse error, got %v", err)
	}
}
