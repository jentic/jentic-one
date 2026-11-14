package auth

import "github.com/jentic/jentic-one/cli/client/config"

// writeFileAtomic delegates to config.WriteFileAtomic so credential and config
// files share the same 0600 create-temp, fsync, and rename implementation.
func writeFileAtomic(path string, data []byte) error {
	return config.WriteFileAtomic(path, data)
}
