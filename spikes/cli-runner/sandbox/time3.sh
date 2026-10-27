#!/bin/sh
# Usage: time3.sh <n> <cmd...> — mean ms per run, sandboxed vs bare.
n=$1; shift
BW="bwrap --unshare-user --unshare-net --unshare-ipc --unshare-uts --unshare-cgroup --unshare-pid --die-with-parent --new-session --ro-bind /usr /usr --symlink usr/lib /lib --symlink usr/bin /bin --ro-bind /etc/ssl /etc/ssl --dev /dev --tmpfs /tmp --tmpfs /home/sbx --setenv HOME /home/sbx --setenv PATH /usr/local/bin:/usr/bin:/bin"
t() { s=$(date +%s%N); i=0; while [ $i -lt $n ]; do "$@" >/dev/null 2>&1; i=$((i+1)); done; e=$(date +%s%N); echo $(( (e-s)/(n*1000000) )); }
echo "$*: sandboxed $(t $BW "$@") ms vs bare $(t "$@") ms (n=$n)"
