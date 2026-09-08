// Command jentic is the API-spec CLI for the Jentic platform. It registers and
// switches agent identities, browses and imports APIs from the public catalog
// into the local registry, inspects operations, and executes against them.
package main

import "github.com/jentic/jentic-one/cli/internal/cli/api"

func main() {
	api.ExecuteAPI()
}
