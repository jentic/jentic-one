package container

import (
	"archive/tar"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"os/exec"
	"slices"
	"strings"
	"testing"
)

// fakeDocker records every docker invocation and answers from a script keyed
// by the joined argv prefix.
type fakeDocker struct {
	calls   [][]string
	inputs  map[string][]byte
	answers map[string]string
	fail    map[string]error
}

func newFake() *fakeDocker {
	return &fakeDocker{inputs: map[string][]byte{}, answers: map[string]string{}, fail: map[string]error{}}
}

func (f *fakeDocker) match(args []string) (string, error) {
	joined := strings.Join(args, " ")
	for prefix, err := range f.fail {
		if strings.HasPrefix(joined, prefix) {
			return "", err
		}
	}
	for prefix, out := range f.answers {
		if strings.HasPrefix(joined, prefix) {
			return out, nil
		}
	}
	return "", nil
}

func (f *fakeDocker) Output(_ context.Context, args ...string) (string, error) {
	f.calls = append(f.calls, args)
	return f.match(args)
}

func (f *fakeDocker) Input(_ context.Context, stdin io.Reader, args ...string) error {
	f.calls = append(f.calls, args)
	data, _ := io.ReadAll(stdin)
	f.inputs[strings.Join(args, " ")] = data
	_, err := f.match(args)
	return err
}

func (f *fakeDocker) Command(ctx context.Context, args ...string) *exec.Cmd {
	f.calls = append(f.calls, args)
	return exec.CommandContext(ctx, "true")
}

func (f *fakeDocker) called(prefix string) bool {
	for _, c := range f.calls {
		if strings.HasPrefix(strings.Join(c, " "), prefix) {
			return true
		}
	}
	return false
}

const testSession = "0123456789ab"

func testOptions() Options {
	return Options{
		SessionID:   testSession,
		Owner:       OwnerLabel(501, "mac"),
		AgentImage:  DefaultAgentImage,
		BridgeImage: DefaultBridgeImage,
		WorkDir:     "/Users/alice/src/repo",
		JenticPorts: []int{8000, 8100},
		Route:       RouteGateway,
		AgentUser:   "1000:1000",
		TTY:         true,
		Env:         []string{"TERM=xterm-256color"},
		Command:     []string{"claude", "--model", "opus"},
	}
}

func mustPlan(t *testing.T, o Options) *Plan {
	t.Helper()
	p, err := NewPlan(o)
	if err != nil {
		t.Fatalf("NewPlan: %v", err)
	}
	return p
}

func argValues(args []string, flag string) []string {
	var vals []string
	for i := range len(args) - 1 {
		if args[i] == flag {
			vals = append(vals, args[i+1])
		}
	}
	return vals
}

func TestAgentContainerIsHardened(t *testing.T) {
	p := mustPlan(t, testOptions())
	args, err := p.Agent.CreateArgs()
	if err != nil {
		t.Fatal(err)
	}
	if args[0] != "create" || !slices.Contains(args, "--rm") || !slices.Contains(args, "-i") || !slices.Contains(args, "-t") {
		t.Errorf("agent must be created --rm with stdin and a TTY: %v", args)
	}
	for flag, want := range map[string]string{
		"--network":      "container:jentic-run-" + testSession + "-loopback",
		"--user":         "1000:1000",
		"--cap-drop":     "ALL",
		"--security-opt": "no-new-privileges",
		"--workdir":      "/Users/alice/src/repo",
	} {
		if got := argValues(args, flag); !slices.Contains(got, want) {
			t.Errorf("%s = %v, want %q", flag, got, want)
		}
	}
	if !slices.Contains(args, "--read-only") || !slices.Contains(args, "--init") {
		t.Errorf("agent root filesystem must be read-only, under an init: %v", args)
	}
	mounts := argValues(args, "--mount")
	wantMounts := []string{
		"type=volume,src=jentic-run-" + testSession + "-home,dst=/home",
		"type=bind,src=/Users/alice/src/repo,dst=/Users/alice/src/repo",
	}
	if !slices.Equal(mounts, wantMounts) {
		t.Errorf("mounts = %v, want %v", mounts, wantMounts)
	}
	joined := strings.Join(args, " ")
	if strings.Contains(joined, "docker.sock") || strings.Contains(joined, "--privileged") ||
		strings.Contains(joined, "--cap-add") || strings.Contains(joined, "-sockets") {
		t.Errorf("agent must not get the Docker socket, privileges or the relay sockets: %v", args)
	}
	env := argValues(args, "--env")
	if !slices.Contains(env, "HOME=/home/agent") || !slices.Contains(env, "TERM=xterm-256color") {
		t.Errorf("env = %v", env)
	}
	if got := args[len(args)-3:]; !slices.Equal(got, []string{"claude", "--model", "opus"}) {
		t.Errorf("command = %v", got)
	}
	labels := argValues(args, "--label")
	for _, want := range []string{
		LabelSession + "=" + testSession, LabelOwner + "=501@mac", LabelRole + "=" + RoleAgent,
	} {
		if !slices.Contains(labels, want) {
			t.Errorf("labels %v lack %q", labels, want)
		}
	}
}

