package prompt

import "testing"

func stubStdinTerminal(t *testing.T, tty bool) {
	t.Helper()
	orig := StdinIsTerminal
	StdinIsTerminal = func() bool { return tty }
	t.Cleanup(func() { StdinIsTerminal = orig })
}

// TestPromptable pins the CLI-wide prompt gate: a TTY is necessary but not
// sufficient — TERM=dumb would put huh into its uncancellable accessible mode
// (jentic-one#841), so it is treated like a piped stdin.
func TestPromptable(t *testing.T) {
	cases := []struct {
		name string
		term string
		tty  bool
		want bool
	}{
		{"tty with normal TERM", "xterm-256color", true, true},
		{"tty with empty TERM", "", true, true},
		{"tty with TERM=dumb", "dumb", true, false},
		{"piped stdin", "xterm-256color", false, false},
		{"piped stdin with TERM=dumb", "dumb", false, false},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			t.Setenv("TERM", tc.term)
			stubStdinTerminal(t, tc.tty)
			if got := Promptable(); got != tc.want {
				t.Fatalf("Promptable() = %v, want %v", got, tc.want)
			}
		})
	}
}
