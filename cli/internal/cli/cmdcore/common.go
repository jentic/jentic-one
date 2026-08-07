package cmdcore

import (
	"fmt"

	"github.com/jentic/jentic-one/cli/internal/config"
)

// ExitCodeError carries a wrapped child's non-zero exit code up to Execute so
// the CLI mirrors it without printing an "error:" line.
type ExitCodeError struct{ Code int }

func (e *ExitCodeError) Error() string { return fmt.Sprintf("child exited with code %d", e.Code) }

// ExitCode satisfies core.ExitCoder so core.Run mirrors a wrapped child's exit
// code verbatim.
func (e *ExitCodeError) ExitCode() int { return e.Code }

// NewExitCodeError constructs an ExitCodeError for the given child exit code.
func NewExitCodeError(code int) *ExitCodeError { return &ExitCodeError{Code: code} }

// ResolveIdentity loads the CLI config once and resolves the profile name and
// control-plane base URL, honouring explicit flag values over config/defaults.
func (a *App) ResolveIdentity(profileFlag, baseURLFlag string) (profileName, baseURL string, err error) {
	cfg, err := config.Load(a.Paths)
	if err != nil {
		return "", "", err
	}
	return cfg.ResolvedProfileName(profileFlag), cfg.ResolvedBaseURLOr(baseURLFlag), nil
}
