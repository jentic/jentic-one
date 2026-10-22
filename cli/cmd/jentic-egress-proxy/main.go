// Command jentic-egress-proxy is the egress proxy sidecar of
// `jentic run --container`. It runs only inside the session's proxy container
// (deploy/docker/run-proxy.Dockerfile), never on the operator's machine, and
// is a separate binary so the proxy's TLS-terminating code is not linked into
// the jentic command tree the agent itself runs.
package main

import (
	"os"

	"github.com/jentic/jentic-one/cli/internal/localagent/egressproxy"
)

func main() {
	os.Exit(egressproxy.Main(os.Args[1:], os.Stdin, os.Stderr))
}
