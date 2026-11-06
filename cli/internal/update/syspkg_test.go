package update

import "testing"

func TestSystemPackageManaged(t *testing.T) {
	tests := []struct {
		name   string
		target string
		want   bool
	}{
		{"deb/rpm/apk install", "/usr/bin/jenticctl", true},
		{"unclean path into /usr/bin", "/usr/lib/../bin/jenticctl", true},
		{"install.sh default dir", "/home/u/.jentic/bin/jenticctl", false},
		{"manual install into /usr/local/bin", "/usr/local/bin/jenticctl", false},
		{"Linuxbrew cask", "/home/linuxbrew/.linuxbrew/Caskroom/jentic/0.44.0/jenticctl", false},
		{"nested under /usr/bin", "/usr/bin/jentic/jenticctl", false},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := SystemPackageManaged(tt.target); got != tt.want {
				t.Errorf("SystemPackageManaged(%q) = %v, want %v", tt.target, got, tt.want)
			}
		})
	}
}