func TestBridgeContainers(t *testing.T) {
	p := mustPlan(t, testOptions())
	relay, err := p.Relay.RunArgs()
	if err != nil {
		t.Fatal(err)
	}
	loop, err := p.Loopback.RunArgs()
	if err != nil {
		t.Fatal(err)
	}
	if got := argValues(relay, "--network"); !slices.Equal(got, []string{"bridge"}) {
		t.Errorf("gateway relay network = %v", got)
	}
	if got := argValues(relay, "--add-host"); !slices.Equal(got, []string{"host.docker.internal:host-gateway"}) {
		t.Errorf("relay add-host = %v", got)
	}
	if got := relay[len(relay)-4:]; !slices.Equal(got, []string{"relay", "host.docker.internal", "8000", "8100"}) {
		t.Errorf("relay command = %v", got)
	}
	if got := argValues(loop, "--network"); !slices.Equal(got, []string{"jentic-run-" + testSession}) {
		t.Errorf("loopback network = %v", got)
	}
	if got := loop[len(loop)-3:]; !slices.Equal(got, []string{"loopback", "8000", "8100"}) {
		t.Errorf("loopback command = %v", got)
	}
	for _, args := range [][]string{relay, loop} {
		if !slices.Contains(args, "-d") || !slices.Contains(args, "--read-only") ||
			!slices.Contains(argValues(args, "--user"), bridgeUser) {
			t.Errorf("bridge must run detached, read-only and unprivileged: %v", args)
		}
		mounts := argValues(args, "--mount")
		if !slices.Equal(mounts, []string{"type=volume,src=jentic-run-" + testSession + "-sockets,dst=/run/jentic-relay"}) {
			t.Errorf("bridge mounts = %v", mounts)
		}
	}

	o := testOptions()
	o.Route = RouteHostNetwork
	host := mustPlan(t, o)
	relay, _ = host.Relay.RunArgs()
	if got := argValues(relay, "--network"); !slices.Equal(got, []string{"host"}) {
		t.Errorf("host-network relay network = %v", got)
	}
	if len(argValues(relay, "--add-host")) != 0 || !slices.Contains(relay, "127.0.0.1") {
		t.Errorf("host-network relay dials 127.0.0.1 without add-host: %v", relay)
	}
	agent, _ := host.Agent.CreateArgs()
	if got := argValues(agent, "--network"); got[0] == "host" {
		t.Errorf("the agent never shares the host network: %v", got)
	}
}

func TestRouteFor(t *testing.T) {
	for _, tc := range []struct {
		os, goos string
		want     HostRoute
	}{
		{"Docker Desktop", "darwin", RouteGateway},
		{"Docker Desktop", "linux", RouteGateway},
		{"OrbStack", "darwin", RouteGateway},
		{"Ubuntu 24.04.1 LTS", "linux", RouteHostNetwork},
		{"Ubuntu 24.04.1 LTS", "darwin", RouteGateway},
	} {
		if got := RouteFor(Engine{OperatingSystem: tc.os}, tc.goos); got != tc.want {
			t.Errorf("RouteFor(%q, %s) = %s, want %s", tc.os, tc.goos, got, tc.want)
		}
	}
}

