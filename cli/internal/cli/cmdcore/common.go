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

// ResolveBaseURL loads the install config once and resolves the control-plane
// base URL, honouring an explicit flag value over the recorded install URL and
// the default. It reads the INSTALL record (~/.jentic/config.yaml base_url —
// where `jenticctl install` recorded the local deployment), not the identity
// store: the ctl tree's status/doctor probes target the local install, while
// identity resolution is the context's job.
func (a *App) ResolveBaseURL(baseURLFlag string) (string, error) {
	cfg, err := config.Load(a.Paths)
	if err != nil {
		return "", err
	}
	return cfg.ResolvedBaseURLOr(baseURLFlag), nil
}
