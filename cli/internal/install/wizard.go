package install

import (
	"strings"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/huh"
	"github.com/charmbracelet/lipgloss"
	"github.com/jentic/jentic-one/cli/internal/theme"
)

// phase is the wizard's current screen.
type phase int

const (
	phaseDeploy phase = iota // page 1: run locally / docker
	phaseHub                 // section menu with live detail pane
	phaseEdit                // editing one section's form
)

const (
	hubListWidth = 22
	maxFormWidth = 72
)

// Header carries the version metadata shown in the wizard's top-right panel:
// the CLI's own version and the server's version when one is already running.
type Header struct {
	CLIVersion    string
	ServerVersion string
	ServerRunning bool
}

// wizard is the hub-and-spoke install TUI: a deployment page, then a menu of
// configuration sections each drilling into a small form, ending in "Continue".
type wizard struct {
	draft  *Draft
	header Header
	phase  phase

	deploy *huh.Form // page 1

	cursor int       // hub cursor; == len(Sections) means the Continue row
	edit   *huh.Form // active section editor
	editIx int

	// edited flips once the user has opened any section editor; the hub's
	// quit keys then require a confirmation (UX-9) so a reflexive `q` (pager
	// muscle memory) can't discard minutes of form-filling instantly. huh
	// binds values live, so even an esc'd section may have changed the draft.
	edited      bool
	confirmQuit bool // waiting on the quit confirmation keypress

	confirmed bool
	done      bool
	width     int
}

// RunWizard runs the interactive install wizard, mutating d in place. It returns
// true when the user chose Continue (proceed with the install), false if they
// quit/cancelled. hdr supplies the CLI/server versions shown in the top-right.
func RunWizard(d *Draft, hdr Header) (bool, error) {
	w := &wizard{draft: d, header: hdr, width: 80}
	w.deploy = newForm(deployGroups(d), w.formWidth())

	m, err := tea.NewProgram(w).Run()
	if err != nil {
		return false, err
	}
	return m.(*wizard).confirmed, nil
}

// Init clears the terminal so the wizard opens with the jentic logo anchored at
// the top, then starts the deployment form.
func (w *wizard) Init() tea.Cmd {
	return tea.Batch(tea.ClearScreen, w.deploy.Init())
}

func (w *wizard) formWidth() int {
	fw := w.width - hubListWidth - 6
	if w.phase != phaseHub && w.phase != phaseDeploy {
		fw = w.width - 4
	}
	if fw > maxFormWidth {
		fw = maxFormWidth
	}
	if fw < 30 {
		fw = 30
	}
	return fw
}

func (w *wizard) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	switch m := msg.(type) {
	case tea.WindowSizeMsg:
		w.width = m.Width
	case tea.KeyMsg:
		if w.confirmQuit {
			// Confirmation prompt is armed: only an explicit `y` quits; any
			// other key returns to the hub with the draft intact.
			if s := m.String(); s == "y" || s == "Y" {
				w.done = true
				return w, tea.Quit
			}
			w.confirmQuit = false
			return w, nil
		}
		switch m.String() {
		case "ctrl+c":
			// ctrl+c always cancels the whole wizard.
			w.done = true
			return w, tea.Quit
		case "q":
			// q quits from any non-text screen. The edit forms accept typed
			// input (tokens, ports), so q must reach them verbatim there.
			if w.phase != phaseEdit {
				return w.requestQuit()
			}
		}
	}

	switch w.phase {
	case phaseDeploy:
		return w.updateDeploy(msg)
	case phaseHub:
		return w.updateHub(msg)
	case phaseEdit:
		return w.updateEdit(msg)
	}
	return w, nil
}

func (w *wizard) updateDeploy(msg tea.Msg) (tea.Model, tea.Cmd) {
	m, cmd := w.deploy.Update(msg)
	if f, ok := m.(*huh.Form); ok {
		w.deploy = f
	}
	switch w.deploy.State {
	case huh.StateCompleted:
		w.phase = phaseHub
		return w, nil
	case huh.StateAborted:
		w.done = true
		return w, tea.Quit
	}
	return w, cmd
}

func (w *wizard) updateHub(msg tea.Msg) (tea.Model, tea.Cmd) {
	key, ok := msg.(tea.KeyMsg)
	if !ok {
		return w, nil
	}
	switch key.String() {
	case "up", "k":
		if w.cursor > 0 {
			w.cursor--
		}
	case "down", "j":
		if w.cursor < len(Sections) {
			w.cursor++
		}
	case "esc":
		return w.requestQuit()
	case "enter", " ", "l", "right":
		if w.cursor == len(Sections) { // Continue
			w.confirmed = true
			w.done = true
			return w, tea.Quit
		}
		w.editIx = w.cursor
		w.edit = newForm(Sections[w.editIx].Groups(w.draft), w.formWidth())
		w.edited = true // section editors bind live; treat any visit as an edit (UX-9)
		w.phase = phaseEdit
		return w, w.edit.Init()
	}
	return w, nil
}

