//go:build !windows

package container

// End to end against a real Docker engine, opt-in: set JENTIC_CONTAINER_IT=1
// and build both images first (BuildHint). A stand-in Jentic listens on the
// host's loopback; the agent container probes it, the internet, the Docker
// socket, its capabilities and the work folder, and the test checks that
// teardown, and the reaper after a SIGKILLed client, leave nothing behind.

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"syscall"
	"testing"
	"time"
)

const (
	itEnv         = "JENTIC_CONTAINER_IT"
	itHelperEnv   = "JENTIC_CONTAINER_IT_HELPER"
	itHelperLock  = "JENTIC_CONTAINER_IT_LOCK"
	itHelperPort  = "JENTIC_CONTAINER_IT_PORT"
	itHelperWork  = "JENTIC_CONTAINER_IT_WORK"
	itHelperOwner = "JENTIC_CONTAINER_IT_OWNER"
	itHelperReady = "session-up"
)

func requireDocker(t *testing.T) (CLI, Engine) {
	t.Helper()
	if os.Getenv(itEnv) != "1" {
		t.Skipf("set %s=1 (and build the images: %s) to run the container integration test", itEnv, BuildHint())
	}
	if _, err := exec.LookPath("docker"); err != nil {
		t.Skip("docker is not installed")
	}
	d := CLI{}
	engine, err := Preflight(context.Background(), d, DefaultAgentImage, DefaultBridgeImage)
	if err != nil {
		t.Skipf("Docker is not usable here: %v", err)
	}
	return d, engine
}

// standIn is a stand-in Jentic on the host's loopback that answers every
// request with the Host header it received.
func standIn(t *testing.T) int {
	t.Helper()
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	srv := &http.Server{
		ReadHeaderTimeout: 5 * time.Second,
		Handler: http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			_ = json.NewEncoder(w).Encode(map[string]string{"standin": "jentic", "host": r.Host, "path": r.URL.Path})
		}),
	}
	go func() { _ = srv.Serve(ln) }()
	t.Cleanup(func() { _ = srv.Close() })
	return ln.Addr().(*net.TCPAddr).Port
}

// itUID is the agent uid the CLI would pick (containerUID in localagentcmd).
func itUID(engine Engine) int {
	if !engine.Desktop() && runtime.GOOS == "linux" && os.Getuid() > 0 {
		return os.Getuid()
	}
	return 1000
}

func itPlan(t *testing.T, engine Engine, owner, workDir string, port int, command []string) *Plan {
	t.Helper()
	id, err := NewSessionID()
	if err != nil {
		t.Fatal(err)
	}
	uid := itUID(engine)
	p, err := NewPlan(Options{
		SessionID:   id,
		Owner:       owner,
		AgentImage:  DefaultAgentImage,
		BridgeImage: DefaultBridgeImage,
		WorkDir:     workDir,
		JenticPorts: []int{port},
		Route:       RouteFor(engine, runtime.GOOS),
		AgentUser:   fmt.Sprintf("%d:%d", uid, uid),
		Command:     command,
	})
	if err != nil {
		t.Fatal(err)
	}
	return p
}

