package api

import (
	"fmt"
	"regexp"
	"strconv"
	"strings"

	openapi_types "github.com/oapi-codegen/runtime/types"
)

// connectExpiresInMax is the longest duration a connect request may ask for
// (999 days, the console's largest custom amount); the route refuses more.
const connectExpiresInMax = 999 * 86400

// connectMaxIdleMin and connectMaxIdleMax bound an idle limit a connect may
// ask for (1 to 999 days); the route refuses anything else.
const (
	connectMaxIdleMin = 86400
	connectMaxIdleMax = 999 * 86400
)

// parseIdleLimit reads an idle limit like parseAccessDuration, between 1 and
// 999 days: last-use is recorded about hourly, so shorter limits can't be exact.
func parseIdleLimit(s string) (int, error) {
	seconds, err := parseAccessDuration(s)
	if err != nil {
		return 0, err
	}
	if seconds < connectMaxIdleMin || seconds > connectMaxIdleMax {
		return 0, fmt.Errorf("idle limit %q must be between 1 day and 999 days", s)
	}
	return seconds, nil
}

var accessDurationRe = regexp.MustCompile(`^(\d+)([smhd]?)$`)

// parseAccessDuration reads how long requested access should last — "7d",
// "8h", "30m", "45s", or a bare number of seconds — the same fixed units the
// console's duration control offers, or the ISO 8601 duration the API itself
// uses ("P7D", "PT8H"; no years or months: their length depends on the
// calendar). It returns whole seconds, between 1 and connectExpiresInMax.
func parseAccessDuration(s string) (int, error) {
	if trimmed := strings.ToUpper(strings.TrimSpace(s)); strings.HasPrefix(trimmed, "P") {
		seconds, err := parseISODurationSeconds(trimmed)
		if err != nil {
			return 0, err
		}
		if seconds <= 0 || seconds > connectExpiresInMax {
			return 0, fmt.Errorf("duration %q must be longer than zero and at most 999 days", s)
		}
		return seconds, nil
	}
	m := accessDurationRe.FindStringSubmatch(strings.ToLower(strings.TrimSpace(s)))
	if m == nil {
		return 0, fmt.Errorf("invalid duration %q: use a whole number with s, m, h or d, for example 7d", s)
	}
	n, err := strconv.Atoi(m[1])
	if err != nil {
		return 0, fmt.Errorf("invalid duration %q: %w", s, err)
	}
	unit := map[string]int{"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}[m[2]]
	if n <= 0 || n > connectExpiresInMax/unit {
		return 0, fmt.Errorf("duration %q must be longer than zero and at most 999 days", s)
	}
	return n * unit, nil
}

// parseISODurationSeconds reads a fixed-length ISO 8601 duration as whole
// seconds: weeks, days, hours, minutes and seconds, never years or months
// (their length depends on the calendar) or fractions.
func parseISODurationSeconds(s string) (int, error) {
	d, err := openapi_types.ParseDuration(s)
	if err != nil {
		return 0, fmt.Errorf("invalid ISO 8601 duration %q: use e.g. P14D or PT36H", s)
	}
	if d.Years != 0 || d.Months != 0 || d.Seconds != float64(int(d.Seconds)) {
		return 0, fmt.Errorf("invalid ISO 8601 duration %q: use weeks, days, hours, minutes or whole seconds", s)
	}
	return d.Weeks*7*86400 + d.Days*86400 + d.Hours*3600 + d.Minutes*60 + int(d.Seconds), nil
}

// isoDuration is a length of time in whole seconds as the API's ISO 8601
// duration, in days and a time part ("P14D", "PT8H", "P1DT12H").
func isoDuration(seconds int) *openapi_types.Duration {
	return &openapi_types.Duration{
		Days:    seconds / 86400,
		Hours:   seconds % 86400 / 3600,
		Minutes: seconds % 3600 / 60,
		Seconds: float64(seconds % 60),
	}
}