// requestQuit handles a hub/deploy quit key: quit immediately when nothing has
// been edited (the deploy page and a pristine hub lose nothing), otherwise arm
// the confirmation prompt (UX-9).
func (w *wizard) requestQuit() (tea.Model, tea.Cmd) {
	if w.phase == phaseHub && w.edited {
		w.confirmQuit = true
		return w, nil
	}
	w.done = true
	return w, tea.Quit
}

func (w *wizard) updateEdit(msg tea.Msg) (tea.Model, tea.Cmd) {
	m, cmd := w.edit.Update(msg)
	if f, ok := m.(*huh.Form); ok {
		w.edit = f
	}
	// Either finishing or aborting (esc) the section editor returns to the hub;
	// huh binds values live, so partial edits are kept.
	if w.edit.State == huh.StateCompleted || w.edit.State == huh.StateAborted {
		w.phase = phaseHub
		w.edit = nil
		return w, nil
	}
	return w, cmd
}

func (w *wizard) View() string {
	if w.done {
		return ""
	}

	panel := theme.VersionPanel(w.header.CLIVersion, w.header.ServerVersion, w.header.ServerRunning)
	header := theme.LogoHeader(w.width, panel) + theme.Dim.Render("  onboarding wizard") + "\n\n"

	switch w.phase {
	case phaseDeploy:
		body := lipgloss.JoinHorizontal(
			lipgloss.Top,
			w.deploy.View(),
			w.detailBox(deployDetail(w.draft.RuntimePath)),
		)
		return header + body + "\n\n\n" + hint("↑/↓ move · enter select · q/esc quit")
	case phaseHub:
		return header + w.hubView()
	case phaseEdit:
		title := theme.Heading.Render(Sections[w.editIx].Title)
		return header + title + "\n\n" + w.edit.View() + "\n" +
			hint("enter confirm · esc back to menu")
	}
	return ""
}

func (w *wizard) hubView() string {
	rows := make([]string, 0, len(Sections)+2)
	for i, s := range Sections {
		rows = append(rows, w.row(i, s.Title))
	}
	rows = append(rows, "", w.row(len(Sections), "Continue →"))

	list := lipgloss.NewStyle().Width(hubListWidth).Render(strings.Join(rows, "\n"))

	var detail string
	if w.cursor < len(Sections) {
		detail = w.detailView(Sections[w.cursor])
	} else {
		detail = theme.Heading.Render("Continue") + "\n" +
			theme.Dim.Render("Write the configuration and run the install.")
	}

	body := lipgloss.JoinHorizontal(lipgloss.Top, list, w.detailBox(detail))
	if w.confirmQuit {
		warn := lipgloss.NewStyle().Foreground(theme.Orange).Bold(true)
		return body + "\n\n" + warn.Render("Quit without installing? Your section edits will be discarded.") +
			"\n" + hint("y quit · any other key stay")
	}
	return body + "\n\n" + hint("↑/↓ move · enter edit · q/esc quit")
}

// detailBox wraps right-side panel content in the shared left-bordered box used
// by both the deployment page and the hub, so the layout reads consistently.
func (w *wizard) detailBox(content string) string {
	return lipgloss.NewStyle().
		Width(w.formWidth()).
		BorderStyle(lipgloss.NormalBorder()).
		BorderForeground(theme.Muted).
		BorderLeft(true).
		PaddingLeft(2).
		Render(content)
}

// rowColors cycles the section list through the brand accents, matching the
// help screen's colourful command list.
var rowColors = []lipgloss.Color{theme.Green, theme.Blue, theme.Orange, theme.Pink, theme.Yellow, theme.Brand}

func (w *wizard) row(i int, label string) string {
	style := lipgloss.NewStyle().Foreground(rowColors[i%len(rowColors)])
	if i == w.cursor {
		return style.Bold(true).Render(theme.SelectOn + " " + label)
	}
	return style.Faint(true).Render(theme.SelectOff + " " + label)
}

func (w *wizard) detailView(s Section) string {
	out := theme.Heading.Render(s.Title) + "\n" + theme.Dim.Render(s.Blurb)
	lines := s.Summary(w.draft)
	if len(lines) > 0 {
		out += "\n\n" + strings.Join(lines, "\n")
	}
	return out
}

// newForm builds a section/deploy form with the shared brand theme, key map, and
// a fixed width so the hub layout stays stable.
func newForm(groups []*huh.Group, width int) *huh.Form {
	return NewForm(groups...).
		WithWidth(width).
		WithShowHelp(false)
}

func hint(s string) string { return theme.Dim.Render(s) }
