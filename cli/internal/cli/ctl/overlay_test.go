package ctl

import (
	"strings"
	"testing"

	"gopkg.in/yaml.v3"
)

func TestOverlaySettings_PreservesUnknownKeysAndComments(t *testing.T) {
	original := []byte(`# top comment
server:
  port: 8000  # keep me
embeddings:            # enterprise overlay section the OSS schema never heard of
  provider: acme
`)
	out, err := OverlaySettings(original, Settings{
		"server": Settings{"port": 9000},
	})
	if err != nil {
		t.Fatalf("OverlaySettings: %v", err)
	}
	s := string(out)

	// The unknown enterprise section and its value must survive verbatim.
	if !strings.Contains(s, "embeddings:") || !strings.Contains(s, "provider: acme") {
		t.Errorf("overlay dropped the unknown enterprise section:\n%s", s)
	}
	// The comment on the preserved key must survive.
	if !strings.Contains(s, "keep me") {
		t.Errorf("overlay dropped an inline comment:\n%s", s)
	}
	// The overlaid value must be updated.
	var decoded struct {
		Server struct {
			Port int `yaml:"port"`
		} `yaml:"server"`
	}
	if err := yaml.Unmarshal(out, &decoded); err != nil {
		t.Fatalf("re-decode: %v", err)
	}
	if decoded.Server.Port != 9000 {
		t.Errorf("server.port = %d, want 9000", decoded.Server.Port)
	}
}

func TestOverlaySettings_CreatesMissingNestedSections(t *testing.T) {
	out, err := OverlaySettings([]byte("server:\n  port: 8000\n"), Settings{
		"databases": Settings{"registry": Settings{"host": "db.internal"}},
	})
	if err != nil {
		t.Fatalf("OverlaySettings: %v", err)
	}
	var decoded struct {
		Databases struct {
			Registry struct {
				Host string `yaml:"host"`
			} `yaml:"registry"`
		} `yaml:"databases"`
	}
	if err := yaml.Unmarshal(out, &decoded); err != nil {
		t.Fatalf("re-decode: %v", err)
	}
	if decoded.Databases.Registry.Host != "db.internal" {
		t.Errorf("nested section not created: %q", decoded.Databases.Registry.Host)
	}
}

func TestOverlaySettings_EmptyOriginalStartsFreshDoc(t *testing.T) {
	out, err := OverlaySettings(nil, Settings{"server": Settings{"port": 1234}})
	if err != nil {
		t.Fatalf("OverlaySettings(nil): %v", err)
	}
	var decoded map[string]map[string]int
	if err := yaml.Unmarshal(out, &decoded); err != nil {
		t.Fatalf("re-decode: %v", err)
	}
	if decoded["server"]["port"] != 1234 {
		t.Errorf("fresh-doc overlay wrong: %v", decoded)
	}
}
