package ctlcmd

import (
	"testing"

	"github.com/jentic/jentic-one/cli/internal/config"
)

// A headless install (no TTY, prompt can't render) must never silently enable
// telemetry: a first-run non-interactive call defaults to OFF and records the
// decision, matching the "absent config = OFF" consent contract.
func TestEnsureTelemetryConsent_NonInteractiveFirstRunDefaultsOff(t *testing.T) {
	app := testApp(t)

	proceed, enabled, err := app.ensureTelemetryConsent(false)
	if err != nil {
		t.Fatalf("ensureTelemetryConsent: %v", err)
	}
	if !proceed {
		t.Fatalf("expected install to proceed on non-interactive run")
	}
	if enabled {
		t.Errorf("enabled = true, want false (headless first run must default OFF)")
	}

	cfg, err := config.Load(app.Paths)
	if err != nil {
		t.Fatalf("load config: %v", err)
	}
	if !cfg.Telemetry.HasConsented {
		t.Errorf("HasConsented = false, want true (decision must be recorded)")
	}
	if cfg.Telemetry.Enabled {
		t.Errorf("Enabled = true, want false (headless first run must default OFF)")
	}
}

// A non-interactive re-run must respect the previously saved choice rather than
// re-defaulting it either way.
func TestEnsureTelemetryConsent_NonInteractivePreservesSavedChoice(t *testing.T) {
	for _, enabled := range []bool{true, false} {
		app := testApp(t)
		cfg, err := config.Load(app.Paths)
		if err != nil {
			t.Fatalf("load config: %v", err)
		}
		cfg.Telemetry.HasConsented = true
		cfg.Telemetry.Enabled = enabled
		if err := cfg.Save(app.Paths); err != nil {
			t.Fatalf("save config: %v", err)
		}

		_, gotEnabled, err := app.ensureTelemetryConsent(false)
		if err != nil {
			t.Fatalf("ensureTelemetryConsent: %v", err)
		}
		if gotEnabled != enabled {
			t.Errorf("returned enabled = %v, want %v (saved choice must be preserved)", gotEnabled, enabled)
		}

		got, err := config.Load(app.Paths)
		if err != nil {
			t.Fatalf("reload config: %v", err)
		}
		if got.Telemetry.Enabled != enabled {
			t.Errorf("Enabled = %v, want %v (saved choice must be preserved)", got.Telemetry.Enabled, enabled)
		}
	}
}
