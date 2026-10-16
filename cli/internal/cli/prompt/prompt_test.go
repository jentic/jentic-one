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
		// The gate normalises TERM (trim + case-fold), a conservative superset
		// of huh v1's exact os.Getenv("TERM") == "dumb" trigger: odd harness
		// spellings fall back to the always-available non-interactive path.
		{"uppercase DUMB is dumb", "DUMB", true, false},
		{"mixed-case Dumb is dumb", "Dumb", true, false},
		{"padded dumb is dumb", " dumb ", true, false},
		{"tab/newline padded dumb is dumb", "\tdumb\n", true, false},
		// A distinct terminfo name that merely starts with "dumb" is not the
		// dumb terminal, so it stays promptable.
		{"dumb-prefixed TERM is not dumb", "dumb-emacs", true, true},
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