func TestSpecRefusesUnsafeContainers(t *testing.T) {
	base := Spec{Name: "x", Image: "img", User: "1000:1000"}
	for name, mutate := range map[string]func(*Spec){
		"no user":   func(s *Spec) { s.User = "" },
		"root":      func(s *Spec) { s.User = "0:0" },
		"root name": func(s *Spec) { s.User = "root" },
		"socket": func(s *Spec) {
			s.Mounts = []Mount{{Type: "bind", Source: "/var/run/docker.sock", Target: "/var/run/docker.sock"}}
		},
		"socket alias": func(s *Spec) {
			s.Mounts = []Mount{{Type: "bind", Source: "/Users/a/.docker/run/docker.sock", Target: "/x"}}
		},
		"socket dir":    func(s *Spec) { s.Mounts = []Mount{{Type: "bind", Source: "/var/run", Target: "/var/run/docker"}} },
		"mount inject":  func(s *Spec) { s.Mounts = []Mount{{Type: "bind", Source: "/a,dst=/etc", Target: "/b"}} },
		"relative dst":  func(s *Spec) { s.Mounts = []Mount{{Type: "bind", Source: "/a", Target: "b"}} },
		"unclean dst":   func(s *Spec) { s.Mounts = []Mount{{Type: "bind", Source: "/a", Target: "/b/../etc"}} },
		"tmpfs mount":   func(s *Spec) { s.Mounts = []Mount{{Type: "tmpfs", Source: "x", Target: "/b"}} },
		"missing image": func(s *Spec) { s.Image = "" },
	} {
		s := base
		mutate(&s)
		if _, err := s.RunArgs(); err == nil {
			t.Errorf("%s: spec accepted: %+v", name, s)
		}
	}
	if _, err := base.RunArgs(); err != nil {
		t.Errorf("baseline refused: %v", err)
	}
}

func TestNewPlanValidation(t *testing.T) {
	for name, mutate := range map[string]func(*Options){
		"bad session":   func(o *Options) { o.SessionID = "../x" },
		"relative dir":  func(o *Options) { o.WorkDir = "repo" },
		"root dir":      func(o *Options) { o.WorkDir = "/" },
		"unclean dir":   func(o *Options) { o.WorkDir = "/a/../b" },
		"agent home":    func(o *Options) { o.WorkDir = "/home/agent/x" },
		"no ports":      func(o *Options) { o.JenticPorts = nil },
		"bad port":      func(o *Options) { o.JenticPorts = []int{70000} },
		"no command":    func(o *Options) { o.Command = nil },
		"bad route":     func(o *Options) { o.Route = "nat" },
		"root agent":    func(o *Options) { o.AgentUser = "0:0" },
		"rw egress dir": func(o *Options) { o.Egress = rwMountEgress{} },
	} {
		o := testOptions()
		mutate(&o)
		if _, err := NewPlan(o); err == nil {
			t.Errorf("%s: plan accepted", name)
		}
	}
	o := testOptions()
	o.WorkDir = "/home/alice/repo"
	if _, err := NewPlan(o); err != nil {
		t.Errorf("a work folder under the host's /home must be accepted: %v", err)
	}
}

type rwMountEgress struct{ NoEgress }

func (rwMountEgress) AgentMounts(*Plan) []Mount {
	return []Mount{{Type: "volume", Source: "ca", Target: "/run/ca"}}
}

// recordingEgress shows the seam: sidecars join the session network, the
// agent gets the egress's env and read-only mounts, and Ready gates the agent.
type recordingEgress struct {
	ready error
}

func (recordingEgress) Name() string            { return "test" }
func (recordingEgress) Networks(*Plan) []string { return nil }
func (recordingEgress) Volumes(*Plan) []string  { return nil }
func (recordingEgress) Sidecars(*Plan) []Spec {
	return []Spec{{Name: "jentic-run-" + testSession + "-egress", Image: "egress:test", User: "65534:65534", Detach: true}}
}
func (recordingEgress) AgentEnv(*Plan) []string { return []string{"HTTPS_PROXY=http://egress:3128"} }
func (recordingEgress) AgentMounts(*Plan) []Mount {
	return []Mount{{Type: "volume", Source: "ca", Target: "/run/ca", ReadOnly: true}}
}
func (e recordingEgress) Ready(context.Context, Docker, *Plan) error { return e.ready }

