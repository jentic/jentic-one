package install

import (
	"github.com/charmbracelet/huh"

	"github.com/jentic/jentic-one/cli/internal/cli/prompt"
	"github.com/jentic/jentic-one/cli/internal/theme"
)

// The wizard styles its output through the shared brand theme so it matches the
// help screen and the rest of the CLI. These locals are thin aliases kept for
// readability at call sites within this package.
var (
	headingStyle = theme.Heading
	stepStyle    = theme.Step
	commandStyle = theme.Command
	mutedStyle   = theme.Dim
	successStyle = theme.Success
	warnStyle    = theme.Warn
	errorStyle   = theme.Error
)

// The interactive-form helpers moved to internal/cli/prompt (impl/1.1 §1a) so
// the jentic tree can build prompts without importing this installer package.
// These aliases keep install's own callers (the wizard) working through the one
// shared, themed implementation.
var (
	// FormKeyMap is re-exported from internal/cli/prompt.
	FormKeyMap = prompt.FormKeyMap
	// FormTheme is re-exported from internal/cli/prompt.
	FormTheme = prompt.FormTheme
	// NewForm is re-exported from internal/cli/prompt.
	NewForm = prompt.NewForm
	// Input is re-exported from internal/cli/prompt.
	Input = prompt.Input
	// RunConfirm is re-exported from internal/cli/prompt.
	RunConfirm = prompt.RunConfirm
	// RunForm is re-exported from internal/cli/prompt.
	RunForm = prompt.RunForm
)

// PromptGlyph is re-exported from internal/cli/prompt.
const PromptGlyph = prompt.PromptGlyph

// Compile-time assurance the re-exported helpers keep their huh signatures.
var (
	_ func(...*huh.Group) *huh.Form = NewForm
	_ func() *huh.Input             = Input
)
