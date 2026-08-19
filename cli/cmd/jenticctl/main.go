// Command jenticctl is the installer and lifecycle CLI for jentic-one. It stands
// up a local deployment (from source via uv, or in Docker via docker compose)
// and manages the running app: health checks, start/stop, log tailing, updates,
// and teardown.
package main

import "github.com/jentic/jentic-one/cli/internal/cli/ctlcmd"

func main() {
	ctlcmd.ExecuteCtl()
}