func TestEgressSeam(t *testing.T) {
	o := testOptions()
	o.Egress = recordingEgress{}
	p := mustPlan(t, o)
	if !slices.Contains(p.Agent.Env, "HTTPS_PROXY=http://egress:3128") {
		t.Errorf("agent env lacks the egress env: %v", p.Agent.Env)
	}
	if !slices.Contains(p.Agent.Mounts, Mount{Type: "volume", Source: "ca", Target: "/run/ca", ReadOnly: true}) {
		t.Errorf("agent mounts lack the egress mount: %v", p.Agent.Mounts)
	}
	d := newFake()
	if err := (&Session{Plan: p, Docker: d}).Up(context.Background()); err != nil {
		t.Fatal(err)
	}
	var sidecar []string
	for _, c := range d.calls {
		if slices.Contains(c, "egress:test") {
			sidecar = c
		}
	}
	if sidecar == nil || !slices.Contains(argValues(sidecar, "--network"), p.Network) ||
		!slices.Contains(argValues(sidecar, "--label"), LabelSession+"="+testSession) {
		t.Errorf("sidecar must join the session network with the session label: %v", sidecar)
	}

	o.Egress = recordingEgress{ready: errors.New("no CA")}
	d = newFake()
	err := (&Session{Plan: mustPlan(t, o), Docker: d}).Up(context.Background())
	if err == nil || !strings.Contains(err.Error(), "no CA") {
		t.Errorf("an egress that is not ready must fail the session: %v", err)
	}
	if d.called("create") {
		t.Errorf("the agent must not be created when the egress is not ready")
	}
	if mode, err := ParseEgressMode("socks"); err == nil || mode != "" {
		t.Errorf("unknown egress modes are refused")
	}
	for in, want := range map[string]string{"": EgressProxy, "proxy": EgressProxy, "none": EgressNone} {
		if got, err := ParseEgressMode(in); err != nil || got != want {
			t.Errorf("ParseEgressMode(%q) = %q, %v; want %q", in, got, err, want)
		}
	}
}

func TestSessionUpOrder(t *testing.T) {
	p := mustPlan(t, testOptions())
	d := newFake()
	if err := (&Session{Plan: p, Docker: d}).Up(context.Background()); err != nil {
		t.Fatal(err)
	}
	var order []string
	for _, c := range d.calls {
		switch {
		case c[0] == "network" && c[1] == "create":
			if !slices.Contains(c, "--internal") {
				t.Errorf("session network must be internal: %v", c)
			}
			order = append(order, "network")
		case c[0] == "volume" && c[1] == "create":
			order = append(order, "volume:"+c[len(c)-1])
		case c[0] == "run":
			order = append(order, "run:"+argValues(c, "--name")[0])
		case c[0] == "exec":
			order = append(order, c[3])
		case c[0] == "create":
			order = append(order, "create:"+argValues(c, "--name")[0])
		}
	}
	base := "jentic-run-" + testSession
	want := []string{
		"network", "volume:" + base + "-home", "volume:" + base + "-sockets",
		"run:" + base + "-relay", "ready-relay", "run:" + base + "-loopback", "ready-loopback",
		"create:" + base + "-agent",
	}
	if !slices.Equal(order, want) {
		t.Errorf("Up order = %v, want %v", order, want)
	}
}

func TestSessionUpFailsWhenTheLoopbackIsNotReady(t *testing.T) {
	p := mustPlan(t, testOptions())
	d := newFake()
	d.fail["exec jentic-run-"+testSession+"-loopback"] = errors.New("not listening")
	s := &Session{Plan: p, Docker: d, ReadyTimeout: 1}
	if err := s.Up(context.Background()); err == nil {
		t.Fatal("Up must fail while the loopback does not listen")
	}
	if d.called("create") {
		t.Error("the agent must not be created before the loopback owns its ports")
	}
}

func TestSeedAndAttach(t *testing.T) {
	p := mustPlan(t, testOptions())
	d := newFake()
	s := &Session{Plan: p, Docker: d}
	if err := s.Seed(context.Background(), bytes.NewReader([]byte("tar"))); err != nil {
		t.Fatal(err)
	}
	if got := d.inputs["cp - jentic-run-"+testSession+"-agent:/home"]; string(got) != "tar" {
		t.Errorf("seed must stream the archive into the agent's /home: %v", d.inputs)
	}
	s.AttachCmd(context.Background())
	if last := d.calls[len(d.calls)-1]; !slices.Equal(last, []string{"start", "--attach", "--interactive", "jentic-run-" + testSession + "-agent"}) {
		t.Errorf("attach = %v", last)
	}
}

