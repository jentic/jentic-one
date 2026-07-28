package cmd

import (
	"fmt"
	"strings"

	"github.com/charmbracelet/huh"
	"github.com/jentic/jentic-one/cli/internal/install"
	"github.com/jentic/jentic-one/cli/internal/theme"
)

// promptAgentUserFields shows the config-dialog-style form with prefilled,
// editable fields for the agent account, mirroring promptOnboarding's shape. The
// two port toggles are bool selects (not confirms) so the whole thing is one
// multi-field form the operator can review at once. It has no huh.NewConfirm, so
// it may call .Run() directly (the confirm-run guard is file-scoped).
//
// configSrcs and providerSrcs are the ACTUAL operator files each toggle would
// copy (already resolved to existing paths by the caller), so the dialog names
// them rather than asking abstractly. When a list is empty the toggle offers only
// "No" with a "none found" note, so the operator isn't offered a copy of nothing.
// Each toggle's default (set by the caller) is Yes when there is something to
// copy, so the affirmative option is focused for the common case.
func (a *App) promptAgentUserFields(fields *agentUserFields, configSrcs []string, providerName string, providerSrcs []string) error {
	return install.NewForm(huh.NewGroup(
		install.Input().
			Title("Agent account name").
			Description("The dedicated Unix user the agent runs as.").
			Value(&fields.name).
			Validate(notEmptyField("account name")),
		install.Input().
			Title("Agent home directory").
			Description("Lives under a shared parent so you can be granted in without widening your home.\n"+
				"This directory will be owned by the agent's Unix account ("+fields.name+").").
			Value(&fields.homeDir).
			Validate(notEmptyField("home directory")),
		portSelect(
			"Copy your operator config into the agent's home?",
			"Gives the agent your settings. May include provider API keys stored locally.",
			configSrcs,
			&fields.portConfig,
		),
		portSelect(
			providerToggleTitle(providerName),
			"Lets the agent reach the same provider. May include long-lived credentials.",
			providerSrcs,
			&fields.portProvider,
		),
	)).WithShowHelp(true).Run()
}

// providerToggleTitle names the detected provider in the toggle title when known,
// so the operator sees what "provider config" means for their setup.
func providerToggleTitle(providerName string) string {
	if providerName == "" || providerName == "anthropic" {
		return "Copy your LLM provider config into the agent's home?"
	}
	return fmt.Sprintf("Copy your %s provider config into the agent's home?", providerName)
}

// portSelect builds a Yes/No bool select whose Description lists the concrete
// source paths that would be copied. With nothing to copy it degrades to a
// No-only select with a "none found" note, so the operator is never offered a
// copy of an empty set.
//
// The chain order matters: .Value() is set BEFORE .Options(). huh's .Options()
// sets both the selected index AND the viewport scroll offset from whatever the
// accessor currently holds; .Value() (chained after) fixes up the selected index
// but NOT the offset. So with Value-after-Options a Yes default lands with the
// viewport scrolled one line down — the cursor is on "Yes" but the widget renders
// "No" at the top with "Yes" hidden above it, until an arrow key resyncs the
// offset. Setting Value first makes .Options() compute both from the real default.
func portSelect(title, why string, srcs []string, value *bool) *huh.Select[bool] {
	if len(srcs) == 0 {
		*value = false
		return huh.NewSelect[bool]().
			Title(title).
			Description("None found in your home — nothing to copy.").
			Value(value).
			Options(huh.NewOption("No", false))
	}
	return huh.NewSelect[bool]().
		Title(title).
		Description(why+"\nWill copy: "+strings.Join(srcs, ", ")).
		Value(value).
		Options(huh.NewOption("Yes", true), huh.NewOption("No", false))
}

// printAgentRunInstructions closes the setup with the copy-paste launch command
// and the first-run permissions caveat, so the operator knows exactly how to
// start a session and why the home can't be broadly granted.
func (a *App) printAgentRunInstructions(agentID, homeDir string) {
	fmt.Fprintln(a.Out)
	fmt.Fprintln(a.Out, theme.Success.Render("Agent isolated. Start a session with:"))
	fmt.Fprintf(a.Out, "    %s\n", theme.Command.Render(fmt.Sprintf("cd %s; jentic run %s", homeDir, agentID)))
	fmt.Fprintln(a.Out)
	fmt.Fprintln(a.Out, theme.Dim.Render(
		"The first time you run the agent in a new directory, you'll be asked to grant it"))
	fmt.Fprintln(a.Out, theme.Dim.Render(
		"access to that workspace. Your home directory can't be broadly allowed — it holds"))
	fmt.Fprintln(a.Out, theme.Dim.Render(
		"jentic-one secrets — so grant specific project directories instead."))
}
