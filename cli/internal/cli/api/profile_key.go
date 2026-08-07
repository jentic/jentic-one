package api

import (
	"errors"
	"fmt"
	"os"
	"strings"

	"github.com/charmbracelet/huh"
	"github.com/charmbracelet/x/term"
	"github.com/jentic/jentic-one/cli/internal/config"
	"github.com/jentic/jentic-one/cli/internal/cli/prompt"
	"github.com/jentic/jentic-one/cli/internal/profile"
	"github.com/jentic/jentic-one/cli/internal/theme"
	"github.com/spf13/cobra"
)

// apiKeyPrefix is the prefix the control-plane assigns to agent API keys.
const apiKeyPrefix = "jak_"

type addKeyOptions struct {
	baseURL string
	apiKey  string
	agentID string
}

func newProfileAddKeyCmd(app *app) *cobra.Command {
	opts := &addKeyOptions{}
	cmd := &cobra.Command{
		Use:   "add-key [name]",
		Short: "Create a profile authenticated by an agent API key",
		Long: "add-key stores an agent API key (jak_*) generated in the Jentic console as\n" +
			"a new profile. Unlike `jentic register`, no key is generated and no operator\n" +
			"approval is needed — the API key is sent directly as the bearer credential.\n" +
			"Pass --api-key for scripting, or run interactively to be prompted for it.",
		Args: cobra.MaximumNArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			name := ""
			if len(args) == 1 {
				name = args[0]
			}
			return app.profileAddKeyE(cmd, name, opts)
		},
	}
	cmd.Flags().StringVar(&opts.baseURL, "base-url", "", "Jentic control-plane base URL")
	cmd.Flags().StringVar(&opts.apiKey, "api-key", "", "agent API key (jak_*); prompted when omitted on a terminal")
	cmd.Flags().StringVar(&opts.agentID, "agent-id", "", "agent id this key belongs to (optional, for display)")
	return cmd
}

func (a *app) profileAddKeyE(_ *cobra.Command, name string, opts *addKeyOptions) error {
	cfg, err := config.Load(a.Paths)
	if err != nil {
		return err
	}
	profileName := cfg.ResolvedProfileName(name)
	baseURL := cfg.ResolvedBaseURLOr(opts.baseURL)
	apiKey := strings.TrimSpace(opts.apiKey)

	if apiKey == "" {
		if !term.IsTerminal(os.Stdin.Fd()) {
			return errors.New("no API key given; pass --api-key or run interactively")
		}
		if err := promptAPIKey(&apiKey); err != nil {
			if errors.Is(err, huh.ErrUserAborted) {
				fmt.Fprintln(a.Out, theme.Dim.Render("Cancelled."))
				return nil
			}
			return err
		}
		apiKey = strings.TrimSpace(apiKey)
	}

	if err := validateAPIKey(apiKey); err != nil {
		return err
	}

	p, err := profile.Open(a.Paths, profileName)
	if err != nil {
		return err
	}
	meta, err := p.LoadMeta()
	if err != nil {
		return err
	}
	meta.AuthMode = profile.AuthModeAPIKey
	meta.BaseURL = baseURL
	if opts.agentID != "" {
		meta.AgentID = opts.agentID
	}
	if err := p.SaveMeta(meta); err != nil {
		return err
	}
	if err := p.SaveAPIKey(apiKey); err != nil {
		return err
	}

	fmt.Fprintln(a.Out, theme.Successf("Stored API key for profile %q", profileName))
	fmt.Fprintln(a.Out, theme.Field("base_url", valueOr(baseURL, "-")))
	if meta.AgentID != "" {
		fmt.Fprintln(a.Out, theme.Field("agent_id", meta.AgentID))
	}
	fmt.Fprintln(a.Out, theme.Field("key", maskAPIKey(apiKey)))
	fmt.Fprintf(a.Out, "\n%s %s\n", theme.Dim.Render("Ready:"),
		theme.Command.Render(fmt.Sprintf("jentic execute --profile %s <operation>", profileName)))
	return nil
}

// validateAPIKey rejects empty keys and keys without the expected prefix.
func validateAPIKey(key string) error {
	if key == "" {
		return errors.New("API key must not be empty")
	}
	if !strings.HasPrefix(key, apiKeyPrefix) {
		return fmt.Errorf("API key must start with %q (agent keys are generated in the Jentic console)", apiKeyPrefix)
	}
	return nil
}

// promptAPIKey collects the API key interactively, masking the input.
func promptAPIKey(key *string) error {
	return prompt.NewForm(
		huh.NewGroup(
			prompt.Input().Title("Agent API key").
				Description("Paste the jak_ key generated in the Jentic console.").
				EchoMode(huh.EchoModePassword).
				Value(key).Validate(validateAPIKey),
		),
	).WithShowHelp(true).Run()
}

// maskAPIKey renders a key as its prefix plus the last 4 chars, hiding the body.
func maskAPIKey(key string) string {
	if len(key) <= len(apiKeyPrefix)+4 {
		return apiKeyPrefix + "…"
	}
	return key[:len(apiKeyPrefix)] + "…" + key[len(key)-4:]
}

// apiKeyLabel masks a stored key, or reports its absence.
func apiKeyLabel(key string) string {
	if key == "" {
		return "missing"
	}
	return maskAPIKey(key)
}