func TestTeardownRemovesEverythingLabelled(t *testing.T) {
	d := newFake()
	filter := "label=" + LabelSession + "=" + testSession
	d.answers["ps --all --quiet --filter "+filter] = "c1\nc2"
	d.answers["network ls --quiet --filter "+filter] = "n1"
	d.answers["volume ls --quiet --filter "+filter] = "v1 v2"
	if err := Teardown(context.Background(), d, testSession); err != nil {
		t.Fatal(err)
	}
	for _, want := range []string{"rm --force --volumes c1 c2", "network rm n1", "volume rm --force v1 v2"} {
		if !d.called(want) {
			t.Errorf("teardown did not run %q: %v", want, d.calls)
		}
	}
	// Containers go before the network and volumes they use.
	var idx []int
	for i, c := range d.calls {
		if c[0] == "rm" || (len(c) > 1 && c[1] == "rm") {
			idx = append(idx, i)
		}
	}
	if !slices.IsSorted(idx) || d.calls[idx[0]][0] != "rm" {
		t.Errorf("teardown order: %v", d.calls)
	}

	d = newFake()
	d.fail["network rm"] = errors.New("in use")
	d.answers["network ls"] = "n1"
	d.answers["volume ls"] = "v1"
	if err := Teardown(context.Background(), d, testSession); err == nil {
		t.Error("a failed removal is reported")
	}
	if !d.called("volume rm --force v1") {
		t.Error("teardown keeps going after a failed removal")
	}
	if err := Teardown(context.Background(), newFake(), "bad id"); err == nil {
		t.Error("teardown refuses an invalid session id")
	}
}

func TestReapRemovesOnlyDeadSessionsOfTheOwner(t *testing.T) {
	d := newFake()
	owner := OwnerLabel(501, "mac")
	ownerFilter := "--filter label=" + LabelOwner + "=" + owner
	d.answers["ps --all "+ownerFilter] = "aaaaaaaaaaaa\nbbbbbbbbbbbb"
	d.answers["network ls "+ownerFilter] = "bbbbbbbbbbbb\nnot-a-session"
	d.answers["volume ls "+ownerFilter] = "cccccccccccc"
	alive := func(id string) bool { return id == "bbbbbbbbbbbb" }
	reaped, err := Reap(context.Background(), d, owner, alive)
	if err != nil {
		t.Fatal(err)
	}
	if !slices.Equal(reaped, []string{"aaaaaaaaaaaa", "cccccccccccc"}) {
		t.Errorf("reaped = %v", reaped)
	}
	for _, c := range d.calls {
		joined := strings.Join(c, " ")
		if strings.Contains(joined, "bbbbbbbbbbbb") || strings.Contains(joined, "not-a-session") {
			t.Errorf("a live or invalid session was touched: %v", c)
		}
	}
}

func TestPreflight(t *testing.T) {
	d := newFake()
	d.answers["info"] = "Docker Desktop"
	e, err := Preflight(context.Background(), d, "a:1", "b:1")
	if err != nil || !e.Desktop() {
		t.Fatalf("Preflight = %+v, %v", e, err)
	}
	if d.called("pull") {
		t.Error("present images are not pulled")
	}

	d = newFake()
	d.fail["info"] = errors.New("cannot connect")
	if _, err := Preflight(context.Background(), d); !IsPreflight(err) {
		t.Errorf("unreachable engine: %v", err)
	}

	d = newFake()
	d.fail["image inspect --format {{.Id}} b:1"] = errors.New("no such image")
	d.fail["pull --quiet b:1"] = errors.New("denied")
	_, err = Preflight(context.Background(), d, "a:1", "b:1")
	if !IsPreflight(err) || !strings.Contains(err.Error(), "docker build") {
		t.Errorf("missing image must fail closed with the build command: %v", err)
	}
	if !d.called("pull --quiet b:1") {
		t.Error("a missing image is pulled once")
	}
}

func readArchive(t *testing.T, data []byte) map[string]*tar.Header {
	t.Helper()
	headers := map[string]*tar.Header{}
	tr := tar.NewReader(bytes.NewReader(data))
	for {
		h, err := tr.Next()
		if errors.Is(err, io.EOF) {
			return headers
		}
		if err != nil {
			t.Fatal(err)
		}
		headers[h.Name] = h
	}
}

