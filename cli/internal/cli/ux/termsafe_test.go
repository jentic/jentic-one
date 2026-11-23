package ux

import (
	"bytes"
	"strings"
	"testing"
	"unicode/utf8"
)

func TestTerminalSafe(t *testing.T) {
	cases := []struct {
		name      string
		in        string
		want      string
		forbidden []string
	}{
		{
			name:      "OSC 52 clipboard write",
			in:        "prefix \x1b]52;c;SGVsbG8=\x07 suffix",
			want:      `prefix \u001b]52;c;SGVsbG8=\u0007 suffix`,
			forbidden: []string{"\x1b", "\x07"},
		},
		{
			name:      "CSI clear screen and home",
			in:        "\x1b[2J\x1b[HHTTP 200 OK",
			want:      `\u001b[2J\u001b[HHTTP 200 OK`,
			forbidden: []string{"\x1b"},
		},
		{
			name:      "OSC 8 hyperlink",
			in:        "\x1b]8;;https://example.invalid\x1b\\click here\x1b]8;;\x1b\\",
			want:      `\u001b]8;;https://example.invalid\u001b\click here\u001b]8;;\u001b\`,
			forbidden: []string{"\x1b"},
		},
		{
			name:      "bare C1 CSI u009b",
			in:        "before \u009b2J after",
			want:      `before \u009b2J after`,
			forbidden: []string{"\u009b"},
		},
		{
			name:      "C1 range endpoints and DEL",
			in:        "\x7f\u0080\u009f",
			want:      `\u007f\u0080\u009f`,
			forbidden: []string{"\x7f", "\u0080", "\u009f"},
		},
		{
			name:      "invalid UTF-8 replaced with U+FFFD",
			in:        "bad\xff\xfeok",
			want:      "bad\ufffd\ufffdok",
			forbidden: []string{"\xff", "\xfe"},
		},
		{
			name:      "carriage return overwrite neutralized",
			in:        "real output\rforged line",
			want:      `real output\u000dforged line`,
			forbidden: []string{"\r"},
		},
		{
			name: "newlines tabs and printable unicode preserved",
			in:   "line 1\n\tindented ✓ café\nline 3",
			want: "line 1\n\tindented ✓ café\nline 3",
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got := TerminalSafe(tc.in)
			if got != tc.want {
				t.Errorf("TerminalSafe(%q) = %q, want %q", tc.in, got, tc.want)
			}
			if !utf8.ValidString(got) {
				t.Errorf("TerminalSafe(%q) produced invalid UTF-8: %q", tc.in, got)
			}
			for _, bad := range tc.forbidden {
				if strings.Contains(got, bad) {
					t.Errorf("TerminalSafe(%q) still contains %q in %q", tc.in, bad, got)
				}
			}

			gotBytes := TerminalSafeBytes([]byte(tc.in))
			if !bytes.Equal(gotBytes, []byte(tc.want)) {
				t.Errorf("TerminalSafeBytes(%q) = %q, want %q", tc.in, gotBytes, tc.want)
			}
		})
	}
}
