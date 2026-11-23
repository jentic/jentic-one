package api

import (
	"testing"
	"unicode/utf8"

	tea "github.com/charmbracelet/bubbletea"
)

func TestSearchLine_BackspaceRemovesWholeRune(t *testing.T) {
	cases := []struct {
		name string
		in   string
		want string
	}{
		{name: "two-byte utf8", in: "café", want: "caf"},
		{name: "three-byte utf8", in: "日本", want: "日"},
		{name: "four-byte emoji modifier", in: "👍🏽", want: "👍"},
		{name: "empty input", in: "", want: ""},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			s := searchLine{active: true, input: tc.in}
			act := s.handleKey(tea.KeyMsg{Type: tea.KeyBackspace})
			if act != searchNone {
				t.Errorf("action = %v, want searchNone", act)
			}
			if !s.active {
				t.Error("expected searchLine to remain active after backspace")
			}
			if s.input != tc.want {
				t.Errorf("input = %q, want %q", s.input, tc.want)
			}
			if !utf8.ValidString(s.input) {
				t.Errorf("input %q is not valid UTF-8", s.input)
			}
		})
	}
}

func TestSearchLine_Actions(t *testing.T) {
	s := searchLine{active: true}
	if act := s.handleKey(keyMsg("stripe")); act != searchNone {
		t.Fatalf("rune append action = %v, want searchNone", act)
	}
	if s.input != "stripe" || !s.active {
		t.Fatalf("after runes: input=%q active=%v", s.input, s.active)
	}

	if act := s.handleKey(tea.KeyMsg{Type: tea.KeyEscape}); act != searchCancel {
		t.Errorf("esc action = %v, want searchCancel", act)
	}
	if s.active {
		t.Error("esc should deactivate searchLine")
	}

	s = searchLine{active: true, input: "stripe"}
	if act := s.handleKey(tea.KeyMsg{Type: tea.KeyEnter}); act != searchSubmit {
		t.Errorf("enter action = %v, want searchSubmit", act)
	}
	if s.active {
		t.Error("enter should deactivate searchLine")
	}
	if s.input != "stripe" {
		t.Errorf("enter should preserve input, got %q", s.input)
	}

	s = searchLine{active: true, input: "stripe"}
	if act := s.handleKey(tea.KeyMsg{Type: tea.KeyCtrlC}); act != searchQuit {
		t.Errorf("ctrl+c action = %v, want searchQuit", act)
	}
}