func itWorkDir(t *testing.T) string {
	t.Helper()
	// Canonical, as the CLI mounts it (macOS's temp dir is behind /var → /private/var).
	abs, err := filepath.EvalSymlinks(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	return abs
}

// leftovers lists every Docker object still carrying the session label.
func leftovers(t *testing.T, d CLI, sessionID string) []string {
	t.Helper()
	filter := "label=" + LabelSession + "=" + sessionID
	found := make([]string, 0, 8)
	for _, list := range [][]string{
		{"ps", "--all", "--quiet", "--filter", filter},
		{"network", "ls", "--quiet", "--filter", filter},
		{"volume", "ls", "--quiet", "--filter", filter},
	} {
		out, err := d.Output(context.Background(), list...)
		if err != nil {
			t.Fatal(err)
		}
		found = append(found, strings.Fields(out)...)
	}
	return found
}

const probeScript = `
set -u
echo "jentic=$(curl -sS -m 3 http://127.0.0.1:$PORT/probe)"
if curl -sS -m 3 -o /dev/null https://example.com 2>/dev/null; then echo internet=yes; else echo internet=no; fi
if curl -sS -m 3 -o /dev/null http://1.1.1.1 2>/dev/null; then echo direct_ip=yes; else echo direct_ip=no; fi
if getent hosts example.com >/dev/null 2>&1; then echo dns=yes; else echo dns=no; fi
if [ -e /var/run/docker.sock ] || [ -e /run/docker.sock ]; then echo docker_sock=yes; else echo docker_sock=no; fi
echo "capeff=$(awk '/^CapEff/ {print $2}' /proc/self/status)"
echo "uid=$(id -u)"
echo "home=$(stat -c '%u %a' "$HOME")"
echo written-in-container > "$PWD/from-container.txt" && echo wrote=yes
`

func TestContainerSessionEndToEnd(t *testing.T) {
	d, engine := requireDocker(t)
	port := standIn(t)
	work := itWorkDir(t)
	owner := OwnerLabel(os.Getuid(), "container-it")
	command := []string{"bash", "-c", "PORT=" + strconv.Itoa(port) + "\n" + probeScript}
	p := itPlan(t, engine, owner, work, port, command)
	sess := &Session{Plan: p, Docker: d}
	ctx := context.Background()
	t.Cleanup(func() { _ = Teardown(context.Background(), d, p.SessionID) })

	if err := sess.Up(ctx); err != nil {
		t.Fatalf("Up: %v", err)
	}
	uid := itUID(engine)
	archive, err := HomeArchive(uid, uid, []HomeFile{{Rel: ".config/jentic/config.yaml", Data: []byte("x: 1\n")}})
	if err != nil {
		t.Fatal(err)
	}
	if err := sess.Seed(ctx, bytes.NewReader(archive)); err != nil {
		t.Fatalf("Seed: %v", err)
	}
	out, err := sess.AttachCmd(ctx).CombinedOutput()
	if err != nil {
		t.Fatalf("agent: %v\n%s", err, out)
	}
	t.Logf("agent probe output:\n%s", out)
	got := map[string]string{}
	for _, line := range strings.Split(string(out), "\n") {
		if k, v, ok := strings.Cut(line, "="); ok {
			got[k] = v
		}
	}
	var jentic map[string]string
	if err := json.Unmarshal([]byte(got["jentic"]), &jentic); err != nil {
		t.Fatalf("the agent did not reach the stand-in Jentic: %q\n%s", got["jentic"], out)
	}
	if want := "127.0.0.1:" + strconv.Itoa(port); jentic["host"] != want {
		t.Errorf("Host = %q, want %q (preserved through the bridge)", jentic["host"], want)
	}
	for key, want := range map[string]string{
		"internet": "no", "direct_ip": "no", "dns": "no", "docker_sock": "no",
		"capeff": "0000000000000000", "wrote": "yes",
	} {
		if got[key] != want {
			t.Errorf("%s = %q, want %q\n%s", key, got[key], want, out)
		}
	}
	if got["uid"] == "0" || !strings.HasSuffix(got["home"], " 700") {
		t.Errorf("agent uid %q, home %q: must be unprivileged with a private home", got["uid"], got["home"])
	}
	if data, err := os.ReadFile(filepath.Join(work, "from-container.txt")); err != nil || string(data) != "written-in-container\n" {
		t.Errorf("file written in the container is not on the host: %q %v", data, err)
	}

	if err := Teardown(ctx, d, p.SessionID); err != nil {
		t.Fatalf("Teardown: %v", err)
	}
	if left := leftovers(t, d, p.SessionID); len(left) > 0 {
		t.Errorf("teardown left %v", left)
	}
}

// TestContainerSessionReapedAfterClientSIGKILL runs a client in a child
// process that holds its session lock and sits in a live session; the test
// SIGKILLs it, checks the session is still there, then reaps it the way the
// next run would.
func TestContainerSessionReapedAfterClientSIGKILL(t *testing.T) {
	d, _ := requireDocker(t)
	port := standIn(t)
	work := itWorkDir(t)
	owner := OwnerLabel(os.Getuid(), "container-it-reap")
	lockPath := filepath.Join(t.TempDir(), "session.lock")

	child := exec.Command(os.Args[0], "-test.run=^TestContainerSessionHelperClient$", "-test.v")
	child.Env = append(os.Environ(),
		itHelperEnv+"=1", itHelperLock+"="+lockPath, itHelperPort+"="+strconv.Itoa(port),
		itHelperWork+"="+work, itHelperOwner+"="+owner)
	stdout, err := child.StdoutPipe()
	if err != nil {
		t.Fatal(err)
	}
	child.Stderr = os.Stderr
	if err := child.Start(); err != nil {
		t.Fatal(err)
	}
	sessionID := ""
	scanner := bufio.NewScanner(stdout)
	deadline := time.AfterFunc(2*time.Minute, func() { _ = child.Process.Kill() })
	for scanner.Scan() {
		if id, ok := strings.CutPrefix(scanner.Text(), itHelperReady+" "); ok {
			sessionID = id
			break
		}
	}
	deadline.Stop()
	t.Cleanup(func() {
		if ValidSessionID(sessionID) {
			_ = Teardown(context.Background(), d, sessionID)
		}
	})
	if !ValidSessionID(sessionID) {
		_ = child.Process.Kill()
		_ = child.Wait()
		t.Fatal("the helper client never brought its session up")
	}

	if err := child.Process.Signal(syscall.SIGKILL); err != nil {
		t.Fatal(err)
	}
	_ = child.Wait()
	if left := leftovers(t, d, sessionID); len(left) == 0 {
		t.Fatal("a SIGKILLed client should leave its session behind for the reaper")
	}

	reaped, err := Reap(context.Background(), d, owner, func(id string) bool {
		if id != sessionID {
			return true
		}
		return lockHeld(t, lockPath)
	})
	if err != nil {
		t.Fatalf("Reap: %v", err)
	}
	if len(reaped) != 1 || reaped[0] != sessionID {
		t.Errorf("reaped = %v, want [%s]", reaped, sessionID)
	}
	if left := leftovers(t, d, sessionID); len(left) > 0 {
		t.Errorf("the reaper left %v", left)
	}
}

// lockHeld reports whether another process holds the flock on path.
func lockHeld(t *testing.T, path string) bool {
	t.Helper()
	f, err := os.OpenFile(path, os.O_RDWR|os.O_CREATE, 0o600)
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	if err := syscall.Flock(int(f.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err != nil {
		return true
	}
	_ = syscall.Flock(int(f.Fd()), syscall.LOCK_UN)
	return false
}

// TestContainerSessionHelperClient is the child client of the SIGKILL test,
// not a test of its own: it holds the session lock, brings a session up with a
// long-running agent and waits to be killed.
func TestContainerSessionHelperClient(t *testing.T) {
	if os.Getenv(itHelperEnv) != "1" {
		t.Skip("helper process for TestContainerSessionReapedAfterClientSIGKILL")
	}
	d, engine := requireDocker(t)
	f, err := os.OpenFile(os.Getenv(itHelperLock), os.O_RDWR|os.O_CREATE, 0o600)
	if err != nil {
		t.Fatal(err)
	}
	if err := syscall.Flock(int(f.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err != nil {
		t.Fatal(err)
	}
	port, _ := strconv.Atoi(os.Getenv(itHelperPort))
	p := itPlan(t, engine, os.Getenv(itHelperOwner), os.Getenv(itHelperWork), port, []string{"sleep", "600"})
	sess := &Session{Plan: p, Docker: d}
	ctx := context.Background()
	if err := sess.Up(ctx); err != nil {
		t.Fatal(err)
	}
	attach := sess.AttachCmd(ctx)
	if err := attach.Start(); err != nil {
		t.Fatal(err)
	}
	// Wait for the agent container to run before reporting the session up.
	for range 100 {
		if out, _ := d.Output(ctx, "inspect", "--format", "{{.State.Running}}", p.Agent.Name); out == "true" {
			break
		}
		time.Sleep(100 * time.Millisecond)
	}
	fmt.Println(itHelperReady + " " + p.SessionID)
	_ = attach.Wait()
}
