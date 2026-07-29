package cmd

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
	"github.com/charmbracelet/x/term"
	"github.com/jentic/jentic-one/cli/internal/config"
	"github.com/jentic/jentic-one/cli/internal/localagent"
	"github.com/jentic/jentic-one/cli/internal/profile"
	"github.com/jentic/jentic-one/cli/internal/theme"
	"github.com/spf13/cobra"
)

func newProfileCmd(app *App) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "profile",
		Short: "View and switch the active profile",
		Long: "profile lists the local agent profiles under ~/.jentic/profiles and\n" +
			"switches which one commands act on by default. The active profile is the\n" +
			"--profile flag, else $JENTIC_PROFILE, else config.yaml default_profile.\n" +
			"Run bare on a terminal to pick interactively.",
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			return app.profileSwitch(cmd, "")
		},
	}
	cmd.AddCommand(newProfileListCmd(app))
	cmd.AddCommand(newProfileViewCmd(app))
	cmd.AddCommand(newProfileUseCmd(app))
	cmd.AddCommand(newProfileAddKeyCmd(app))
	return cmd
}

func newProfileViewCmd(app *App) *cobra.Command {
	return &cobra.Command{
		Use:   "view [name]",
		Short: "Show a profile's details and the directories its agent can access",
		Long: "Show a profile's details plus the map of every directory its agent can\n" +
			"reach. With no name it non-interactively shows the currently active profile —\n" +
			"so an agent can run `jentic profile view` to see exactly what it can access.",
		Args: cobra.MaximumNArgs(1),
		RunE: func(_ *cobra.Command, args []string) error {
			name := ""
			if len(args) == 1 {
				name = args[0]
			}
			return app.profileView(name)
		},
	}
}

func newProfileListCmd(app *App) *cobra.Command {
	return &cobra.Command{
		Use:     "list",
		Aliases: []string{"ls"},
		Short:   "List profiles and mark the active one",
		Args:    cobra.NoArgs,
		RunE: func(_ *cobra.Command, _ []string) error {
			return app.profileList()
		},
	}
}

func newProfileUseCmd(app *App) *cobra.Command {
	return &cobra.Command{
		Use:   "use [name]",
		Short: "Set the default profile (interactive picker when no name given)",
		Args:  cobra.MaximumNArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			name := ""
			if len(args) == 1 {
				name = args[0]
			}
			return app.profileSwitch(cmd, name)
		},
	}
}

// profileRef locates one discovered profile: its name, the Paths root the
// profile store lives under, and (for an agent-owned profile) the agent id whose
// home holds it. Operator-owned profiles have an empty agentID.
type profileRef struct {
	name    string
	paths   config.Paths
	agentID string
}

// owned reports whether this profile lives in an agent user's home rather than
// the operator's own ~/.jentic.
func (r profileRef) owned() bool { return r.agentID != "" }

// discoverProfiles returns every profile visible to the operator: those in the
// operator's own ~/.jentic/profiles, plus those an agent registered as its own
// Unix user wrote into its home (<ConfigDir>/profiles). The operator can read the
// latter because account creation grants a recursive, inherited ACL over the
// agent home (see localagent.GrantOperatorHomeCmd). A name that exists in more
// than one source is kept as distinct refs, disambiguated for display by owner.
func (a *App) discoverProfiles(cfg *config.FileConfig) ([]profileRef, error) {
	var refs []profileRef

	names, err := profile.List(a.Paths)
	if err != nil {
		return nil, err
	}
	for _, n := range names {
		refs = append(refs, profileRef{name: n, paths: a.Paths})
	}

	// Agent-owned profiles, one source per configured agent with a ConfigDir.
	for id, agent := range cfg.LocalAgents {
		if agent.ConfigDir == "" {
			continue // same-user agent: its identity is in the operator's config
		}
		ap := config.Paths{Root: agent.ConfigDir}
		agentNames, aerr := profile.List(ap)
		if aerr != nil {
			// An unreadable agent home shouldn't sink the whole listing.
			fmt.Fprintln(a.Err, theme.Dim.Render(fmt.Sprintf("  (skipping agent %s profiles: %v)", id, aerr)))
			continue
		}
		for _, n := range agentNames {
			refs = append(refs, profileRef{name: n, paths: ap, agentID: id})
		}
	}

	sort.Slice(refs, func(i, j int) bool {
		if refs[i].name != refs[j].name {
			return refs[i].name < refs[j].name
		}
		return refs[i].agentID < refs[j].agentID
	})
	return refs, nil
}