func TestHomeArchive(t *testing.T) {
	data, err := HomeArchive(1000, 1000, []HomeFile{
		{Rel: ".config/jentic/keys/a.key", Data: []byte("k")},
		{Rel: ".claude.json", Data: []byte("{}"), Mode: 0o644},
	})
	if err != nil {
		t.Fatal(err)
	}
	h := readArchive(t, data)
	for name, mode := range map[string]int64{
		"agent/": 0o700, "agent/.config/": 0o700, "agent/.config/jentic/": 0o700,
		"agent/.config/jentic/keys/": 0o700, "agent/.config/jentic/keys/a.key": 0o600,
		"agent/.claude.json": 0o644,
	} {
		got, ok := h[name]
		if !ok {
			t.Errorf("archive lacks %s: %v", name, h)
			continue
		}
		if got.Mode != mode || got.Uid != 1000 || got.Gid != 1000 {
			t.Errorf("%s: mode %o uid %d gid %d", name, got.Mode, got.Uid, got.Gid)
		}
	}
	for _, bad := range []string{"/etc/passwd", "../x", "a/../../x", "", ".", "a//b"} {
		if _, err := HomeArchive(1000, 1000, []HomeFile{{Rel: bad}}); err == nil {
			t.Errorf("path %q accepted", bad)
		}
	}
	if _, err := HomeArchive(1000, 1000, []HomeFile{{Rel: "a"}, {Rel: "a/b"}}); err == nil {
		t.Error("a path used as both file and directory is refused")
	}
}

func TestClaudeHomeFilesSeedsOnlyAllowlistedState(t *testing.T) {
	seed := ClaudeSeed{
		State: []byte(`{"theme":"dark","primaryApiKey":"sk-ant-secret","oauthAccount":{"email":"a@b"},` +
			`"customApiKeyResponses":{"approved":["x"]},"projects":{"/Users/alice/other":{"allowedTools":["Bash"]}}}`),
		Settings: []byte(`{"model":"opus","hooks":{"PreToolUse":[{"hooks":[{"command":"/opt/homebrew/bin/x"}]}]},` +
			`"statusLine":{"command":"~/.claude/statusline.sh"},"apiKeyHelper":"/usr/local/bin/key",` +
			`"env":{"ANTHROPIC_API_KEY":"sk-ant-env","CLAUDE_CODE_USE_BEDROCK":"1"},"awsAuthRefresh":"aws sso login"}`),
		Memory: []byte("# me"),
	}
	files, err := ClaudeHomeFiles("/Users/alice/src/repo", seed)
	if err != nil {
		t.Fatal(err)
	}
	byRel := map[string][]byte{}
	for _, f := range files {
		byRel[f.Rel] = f.Data
	}
	var state map[string]any
	if err := json.Unmarshal(byRel[".claude.json"], &state); err != nil {
		t.Fatal(err)
	}
	if state["hasCompletedOnboarding"] != true || state["theme"] != "dark" {
		t.Errorf("state = %v", state)
	}
	projects, _ := state["projects"].(map[string]any)
	trusted, _ := projects["/Users/alice/src/repo"].(map[string]any)
	if len(projects) != 1 || trusted["hasTrustDialogAccepted"] != true {
		t.Errorf("only the mounted work folder is trusted: %v", projects)
	}
	all := string(byRel[".claude.json"]) + string(byRel[".claude/settings.json"])
	leaks := []string{
		"sk-ant", "oauthAccount", "customApiKeyResponses", "hooks", "statusLine",
		"apiKeyHelper", "homebrew", "BEDROCK", "awsAuthRefresh", "/Users/alice/other",
	}
	for _, leaked := range leaks {
		if strings.Contains(all, leaked) {
			t.Errorf("seeded config carries %q:\n%s", leaked, all)
		}
	}
	if !strings.Contains(string(byRel[".claude/settings.json"]), `"opus"`) || string(byRel[".claude/CLAUDE.md"]) != "# me" {
		t.Errorf("allowlisted settings and memory are kept: %v", byRel)
	}
	for rel := range byRel {
		if !slices.Contains([]string{".claude.json", ".claude/settings.json", ".claude/CLAUDE.md"}, rel) {
			t.Errorf("unexpected seeded file %s", rel)
		}
	}

	bare, err := ClaudeHomeFiles("/w", ClaudeSeed{})
	if err != nil || len(bare) != 1 || bare[0].Rel != ".claude.json" {
		t.Errorf("without a seed only ~/.claude.json is written: %v %v", bare, err)
	}
	if _, err := ClaudeHomeFiles("/w", ClaudeSeed{Settings: []byte("not json")}); err == nil {
		t.Error("unparsable settings are an error, not silently dropped")
	}
}

func TestSessionIDs(t *testing.T) {
	id, err := NewSessionID()
	if err != nil || !ValidSessionID(id) {
		t.Fatalf("NewSessionID = %q, %v", id, err)
	}
	for _, bad := range []string{"", "ABCDEF012345", "0123456789a", "0123456789abc", "0123456789a/"} {
		if ValidSessionID(bad) {
			t.Errorf("%q accepted", bad)
		}
	}
}
