#!/bin/sh
t() { name=$1; shift; out=$("$@" 2>&1); rc=$?; if [ $rc -eq 0 ]; then echo "OK   $name: $(echo "$out" | head -1)"; else echo "FAIL $name: $(echo "$out" | tail -1)"; fi; }
BASE="--die-with-parent --new-session --ro-bind /usr /usr --symlink usr/lib /lib --symlink usr/bin /bin --ro-bind /etc/ssl /etc/ssl --dev /dev --tmpfs /tmp --tmpfs /home/runner --setenv HOME /home/runner --clearenv"
BW1="bwrap --unshare-user --unshare-net --unshare-ipc --unshare-uts --unshare-cgroup $BASE"            # no pid ns, no new /proc
BW2="bwrap --unshare-user --unshare-net --unshare-ipc --unshare-uts --unshare-cgroup --unshare-pid $BASE"  # pid ns without --proc
t "bwrap(no pidns)+gh"              $BW1 /usr/local/bin/gh --version
t "bwrap(no pidns) net (must FAIL)" $BW1 /usr/bin/curl -sS -m 3 https://api.github.com/zen
t "bwrap(no pidns) sees host env?"  $BW1 /bin/sh -c 'test -z "$PATH_SECRET" && echo clean-env'
t "bwrap(pidns, no /proc)+gh"       $BW2 /usr/local/bin/gh --version
t "unshare -Urn net (must FAIL)"    unshare -Urn /usr/bin/curl -sS -m 3 https://api.github.com/zen
t "bwrap only lo iface"             $BW1 /bin/sh -c 'ls /sys/class/net 2>/dev/null || cat /proc/net/dev | tail -n +3 | cut -d: -f1'
# overhead: 20 launches
s=$(date +%s%N); i=0; while [ $i -lt 20 ]; do $BW1 /usr/local/bin/gh --version >/dev/null 2>&1; i=$((i+1)); done; e=$(date +%s%N)
d=$(date +%s%N); i=0; while [ $i -lt 20 ]; do /usr/local/bin/gh --version >/dev/null 2>&1; i=$((i+1)); done; f=$(date +%s%N)
echo "TIME gh --version: sandboxed $(( (e-s)/20000000 )) ms/run vs bare $(( (f-d)/20000000 )) ms/run"