// findProfileRef resolves a profile name to its ref among the discovered
// profiles, preferring an operator-owned profile when the name is ambiguous.
func (a *App) findProfileRef(cfg *config.FileConfig, name string) (profileRef, bool, error) {
	refs, err := a.discoverProfiles(cfg)
	if err != nil {
		return profileRef{}, false, err
	}
	var match profileRef
	found := false
	for _, r := range refs {
		if r.name != name {
			continue
		}
		if !found || !r.owned() {
			match, found = r, true
		}
	}
	return match, found, nil
}

// profileList prints every profile with the active one marked by a filled radio
// ring, plus each profile's base URL, agent id, and token state.
func (a *App) profileList() error {
	cfg, err := config.Load(a.Paths)
	if err != nil {
		return err
	}
	refs, err := a.discoverProfiles(cfg)
	if err != nil {
		return err
	}

	fmt.Fprintln(a.Out, theme.Heading.Render("Profiles"))
	if len(refs) == 0 {
		fmt.Fprintln(a.Out, dotDown()+" "+theme.Dim.Render("no profiles yet — run `jentic register`"))
		return nil
	}

	active := cfg.ResolvedProfileName("")
	for _, ref := range refs {
		// Only an operator-owned profile can be the active one (the active
		// profile governs the operator's own commands).
		a.printProfileRow(ref, ref.name == active && !ref.owned())
	}
	fmt.Fprintln(a.Out)
	fmt.Fprintln(a.Out, theme.Dim.Render("active: ")+theme.Command.Render(active))
	return nil
}

// printProfileRow renders a single profile: a radio glyph + name header, then an
// indented summary read from its on-disk metadata and cached tokens.
func (a *App) printProfileRow(ref profileRef, active bool) {
	glyph := theme.Dim.Render(theme.SelectOff)
	if active {
		glyph = theme.Success.Render(theme.SelectOn)
	}
	header := glyph + " " + theme.Accent.Render(ref.name)
	if ref.owned() {
		header += " " + theme.Dim.Render("(agent: "+ref.agentID+")")
	}
	fmt.Fprintln(a.Out, header)

	p, err := profile.Open(ref.paths, ref.name)
	if err != nil {
		fmt.Fprintln(a.Out, "    "+theme.Warnf("unreadable: %v", err))
		return
	}
	meta, err := p.LoadMeta()
	if err != nil {
		fmt.Fprintln(a.Out, "    "+theme.Warnf("unreadable: %v", err))
		return
	}
	if meta.IsAPIKey() {
		fmt.Fprintln(a.Out, "    "+theme.Field("auth", "api-key"))
		fmt.Fprintln(a.Out, "    "+theme.Field("base_url", valueOr(meta.BaseURL, "-")))
		if meta.AgentID != "" {
			fmt.Fprintln(a.Out, "    "+theme.Field("agent_id", meta.AgentID))
		}
		key, _ := p.LoadAPIKey()
		fmt.Fprintln(a.Out, "    "+theme.Field("key", apiKeyLabel(key)))
		return
	}
	if meta.AgentID == "" {
		fmt.Fprintln(a.Out, "    "+theme.Dim.Render("not registered"))
		return
	}
	fmt.Fprintln(a.Out, "    "+theme.Field("base_url", valueOr(meta.BaseURL, "-")))
	fmt.Fprintln(a.Out, "    "+theme.Field("agent_id", meta.AgentID))
	tokens, _ := p.LoadTokens()
	state, _ := tokenStatus(tokens)
	fmt.Fprintln(a.Out, "    "+theme.Field("token", state))
}

// profileView prints one profile's details plus an ASCII tree of every directory
// a confined agent session can actually reach: the agent's own home and each
// granted dir (read/write), plus the executable routes on its PATH (read-only).
// The access set is sourced from localagent.SessionAccess — the SAME function the
// launcher's confinement builders use — so this display can never diverge from
// what the sandbox actually mounts. A directory reachable wholesale is shown with
// a trailing "/*", and nested paths collapse into their enclosing entry so the
// tree stays high-level.
//
// With no name it resolves the currently active profile (flag < JENTIC_PROFILE <
// configured default), so an agent can run bare `jentic profile view` to see the
// map of what it can access without knowing its own profile name.
func (a *App) profileView(name string) error {
	cfg, err := config.Load(a.Paths)
	if err != nil {
		return err
	}
	if name == "" {
		name = cfg.ResolvedProfileName("")
	}
	ref, found, err := a.findProfileRef(cfg, name)
	if err != nil {
		return err
	}
	if !found {
		return fmt.Errorf("profile %q does not exist; run `jentic profile list` to see options", name)
	}

	// Header + details (reuse the same summary the list row prints).
	active := cfg.ResolvedProfileName("")
	a.printProfileRow(ref, ref.name == active && !ref.owned())

	// Which agent's access does this profile represent? An agent-owned profile
	// names its agent directly; an operator profile is matched to a configured
	// agent by its registered agent id, if any.
	agentID, agent, ok := a.resolveProfileAgent(cfg, ref)
	fmt.Fprintln(a.Out)
	if !ok {
		fmt.Fprintln(a.Out, theme.Dim.Render("No local agent account is linked to this profile — no filesystem access to show."))
		return nil
	}

	fmt.Fprintln(a.Out, theme.Heading.Render("Filesystem access")+" "+theme.Dim.Render("(agent: "+agentID+")"))
	dirs := localagent.SessionAccess(agent.HomeDir, agent.GrantedDirs)
	fmt.Fprint(a.Out, renderAccessTree(dirs))
	a.printRevokeHint(agentID)
	return nil
}

