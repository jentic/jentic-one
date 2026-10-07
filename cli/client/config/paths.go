package config

import (
	"os"
	"path/filepath"
)

// ConfigDir resolves the Jentic config directory. It enforces the XDG layout
// (~/.config/jentic) on EVERY OS — including Windows, where it ignores %AppData% —
// so paths are predictable for Docker volume mounts and cross-machine docs
// (impl/1.2 §2). Honors XDG_CONFIG_HOME.
//
// Named ConfigDir (not Dir) deliberately: it forms a symmetric trio with
// CacheDir/StateDir and matches every impl-guide call site (config.ConfigDir());
// renaming only this one would break that symmetry.
//
//nolint:revive // intentional stutter; see the naming note above.
func ConfigDir() (string, error) {
	dir := os.Getenv("XDG_CONFIG_HOME")
	if dir == "" {
		home, err := os.UserHomeDir()
		if err != nil {
			return "", err
		}
		dir = filepath.Join(home, ".config")
	}
	return filepath.Join(dir, "jentic"), nil
}

// CacheDir resolves the Jentic cache directory. It honors XDG_CACHE_HOME first
// (OPS-1) so the cache location is predictable and overridable on EVERY OS —
// matching ConfigDir/StateDir, and important for Docker volume mounts and
// cross-machine docs where os.UserCacheDir's platform-native path (~/Library/
// Caches on macOS, %LocalAppData% on Windows) would ignore the operator's
// XDG_CACHE_HOME. When XDG_CACHE_HOME is unset it defers to os.UserCacheDir.
// Cache contents are disposable; never store tokens here (see StateDir).
func CacheDir() (string, error) {
	if dir := os.Getenv("XDG_CACHE_HOME"); dir != "" {
		return filepath.Join(dir, "jentic"), nil
	}
	dir, err := os.UserCacheDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(dir, "jentic"), nil
}

// StateDir resolves the XDG *state* directory (~/.local/state/jentic). OAuth
// tokens are STATE, not cache: they are active cryptographic sessions that MUST
// survive an OS cache purge (purging ~/.cache mid-run would destroy every agent's
// session and trigger a re-exchange storm), so tokens are never stored under
// CacheDir. Honors XDG_STATE_HOME; like ConfigDir it enforces the XDG layout on
// every OS.
func StateDir() (string, error) {
	dir := os.Getenv("XDG_STATE_HOME")
	if dir == "" {
		home, err := os.UserHomeDir()
		if err != nil {
			return "", err
		}
		dir = filepath.Join(home, ".local", "state")
	}
	return filepath.Join(dir, "jentic"), nil
}
