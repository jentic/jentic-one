package install

import (
	"fmt"
	"sort"
	"strings"

	"gopkg.in/yaml.v3"
)

// UnqualifiedPublishes parses docker-compose.yaml bytes and returns a line per
// port mapping published without a host-IP prefix, as "<service> <mapping>".
//
// Docker publishes an unqualified mapping ("8000:8000") on ALL interfaces —
// and bypasses UFW while doing it — so on an install whose operator chose a
// loopback bind this is a silent network exposure (#992). Compose files
// generated after the #992 fix always carry a prefix; this check exists for
// the installs generated before it.
func UnqualifiedPublishes(data []byte) ([]string, error) {
	var doc struct {
		Services map[string]struct {
			Ports []string `yaml:"ports"`
		} `yaml:"services"`
	}
	if err := yaml.Unmarshal(data, &doc); err != nil {
		return nil, fmt.Errorf("parse compose file: %w", err)
	}
	names := make([]string, 0, len(doc.Services))
	for name := range doc.Services {
		names = append(names, name)
	}
	sort.Strings(names)

	var out []string
	for _, name := range names {
		for _, p := range doc.Services[name].Ports {
			if publishIsUnqualified(p) {
				out = append(out, name+" "+p)
			}
		}
	}
	return out, nil
}

// publishIsUnqualified reports whether a short-syntax port mapping lacks a
// host-IP prefix. Short syntax is [HOST_IP:]HOST_PORT:CONTAINER_PORT[/proto]
// (or a bare container port, which publishes an ephemeral host port — also on
// all interfaces). An IPv6 host IP is bracketed ("[::1]:8000:8000"), so a
// leading '[' counts as qualified; otherwise two colons or more means an IP
// prefix is present.
func publishIsUnqualified(mapping string) bool {
	m := strings.TrimSpace(mapping)
	if m == "" {
		return false
	}
	if strings.HasPrefix(m, "[") {
		return false
	}
	if i := strings.LastIndex(m, "/"); i >= 0 {
		m = m[:i]
	}
	return strings.Count(m, ":") < 2
}
