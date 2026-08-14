package install

import (
	"strings"
	"testing"
)

func stepTitles(steps []Step) string {
	var b strings.Builder
	for _, s := range steps {
		b.WriteString(s.Title)
		b.WriteString("\n")
		for _, c := range s.Commands {
			b.WriteString(c)
			b.WriteString("\n")
		}
	}
	return b.String()
}

func TestNextStepsSourceUnbuilt(t *testing.T) {
	d := NewDraft() // sqlite, nothing built yet
	d.RuntimePath = RuntimeSource
	out := stepTitles(d.NextSteps("/cfg.yaml", SetupRequired))
	if !strings.Contains(out, "make install") {
		t.Errorf("unbuilt source should suggest make install:\n%s", out)
	}
	if !strings.Contains(out, "migrations.run") {
		t.Errorf("should include migrate step:\n%s", out)
	}
	if !strings.Contains(out, "-m jentic_one") {
		t.Errorf("should include start step:\n%s", out)
	}
	// The broker is a separate service and must get its own start step.
	if !strings.Contains(out, "JENTIC__APPS=broker") {
		t.Errorf("source steps should include a broker start step:\n%s", out)
	}
	// No-credential first-run: must always prompt to create the first admin.
	if !strings.Contains(out, "jenticctl setup") {
		t.Errorf("source steps should include the create-first-admin step:\n%s", out)
	}
}

func TestNextStepsSkipsBrokerWhenStarted(t *testing.T) {
	d := NewDraft()
	d.RuntimePath = RuntimeSource
	d.VenvPython = "/venv/bin/python"
	d.MigrationsDone = true
	d.AppStarted = true
	d.BrokerStarted = true
	out := stepTitles(d.NextSteps("/cfg.yaml", SetupRequired))
	if strings.Contains(out, "JENTIC__APPS=broker") {
		t.Errorf("should not tell user to start an already-running broker:\n%s", out)
	}
}

func TestNextStepsSkipsStartWhenAppStarted(t *testing.T) {
	d := NewDraft()
	d.RuntimePath = RuntimeSource
	d.VenvPython = "/venv/bin/python"
	d.MigrationsDone = true
	d.AppStarted = true
	steps := d.NextSteps("/cfg.yaml", SetupRequired)
	out := stepTitles(steps)
	if strings.Contains(out, "Start the app") {
		t.Errorf("should not tell user to start an already-running app:\n%s", out)
	}
	if strings.Contains(out, "make install") {
		t.Errorf("built venv should not suggest make install:\n%s", out)
	}
}

func TestNextStepsDockerNotStarted(t *testing.T) {
	d := NewDraft()
	d.RuntimePath = RuntimeDocker
	out := stepTitles(d.NextSteps("/cfg.yaml", SetupRequired))
	if !strings.Contains(out, "jenticctl start") {
		t.Errorf("docker path should suggest starting the stack:\n%s", out)
	}
	if strings.Contains(out, "tools.deploy") || strings.Contains(out, "make build-all") {
		t.Errorf("docker path should no longer reference the kind/Helm flow:\n%s", out)
	}
}

func TestNextStepsDockerStartedHasFirstAdminAndConnect(t *testing.T) {
	d := NewDraft()
	d.RuntimePath = RuntimeDocker
	d.AppStarted = true
	steps := d.NextSteps("/cfg.yaml", SetupRequired)
	// A started stack has no start step, but the no-credential model always
	// leaves the create-first-admin step, followed by the connect-an-agent step.
	if len(steps) != 2 {
		t.Fatalf("a started docker stack should have the first-admin + connect-agent steps, got %+v", steps)
	}
	out := stepTitles(steps)
	if !strings.Contains(out, "jenticctl setup") {
		t.Errorf("started docker stack should still prompt to create the first admin:\n%s", out)
	}
	// The connect step must steer to 127.0.0.1 (not localhost) — the audience
	// match is exact against the local canonical_base_url.
	if !strings.Contains(out, "jentic register --url http://127.0.0.1:8000") {
		t.Errorf("started docker stack should tell the user how to connect an agent:\n%s", out)
	}
	if strings.Contains(out, "http://localhost") {
		t.Errorf("connect step must use a 127.0.0.1 URL, not localhost:\n%s", out)
	}
}

// When a live probe reports an admin already exists (re-install over a database
// uninstall left behind), the summary must steer the operator to sign in
// instead of contradicting itself with "create the first admin".
func TestNextStepsSetupCompleteShowsSignIn(t *testing.T) {
	d := NewDraft()
	d.RuntimePath = RuntimeDocker
	d.AppStarted = true
	out := stepTitles(d.NextSteps("/cfg.yaml", SetupComplete))
	if strings.Contains(out, "jenticctl setup") {
		t.Errorf("setup-complete should not prompt to create the first admin:\n%s", out)
	}
	if !strings.Contains(out, "already exists") {
		t.Errorf("setup-complete should explain an admin already exists:\n%s", out)
	}
}

func TestRenderSummaryDockerStack(t *testing.T) {
	d := NewDraft()
	d.RuntimePath = RuntimeDocker
	d.AppStarted = true
	d.ComposePath = "/home/u/.jentic/docker-compose.yaml"
	summary := RenderSummary(d, "/cfg.yaml", "", "/logs", SetupRequired)
	if !strings.Contains(summary, "Stack (running in Docker)") {
		t.Errorf("docker summary should show the Docker stack block:\n%s", summary)
	}
	if !strings.Contains(summary, d.ComposePath) {
		t.Errorf("docker summary should reference the compose file:\n%s", summary)
	}
	if !strings.Contains(summary, d.BrokerURL()) {
		t.Errorf("docker summary should reference the broker URL:\n%s", summary)
	}
	if strings.Contains(summary, "pid:") {
		t.Errorf("docker summary should not show a PID:\n%s", summary)
	}
}

func TestRenderSummaryShowsAppWhenStarted(t *testing.T) {
	d := NewDraft()
	d.RuntimePath = RuntimeSource
	d.AppStarted = true
	d.AppPID = 4242
	d.BrokerStarted = true
	d.BrokerPID = 4343
	summary := RenderSummary(d, "/cfg.yaml", "/data", "/logs", SetupRequired)
	if !strings.Contains(summary, "4242") {
		t.Errorf("summary should show the running PID:\n%s", summary)
	}
	if !strings.Contains(summary, "4343") || !strings.Contains(summary, d.BrokerURL()) {
		t.Errorf("summary should show the running broker PID and URL:\n%s", summary)
	}
	if !strings.Contains(summary, FirstRunNote) {
		t.Errorf("summary should include the first-run note")
	}
}

// With a live probe reporting an existing admin, the summary swaps the
// first-run note for the admin-exists note rather than falsely claiming "no
// users exist yet".
func TestRenderSummarySetupCompleteSwapsNote(t *testing.T) {
	d := NewDraft()
	d.RuntimePath = RuntimeSource
	d.AppStarted = true
	summary := RenderSummary(d, "/cfg.yaml", "/data", "/logs", SetupComplete)
	if strings.Contains(summary, FirstRunNote) {
		t.Errorf("setup-complete summary must not claim no users exist:\n%s", summary)
	}
	if !strings.Contains(summary, AdminExistsNote) {
		t.Errorf("setup-complete summary should include the admin-exists note:\n%s", summary)
	}
}
