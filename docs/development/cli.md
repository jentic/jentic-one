# Developing the CLI

Working on the `jentic` and `jenticctl` binaries from a source checkout. What
the binaries are and how to use them is in [`cli/README.md`](../../cli/README.md);
every command and flag is in the generated [CLI reference](../reference/cli.md).

## Build from a checkout

```bash
cd cli
make build   # builds both binaries (jenticctl + jentic), stamping version/commit/date
make help    # list all targets (test, lint, check, cov, ...)
```

`make build-ctl` / `make build-api` build a single binary; `make check` runs
lint + vet + race tests. To force the [installer script](../installation/cli.md#one-line-installer-script)
down its from-source path instead, set `JENTIC_INSTALL_METHOD=source`.

To put the freshly built binaries on your `PATH`:

```bash
make build && sudo install -m 0755 jenticctl jentic /usr/local/bin/
# or, without installing, run them straight from the build directory:
export PATH="$PWD:$PATH"
```

## The generated CLI reference

[`docs/reference/cli.md`](../reference/cli.md) and
`ui/public/cli-reference.json` (rendered in the app at `/app/docs`) are both
generated from the cobra command tree by [`cli/cmd/clidocs`](../../cli/cmd/clidocs/).
After changing a command's `Use`, `Short`, `Long`, `Example`, or flags, run
from the repo root:

```bash
make cli-reference
```

and commit both files. `TestCommittedCLIReferenceUpToDate` and
`TestCommittedCLIMarkdownUpToDate` (in `cli/pkg/clitree`) and the CI `cli` job
fail on drift. Hidden commands and hidden flags are left out of both.

## Debugging the MCP server (Inspector loop)

`jentic mcp` serves the agent tool surface over **stdio MCP** — stdin/stdout
are the JSON-RPC wire, so you can't poke it from a shell. The fast debug loop
is the official Inspector, a local web UI that spawns the server exactly like
a real client would:

```bash
make build-api
npx @modelcontextprotocol/inspector ./jentic mcp
```

It opens a browser page where you can run `tools/list`, call tools with
hand-written arguments, read the `skill://` resources, and watch the raw
JSON-RPC frames. Handy checks while developing:

- `tools/list` works with **no config at all** (the "always boots"
  invariant), and `get_started` diagnoses the machine's setup state.
- `resources/read` on `skill://jentic` reports `one.jentic/source: hosted`
  when the configured backend answers, `bundled` offline / pre-auth.
- Flag behavior: `--read-only` withholds exactly `execute`;
  `--exclude-tools` drops what you name.

Server logs never touch stdout (it's the wire) — tail them with
`tail -f ~/.local/state/jentic/logs/mcp.log`, or point `--log-file`
somewhere else (pass flags after `jentic mcp` in the Inspector command line).

## Package map

Where to look when changing the CLI (each package's doc comment carries the
detail):

| Package | Owns |
| ------- | ---- |
| `internal/cli` | Command trees: `api` (the `jentic` tree), `ctl`/`ctlcmd` (`jenticctl`), `cmdcore` (shared command plumbing), `binder` (flag/form building from the OpenAPI spec), `apispec` (parsing the vendored control spec), `clictx`, `prompt`, `ux`, `localagentcmd`. |
| `internal/agentops` | The UX-free execute/inspect core both binaries share. |
| `internal/agentkey` | The agent's Ed25519 identity key: generation, storage, assertion signing. |
| `internal/config` | Filesystem paths (XDG layout) and default settings. |
| `internal/install` | The `jenticctl install` onboarding wizard. |
| `internal/localagent` | Teardown primitives `jentic reset` uses for agent accounts an earlier release created — see [its README](../../cli/internal/localagent/README.md). |
| `internal/mcpcfg` | Writing the `jentic mcp` server entry into supported agent configs. |
| `internal/skillgen` | Rendering the canonical agent skill. |
| `internal/serverinfo`, `internal/proc`, `internal/update`, `internal/theme` | Server probing, process helpers, `jenticctl update`, colour theme. |
| `pkg/core`, `pkg/clitree` | The public composition seam for building your own CLI binary — see [Extending Jentic One](extending-jentic-one.md). |

## Wizard structure

The wizard is a **hub-and-spoke TUI** ([`internal/install/wizard.go`](../../cli/internal/install/wizard.go),
built on [bubbletea](https://github.com/charmbracelet/bubbletea) +
[huh](https://github.com/charmbracelet/huh)): a deployment page, then a menu of
configuration **sections** with a live detail pane, each drilling into a small
form, ending in **Continue**.

To onboard a new `AppConfig` option:

1. Add a field to `Draft` in
   [`internal/install/draft.go`](../../cli/internal/install/draft.go) (with a
   default in `NewDraft`).
2. Add it to a `Section` in
   [`internal/install/sections.go`](../../cli/internal/install/sections.go): a
   huh field in the section's `Groups` (use `WithHideFunc` for conditional
   fields) and a line in its `Summary` for the detail pane. Add a whole new
   `Section` to the `Sections` slice to create a new hub row.
3. Map the field into the generated YAML in
   [`internal/install/render.go`](../../cli/internal/install/render.go).

No changes to the command wiring are needed — the hub is built from the
`Sections` registry automatically.