// printRevokeHint prints a small footer, under any directory-access tree, telling
// the operator how to take a grant away again. It mirrors the "Granted (…)" line
// the grant flow prints, so revocation is always one command away from wherever
// access is shown. Kept generic (agentID may be empty) so callers that don't know
// the agent still get the shape of the command.
func (a *App) printRevokeHint(agentID string) {
	id := agentID
	if id == "" {
		id = "<agent>"
	}
	fmt.Fprintln(a.Out)
	fmt.Fprintln(a.Out, theme.Dim.Render("To take a directory away: `jentic run "+id+" --revoke <dir>` "+
		"(`--list-grants` to review)."))
}

// resolveProfileAgent finds the local-agent config entry whose access the profile
// represents: for an agent-owned profile that is the owning agent; for an
// operator profile it is the agent whose registered AgentID matches the profile's
// metadata (if any). Returns the agent id (the LocalAgents key), the entry, and
// whether a match was found.
func (a *App) resolveProfileAgent(cfg *config.FileConfig, ref profileRef) (string, config.LocalAgent, bool) {
	if ref.owned() {
		entry, ok := cfg.LocalAgent(ref.agentID)
		return ref.agentID, entry, ok
	}
	// An operator profile has no explicit agent link; the best-effort match is a
	// configured local agent whose id equals the profile name (the common case,
	// since `jentic run <id>` and the profile are usually named alike).
	if entry, ok := cfg.LocalAgent(ref.name); ok && entry.User != "" {
		return ref.name, entry, true
	}
	return "", config.LocalAgent{}, false
}

// renderAccessTree draws a high-level ASCII directory tree of everything a
// confined session can reach, as returned by localagent.SessionAccess. Read/write
// directories (the agent home and grants) render first; read-only executable
// routes render after under a dimmed "(read-only)" tag so the operator sees the
// full mounted set without mistaking the exec routes for writable grants. Each
// entry is shown with a trailing "/*" (whole-subtree access); a path nested inside
// another entry of the SAME access kind is folded into it so the tree stays
// high-level.
func renderAccessTree(dirs []localagent.SessionDir) string {
	var rw, ro []string
	for _, d := range dirs {
		c := filepath.Clean(d.Path)
		if c == "" || c == "." {
			continue
		}
		if d.Kind == localagent.AccessReadOnly {
			ro = append(ro, c)
		} else {
			rw = append(rw, c)
		}
	}
	rw = topLevelDirs(rw)
	ro = topLevelDirs(ro)
	if len(rw) == 0 && len(ro) == 0 {
		return "  " + theme.Dim.Render("(no directories)") + "\n"
	}

	var b strings.Builder
	// The read-only tag is a suffix on those rows; the last drawn row (of either
	// group) gets the └─ elbow.
	total := len(rw) + len(ro)
	i := 0
	draw := func(path, suffix string) {
		connector := "├─ "
		if i == total-1 {
			connector = "└─ "
		}
		i++
		fmt.Fprintf(&b, "%s%s%s\n", connector, theme.Accent.Render(path+"/*"), suffix)
	}
	for _, t := range rw {
		draw(t, "")
	}
	for _, t := range ro {
		draw(t, " "+theme.Dim.Render("(read-only)"))
	}
	return b.String()
}

