package ctlcmd

import (
	"errors"
	"fmt"
	"os"

	"github.com/charmbracelet/huh"
	"github.com/charmbracelet/x/term"
	"github.com/jentic/jentic-one/cli/internal/install"
	"github.com/jentic/jentic-one/cli/internal/theme"
)

// credentialPrompt describes the labels for an interactive email+password form.
// setup and reset-password share the same three-field shape (email, password,
// confirm) and differ only in their wording, so the prompt itself lives here.
type credentialPrompt struct {
	heading       string
	subheading    string
	emailLabel    string
	passwordLabel string
}

// collectCredentials fills missing email/password fields. With skipPrompts (or a
// non-TTY stdin) it leaves them as-is for the caller to validate; otherwise it
// prompts using p's labels, masking the password and requiring a matching
// confirmation. The bound pointers receive the entered values.
func (a *app) collectCredentials(p credentialPrompt, skipPrompts bool, email, password *string) error {
	interactive := !skipPrompts && term.IsTerminal(os.Stdin.Fd())
	if !interactive {
		return nil
	}

	fmt.Fprintln(a.Out, theme.Headingf("%s", p.heading))
	fmt.Fprintln(a.Out, theme.Dim.Render(p.subheading))

	confirm := *password
	form := install.NewForm(
		huh.NewGroup(
			install.Input().Title(p.emailLabel).Value(email).
				Validate(func(s string) error {
					if s == "" {
						return errors.New("email is required")
					}
					return nil
				}),
			install.Input().Title(p.passwordLabel).EchoMode(huh.EchoModePassword).
				Value(password).
				Validate(func(s string) error {
					if len(s) < minPasswordLen {
						return fmt.Errorf("must be at least %d characters", minPasswordLen)
					}
					return nil
				}),
			install.Input().Title("Confirm password").EchoMode(huh.EchoModePassword).
				Value(&confirm).
				Validate(func(s string) error {
					if s != *password {
						return errors.New("passwords do not match")
					}
					return nil
				}),
		),
	)
	return form.Run()
}
