package ctlcmd

import (
	"errors"
	"fmt"

	"github.com/charmbracelet/huh"
	"github.com/jentic/jentic-one/cli/internal/config"
	"github.com/jentic/jentic-one/cli/internal/install"
	"github.com/jentic/jentic-one/cli/internal/theme"
)

// ensureTelemetryConsent presents the telemetry consent prompt on every
// install, pre-selecting the previously saved answer so the user can confirm
// or change their mind. In non-interactive mode (CI, no TTY) the prompt can't
// render, so we keep any previously saved choice and default to OFF on first
// run — a headless install where the user could not explicitly opt in must not
// silently enable telemetry (matches the "absent config = OFF" contract). The
// decision is written back to config.yaml.
//
// Returns (proceed, enabled): proceed is false only when the user aborts (the
// caller should treat that as a cancelled install), and enabled is the consent
// answer the caller stamps into the generated jentic-one.yaml so the backend
// telemetry gate actually reflects the choice — config.yaml alone is the CLI's
// own file and the app never reads it.
func (a *app) ensureTelemetryConsent(interactive bool) (proceed bool, enabled bool, err error) {
	cfg, err := config.Load(a.Paths)
	if err != nil {
		return false, false, fmt.Errorf("load config: %w", err)
	}

	// Interactive first run pre-selects "on" in the prompt (opt-out UX); a
	// non-interactive first run stays OFF because no one answered. Either way an
	// already-consented instance reflects its saved choice.
	enabled = cfg.Telemetry.Enabled
	if interactive {
		if !cfg.Telemetry.HasConsented {
			enabled = true
		}
		fmt.Fprintln(a.Out, theme.Headingf("Telemetry"))
		fmt.Fprintln(a.Out, theme.Dim.Render("Jentic One optionally sends anonymous usage events under a random instance ID. No"))
		fmt.Fprintln(a.Out, theme.Dim.Render("hostnames, credentials, or personal data are ever included in the payload. The OS"))
		fmt.Fprintln(a.Out, theme.Dim.Render("family (linux/macOS/windows, anything else as \"other\") is reported on startup."))
		fmt.Fprintln(a.Out)
		fmt.Fprintln(a.Out, theme.Dim.Render("As with any HTTPS request, IP addresses may appear in standard server logs but"))
		fmt.Fprintln(a.Out, theme.Dim.Render("are never stored or persisted beyond that."))
		fmt.Fprintln(a.Out, theme.Dim.Render("For details: https://github.com/jentic/jentic-one#readme"))
		fmt.Fprintln(a.Out)

		if err := install.RunConfirm(
			huh.NewConfirm().
				Title("Allow Jentic to collect anonymous telemetry?").
				Description("Anonymized usage events only — absolutely no personal data, no API payloads.").
				Affirmative("Enable Telemetry").
				Negative("Disable Telemetry").
				Value(&enabled),
		); err != nil {
			if errors.Is(err, huh.ErrUserAborted) {
				fmt.Fprintln(a.Out)
				fmt.Fprintln(a.Out, theme.Warnf("Install cancelled — telemetry consent is required to proceed."))
				fmt.Fprintln(a.Out, theme.Dim.Render("Re-run `jenticctl install` when you're ready to make a choice."))
				return false, false, nil
			}
			return false, false, err
		}
		fmt.Fprintln(a.Out)
	}

	cfg.Telemetry.HasConsented = true
	cfg.Telemetry.Enabled = enabled
	if err := cfg.Save(a.Paths); err != nil {
		// Non-fatal: the user answered; we just can't persist it right now.
		fmt.Fprintln(a.Out, theme.Warnf("Could not save telemetry preference: %v", err))
	}
	return true, enabled, nil
}