// topLevelDirs de-duplicates, sorts, and drops any directory contained by another
// in the same set (the enclosing entry already covers it, since access is
// whole-subtree).
func topLevelDirs(paths []string) []string {
	seen := map[string]bool{}
	cleaned := make([]string, 0, len(paths))
	for _, p := range paths {
		if seen[p] {
			continue
		}
		seen[p] = true
		cleaned = append(cleaned, p)
	}
	sort.Strings(cleaned)

	var tops []string
	for _, c := range cleaned {
		contained := false
		for _, other := range cleaned {
			if other != c && localagent.IsUnderHome(other, c) {
				contained = true
				break
			}
		}
		if !contained {
			tops = append(tops, c)
		}
	}
	return tops
}

// profileSwitch persists the default profile. With no name it opens the
// interactive picker on a terminal, or errors on a pipe/CI. It works off the
// same discovered set as `profile list` — operator-owned and agent-owned
// profiles alike are listed.
//
// Selecting an AGENT-OWNED profile is not yet a completed action: making it the
// operator's active profile is meant to run every subsequent operation AS the
// agent's Unix user (a run-as mechanism). That is a substantial change tracked
// as a merge prerequisite in
// docs/security/local-agent/profile-run-as-agent-plan.md; until it lands, an
// agent-owned profile is viewable and selectable in the picker but the switch
// is refused with a pointer, rather than silently setting a default that would
// not run-as.
func (a *App) profileSwitch(_ *cobra.Command, name string) error {
	cfg, err := config.Load(a.Paths)
	if err != nil {
		return err
	}
	refs, err := a.discoverProfiles(cfg)
	if err != nil {
		return err
	}
	if len(refs) == 0 {
		return errors.New("no profiles found — run `jentic register` to create one")
	}

	active := cfg.ResolvedProfileName("")

	var chosen profileRef
	if name == "" {
		if !term.IsTerminal(os.Stdin.Fd()) {
			return errors.New("no profile name given; pass one (e.g. `jentic profile use <name>`) or run interactively")
		}
		selected, perr := a.pickProfile(refs, active)
		if perr != nil {
			if errors.Is(perr, errProfilePickAborted) {
				fmt.Fprintln(a.Out, theme.Dim.Render("Cancelled."))
				return nil
			}
			return perr
		}
		chosen = selected
	} else {
		ref, found, ferr := a.findProfileRef(cfg, name)
		if ferr != nil {
			return ferr
		}
		if !found {
			return fmt.Errorf("profile %q does not exist; run `jentic profile list` to see options", name)
		}
		chosen = ref
	}

	// Deferred: running operations as the agent user. See the plan doc above.
	if chosen.owned() {
		return fmt.Errorf("profile %q belongs to agent %q. Switching to it so operations run as that "+
			"agent isn't supported yet — see docs/security/local-agent/profile-run-as-agent-plan.md. "+
			"View it with `jentic profile view %s`", chosen.name, chosen.agentID, chosen.name)
	}

	if err := config.SetDefaultProfile(a.Paths, chosen.name); err != nil {
		return err
	}
	name = chosen.name
	fmt.Fprintln(a.Out, theme.Successf("Active profile set to %q", name))
	if env := os.Getenv(config.ProfileEnv); env != "" && env != name {
		fmt.Fprintln(a.Out, theme.Warnf("note: $%s=%q overrides this for the current shell", config.ProfileEnv, env))
	}
	fmt.Fprintln(a.Out, theme.Dim.Render("Override per-command with --profile or $"+config.ProfileEnv+"."))
	return nil
}

// errProfilePickAborted signals the interactive picker was cancelled (q/esc).
var errProfilePickAborted = errors.New("profile selection cancelled")

// pickProfile runs the interactive two-column picker: a list of profiles on the
// left and the highlighted profile's details on the right. It lists the same
// discovered set as `profile list` (operator- and agent-owned alike),
// pre-selects the active profile, and returns the chosen ref (or
// errProfilePickAborted). Only an operator-owned profile matches `active`.
func (a *App) pickProfile(refs []profileRef, active string) (profileRef, error) {
	items := make([]profileItem, 0, len(refs))
	start := 0
	for i, r := range refs {
		items = append(items, a.loadProfileItem(r))
		if r.name == active && !r.owned() {
			start = i
		}
	}

	m, err := tea.NewProgram(&profilePicker{items: items, cursor: start, active: active}).Run()
	if err != nil {
		return profileRef{}, err
	}
	res := m.(*profilePicker)
	if res.aborted {
		return profileRef{}, errProfilePickAborted
	}
	return refs[res.cursorAtDone], nil
}

// profileItem is a profile's display summary, loaded once before the picker runs
// so cursor movement re-renders without touching disk. owner is the agent id for
// an agent-owned profile (empty for operator-owned), used to tag the row and
// detail pane.
type profileItem struct {
	name       string
	owner      string
	registered bool
	apiKey     bool
	baseURL    string
	agentID    string
	agentName  string
	token      string
	keyLabel   string
}

