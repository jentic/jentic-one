package api

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

func newProfileCmd(app *app) *cobra.Command {
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

func newProfileViewCmd(app *app) *cobra.Command {
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

func newProfileListCmd(app *app) *cobra.Command {
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

func newProfileUseCmd(app *app) *cobra.Command {
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
// profile store lives under, and whether it lives in the shared agent account's
// home. Operator-owned profiles have agent=false.
type profileRef struct {
	name  string
	paths config.Paths
	agent bool
}

// owned reports whether this profile lives in the agent account's home rather
// than the operator's own ~/.jentic.
func (r profileRef) owned() bool { return r.agent }

// discoverProfiles returns every profile visible to the operator: those in the
// operator's own ~/.jentic/profiles, plus those written into the shared agent
// account's home (<ConfigDir>/profiles) once that account exists. The operator
// can read the latter because account creation grants a recursive, inherited ACL
// over the agent home (see localagent.GrantOperatorHomeCmd). A name that exists
// in both sources is kept as distinct refs, disambiguated for display by owner.
func (a *app) discoverProfiles(cfg *config.FileConfig) ([]profileRef, error) {
	var refs []profileRef

	names, err := profile.List(a.Paths)
	if err != nil {
		return nil, err
	}
	for _, n := range names {
		refs = append(refs, profileRef{name: n, paths: a.Paths})
	}

	// Agent-owned profiles live in the single shared account's home.
	if acct, ok := cfg.AgentAccount(); ok && acct.ConfigDir != "" {
		ap := config.Paths{Root: acct.ConfigDir}
		agentNames, aerr := profile.List(ap)
		if aerr != nil {
			// An unreadable agent home shouldn't sink the whole listing.
			fmt.Fprintln(a.Err, theme.Dim.Render(fmt.Sprintf("  (skipping agent profiles: %v)", aerr)))
		}
		for _, n := range agentNames {
			refs = append(refs, profileRef{name: n, paths: ap, agent: true})
		}
	}

	sort.Slice(refs, func(i, j int) bool {
		if refs[i].name != refs[j].name {
			return refs[i].name < refs[j].name
		}
		return !refs[i].agent && refs[j].agent
	})
	return refs, nil
}

// activeRef resolves the currently active profile name to the ref that commands
// actually act on — the SAME precedence sessionPaths uses, so display marking
// never diverges from run-as routing. An operator-owned profile wins a name tie.
// Returns a zero ref (agent=false, empty name) when nothing is active or the
// active name resolves to no discovered profile.
func (a *app) activeRef(cfg *config.FileConfig) profileRef {
	active := cfg.ResolvedProfileName("")
	if active == "" {
		return profileRef{}
	}
	ref, found, err := a.findProfileRef(cfg, active)
	if err != nil || !found {
		return profileRef{name: active}
	}
	return ref
}

// isActive reports whether ref is the one activeRef resolved to, comparing both
// name and owner so an operator-owned and agent-owned profile of the same name
// are never both marked active.
func isActive(ref, active profileRef) bool {
	return ref.name == active.name && ref.agent == active.agent && active.name != ""
}

// findProfileRef resolves a profile name to its ref among the discovered
// profiles, preferring an operator-owned profile when the name is ambiguous.
func (a *app) findProfileRef(cfg *config.FileConfig, name string) (profileRef, bool, error) {
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
func (a *app) profileList() error {
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

	active := a.activeRef(cfg)
	for _, ref := range refs {
		// The active profile can now be operator- OR agent-owned: checking out an
		// agent-owned profile runs subsequent commands in-process as that agent.
		a.printProfileRow(ref, isActive(ref, active))
	}
	fmt.Fprintln(a.Out)
	label := active.name
	if active.owned() {
		label += " (agent)"
	}
	fmt.Fprintln(a.Out, theme.Dim.Render("active: ")+theme.Command.Render(label))
	return nil
}

// printProfileRow renders a single profile: a radio glyph + name header, then an
// indented summary read from its on-disk metadata and cached tokens.
func (a *app) printProfileRow(ref profileRef, active bool) {
	glyph := theme.Dim.Render(theme.SelectOff)
	if active {
		glyph = theme.Success.Render(theme.SelectOn)
	}
	header := glyph + " " + theme.Accent.Render(ref.name)
	if ref.owned() {
		header += " " + theme.Dim.Render("(agent)")
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
func (a *app) profileView(name string) error {
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
	a.printProfileRow(ref, isActive(ref, a.activeRef(cfg)))

	// Filesystem access is a property of the single shared agent account, not of
	// the individual profile: whichever profile is checked out, a `jentic run`
	// session reaches the same directories (the account's home + its grants). Show
	// that access whenever an account exists.
	acct, ok := cfg.AgentAccount()
	fmt.Fprintln(a.Out)
	if !ok || !acct.AccountCreated {
		fmt.Fprintln(a.Out, theme.Dim.Render("No local agent account is set up — no filesystem access to show."))
		return nil
	}

	fmt.Fprintln(a.Out, theme.Heading.Render("Filesystem access")+" "+theme.Dim.Render("(agent: "+acct.User+")"))
	dirs := localagent.SessionAccess(acct.HomeDir, acct.GrantedDirs)
	fmt.Fprint(a.Out, renderAccessTree(dirs))
	a.PrintRevokeHint()
	return nil
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

// profileSwitch persists the operator's default profile. With no name it opens
// the interactive picker on a terminal, or errors on a pipe/CI. It works off the
// same discovered set as `profile list` — operator-owned and agent-owned
// profiles alike are listed and selectable.
//
// Selecting an AGENT-OWNED profile checks it out for run-as: the operator's own
// default_profile is set to that name, and profile-scoped commands (`execute`,
// catalog) then resolve THAT profile's tokens from the agent store and call the
// control-plane in-process as the operator (loopback + the agent's bearer; see
// sessionPaths). The operator already holds a recursive ACL over the agent home,
// so this needs no re-exec or confinement. This is independent of the agent
// home's own default_profile, which is what `jentic run` injects as
// $JENTIC_PROFILE when launching the agent under its own Unix user.
func (a *app) profileSwitch(_ *cobra.Command, name string) error {
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

	active := a.activeRef(cfg)

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

	if err := config.SetDefaultProfile(a.Paths, chosen.name); err != nil {
		return err
	}
	name = chosen.name
	fmt.Fprintln(a.Out, theme.Successf("Active profile set to %q", name))
	if chosen.owned() {
		fmt.Fprintln(a.Out, theme.Dim.Render("This is an agent-owned profile; commands run in-process as that agent."))
	}
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
// errProfilePickAborted). The active profile may itself be agent-owned.
func (a *app) pickProfile(refs []profileRef, active profileRef) (profileRef, error) {
	items := make([]profileItem, 0, len(refs))
	start := 0
	for i, r := range refs {
		items = append(items, a.loadProfileItem(r))
		if isActive(r, active) {
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
// so cursor movement re-renders without touching disk. owned marks a profile that
// lives in the shared agent account's home, used to tag the row and detail pane.
type profileItem struct {
	name       string
	owned      bool
	registered bool
	apiKey     bool
	baseURL    string
	agentID    string
	agentName  string
	token      string
	keyLabel   string
}

// loadProfileItem reads a profile's metadata and token state for the detail
// pane, from the store the ref points at (the operator's ~/.jentic or the shared
// agent account's home).
func (a *app) loadProfileItem(ref profileRef) profileItem {
	it := profileItem{name: ref.name, owned: ref.agent}
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
	active       profileRef
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
// "(agent)" tag; the persisted active profile carries "(active)" (which may be
// the agent-owned one once it is checked out for run-as).
func (p *profilePicker) row(i int, it profileItem) string {
	tag := ""
	isActiveRow := it.name == p.active.name && it.owned == p.active.agent && p.active.name != ""
	if it.owned {
		tag = " " + theme.Dim.Render("(agent)")
	}
	if isActiveRow {
		tag += " " + theme.Dim.Render("(active)")
	}
	if i == p.cursor {
		return theme.Success.Render(theme.SelectOn) + " " + theme.Accent.Render(it.name) + tag
	}
	return theme.Dim.Render(theme.SelectOff+" "+it.name) + tag
}

// profileDetailView renders the right-hand details for the hovered profile.
func profileDetailView(it profileItem) string {
	out := theme.Heading.Render(it.name)
	if it.owned {
		out += " " + theme.Dim.Render("(agent)")
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
