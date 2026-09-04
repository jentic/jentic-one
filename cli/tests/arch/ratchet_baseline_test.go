package arch

// legacyBaseline is the grandfathered count of naked fmt.Print* / log.Fatal* /
// log.Print* calls in the shipped command tree (internal/cli/{cmdcore,api,
// ctlcmd}).
// Measured: 0 — the shipped CLI routes all output through the
// App.Out/App.Err writers, so this rule is effectively strict today. The 1B
// guardrail forbids the number from *growing*.
//
// (os.Stdout is intentionally NOT part of this count — the stdout/stderr
// boundary test, 1F, governs os.Stdout with a precise render/bootstrap/
// subprocess allowlist.)
const legacyBaseline = 0
