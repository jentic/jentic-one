package cmd

import (
	"fmt"

	"github.com/charmbracelet/huh"
	"github.com/jentic/jentic-one/cli/internal/install"
	"github.com/jentic/jentic-one/cli/internal/theme"
)

// promptAgentUserFields shows the config-dialog-style form with prefilled,
// editable fields for the agent account, mirroring promptOnboarding's shape. The
// two port toggles are bool selects (not confirms) so the whole thing is one
// multi-field form the operator can review at once. It has no huh.NewConfirm, so
// it may call .Run() directly (the confirm-run guard is file-scoped).
func (a *App) promptAgentUserFields(fields *agentUserFields) error {
	return install.NewForm(huh.NewGroup(
		install.Input().
			Title("Agent account name").
			Description("The dedicated Unix user the agent runs as.").
			Value(&fields.name).
			Validate(notEmptyField("account name")),
		install.Input().
			Title("Agent home directory").
			Description("Lives under a shared parent so you can be granted in without widening your home.").
			Value(&fields.homeDir).
			Validate(notEmptyField("home directory")),
		huh.NewSelect[bool]().
			Title("Copy your operator config into the agent's home?").
			Description("Gives the agent your settings. May include provider API keys stored locally.").
			Options(huh.NewOption("Yes", true), huh.NewOption("No", false)).
			Value(&fields.portConfig),
		huh.NewSelect[bool]().
			Title("Copy your LLM provider config into the agent's home?").
			Description("Lets the agent reach the same provider. May include long-lived credentials.").
			Options(huh.NewOption("Yes", true), huh.NewOption("No", false)).
			Value(&fields.portProvider),
	)).WithShowHelp(true).Run()
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
