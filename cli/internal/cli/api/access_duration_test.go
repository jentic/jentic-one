package api

import "testing"

func TestParseAccessDuration(t *testing.T) {
	for _, tc := range []struct {
		in   string
		want int
	}{
		{"7d", 604800},
		{"8h", 28800},
		{"30m", 1800},
		{"45s", 45},
		{"3600", 3600},
		{" 2D ", 172800},
		{"999d", 999 * 86400},
	} {
		in, want := tc.in, tc.want
		got, err := parseAccessDuration(in)
		if err != nil || got != want {
			t.Errorf("parseAccessDuration(%q) = %d, %v; want %d", in, got, err, want)
		}
	}
	for _, in := range []string{"", "0d", "1000d", "1w", "1.5d", "-1h", "d"} {
		if _, err := parseAccessDuration(in); err == nil {
			t.Errorf("parseAccessDuration(%q) succeeded; want an error", in)
		}
	}
}

func TestParseIdleLimit(t *testing.T) {
	if got, err := parseIdleLimit("14d"); err != nil || got != 14*86400 {
		t.Errorf("parseIdleLimit(14d) = %d, %v", got, err)
	}
	for in, want := range map[string]int{"P14D": 14 * 86400, "P2W": 14 * 86400, "p1dt12h": 36 * 3600} {
		if got, err := parseIdleLimit(in); err != nil || got != want {
			t.Errorf("parseIdleLimit(%s) = %d, %v; want %d", in, got, err, want)
		}
	}
	for _, in := range []string{"12h", "23h", "1000d", "PT12H", "P1M", "P1Y", "P"} {
		if _, err := parseIdleLimit(in); err == nil {
			t.Errorf("parseIdleLimit(%q) succeeded; want an error", in)
		}
	}
}

func TestISODurationRendersDaysAndTime(t *testing.T) {
	for seconds, want := range map[int]string{14 * 86400: "P14D", 36 * 3600: "P1DT12H"} {
		if got := isoDuration(seconds).String(); got != want {
			t.Errorf("isoDuration(%d) = %q; want %q", seconds, got, want)
		}
	}
}

func TestParseAccessDurationTakesISO8601(t *testing.T) {
	for in, want := range map[string]int{"P7D": 7 * 86400, "PT8H": 8 * 3600, "7d": 7 * 86400} {
		if got, err := parseAccessDuration(in); err != nil || got != want {
			t.Errorf("parseAccessDuration(%s) = %d, %v; want %d", in, got, err, want)
		}
	}
	for _, in := range []string{"PT0S", "P1000D", "P1M"} {
		if _, err := parseAccessDuration(in); err == nil {
			t.Errorf("parseAccessDuration(%q) succeeded; want an error", in)
		}
	}
}