// loadProfileItem reads a profile's metadata and token state for the detail
// pane, from the store the ref points at (the operator's ~/.jentic or an agent's
// home).
func (a *App) loadProfileItem(ref profileRef) profileItem {
	it := profileItem{name: ref.name, owner: ref.agentID}
	p, err := profile.Open(ref.paths, ref.name)
	if err != nil {
		return it
	}
	meta, err := p.LoadMeta()
	if err != nil {
		return it
	}
	if meta.IsAPIKey() {
		it.registered = true
		it.apiKey = true
		it.baseURL = meta.BaseURL
		it.agentID = meta.AgentID
		key, _ := p.LoadAPIKey()
		it.keyLabel = apiKeyLabel(key)
		return it
	}
	if meta.AgentID == "" {
		return it
	}
	it.registered = true
	it.baseURL = meta.BaseURL
	it.agentID = meta.AgentID
	it.agentName = meta.AgentName
	tokens, _ := p.LoadTokens()
	it.token, _ = tokenStatus(tokens)
	return it
}

const profileListWidth = 24

// profilePicker is the Bubble Tea model backing the interactive picker.
type profilePicker struct {
	items        []profileItem
	cursor       int
	active       string
	cursorAtDone int
	aborted      bool
	done         bool
}

func (p *profilePicker) Init() tea.Cmd { return nil }

func (p *profilePicker) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	m, ok := msg.(tea.KeyMsg)
	if !ok {
		return p, nil
	}
	switch m.String() {
	case "ctrl+c", "q", "esc":
		p.aborted = true
		p.done = true
		return p, tea.Quit
	case "up", "k":
		if p.cursor > 0 {
			p.cursor--
		}
	case "down", "j":
		if p.cursor < len(p.items)-1 {
			p.cursor++
		}
	case "enter", " ":
		p.cursorAtDone = p.cursor
		p.done = true
		return p, tea.Quit
	}
	return p, nil
}

func (p *profilePicker) View() string {
	if p.done {
		return ""
	}
	head := theme.Heading.Render("Select active profile") + "\n\n"

	rows := make([]string, 0, len(p.items))
	for i, it := range p.items {
		rows = append(rows, p.row(i, it))
	}
	list := lipgloss.NewStyle().Width(profileListWidth).Render(strings.Join(rows, "\n"))

	detailBox := lipgloss.NewStyle().
		BorderStyle(lipgloss.NormalBorder()).
		BorderForeground(theme.Muted).
		BorderLeft(true).
		PaddingLeft(2).
		Render(profileDetailView(p.items[p.cursor]))

	body := lipgloss.JoinHorizontal(lipgloss.Top, list, detailBox)
	return head + body + "\n\n" + theme.Dim.Render("↑/↓ move · enter select · q/esc cancel")
}

// row renders one profile in the left list: a filled ring + accent name for the
// hovered row, a hollow ring otherwise. An agent-owned profile carries an
// "(agent: id)" tag; the persisted operator profile carries "(active)".
func (p *profilePicker) row(i int, it profileItem) string {
	tag := ""
	if it.owner != "" {
		tag = " " + theme.Dim.Render("(agent: "+it.owner+")")
	} else if it.name == p.active {
		tag = " " + theme.Dim.Render("(active)")
	}
	if i == p.cursor {
		return theme.Success.Render(theme.SelectOn) + " " + theme.Accent.Render(it.name) + tag
	}
	return theme.Dim.Render(theme.SelectOff+" "+it.name) + tag
}

// profileDetailView renders the right-hand details for the hovered profile.
func profileDetailView(it profileItem) string {
	out := theme.Heading.Render(it.name)
	if it.owner != "" {
		out += " " + theme.Dim.Render("(agent: "+it.owner+")")
	}
	if !it.registered {
		return out + "\n" + theme.Dim.Render("not registered — run `jentic register`")
	}
	if it.apiKey {
		out += "\n" + theme.Field("auth", "api-key")
		out += "\n" + theme.Field("base_url", valueOr(it.baseURL, "-"))
		if it.agentID != "" {
			out += "\n" + theme.Field("agent_id", it.agentID)
		}
		out += "\n" + theme.Field("key", it.keyLabel)
		return out
	}
	out += "\n" + theme.Field("base_url", valueOr(it.baseURL, "-"))
	out += "\n" + theme.Field("agent_id", it.agentID)
	if it.agentName != "" {
		out += "\n" + theme.Field("name", it.agentName)
	}
	out += "\n" + theme.Field("token", it.token)
	return out
}
