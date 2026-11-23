package ux

import (
	"fmt"
	"strings"
	"unicode/utf8"
)

// TerminalSafe replaces terminal control characters in s — C0 controls except
// '\n' and '\t', plus DEL (0x7f) and C1 controls (U+0080–U+009F) — with visible
// \uXXXX escapes, and replaces invalid UTF-8 sequences with U+FFFD.
func TerminalSafe(s string) string {
	var b strings.Builder
	b.Grow(len(s))
	for i := 0; i < len(s); {
		r, width := utf8.DecodeRuneInString(s[i:])
		i += width
		if r == utf8.RuneError && width == 1 {
			b.WriteRune(utf8.RuneError)
			continue
		}
		if (r < 0x20 && r != '\n' && r != '\t') || (r >= 0x7f && r <= 0x9f) {
			fmt.Fprintf(&b, "\\u%04x", r)
			continue
		}
		b.WriteRune(r)
	}
	return b.String()
}

// TerminalSafeBytes neutralizes terminal control characters and invalid UTF-8
// in b for human terminal output.
func TerminalSafeBytes(b []byte) []byte {
	return []byte(TerminalSafe(string(b)))
}
