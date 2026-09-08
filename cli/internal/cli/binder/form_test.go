package binder_test

import (
	"testing"

	"github.com/jentic/jentic-one/cli/internal/cli/binder"
	"github.com/jentic/jentic-one/cli/internal/cli/ctl"
	"github.com/jentic/jentic-one/cli/internal/cli/ctl/generated"
)

// BuildDynamicForm must construct a form over the real generated config without
// panicking — pointer sections/leaves are allocated by the shared walk, so Bubble
// Tea can bind them (impl/6.1 §3, "prevent nil pointer panics"). We also confirm
// the sensitive exclusion the installer relies on is honoured (a secret leaf must
// not be prompted as a plain field).
func TestBuildDynamicForm_RealConfigNoPanicAndExcludesSecrets(t *testing.T) {
	sensitive, err := ctl.SensitivePaths()
	if err != nil {
		t.Fatalf("SensitivePaths: %v", err)
	}
	cfg := &generated.BackendConfig{}

	defer func() {
		if r := recover(); r != nil {
			t.Fatalf("BuildDynamicForm panicked on the real config: %v", r)
		}
	}()
	form := binder.BuildDynamicForm(cfg, binder.FormOptions{Exclude: sensitive})
	if form == nil {
		t.Fatal("expected a non-nil form")
	}
}

func TestBuildDynamicForm_RejectsNonStructPointer(t *testing.T) {
	defer func() {
		if recover() == nil {
			t.Error("BuildDynamicForm must panic on a non-struct-pointer target")
		}
	}()
	x := 3
	binder.BuildDynamicForm(&x, binder.FormOptions{})
}
