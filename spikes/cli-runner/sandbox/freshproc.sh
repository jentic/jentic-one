#!/bin/sh
n=8
BASE="--die-with-parent --ro-bind /usr /usr --symlink usr/lib /lib --symlink usr/bin /bin --ro-bind /etc/ssl /etc/ssl --dev /dev --tmpfs /tmp --tmpfs /home/sbx --setenv HOME /home/sbx --setenv PATH /usr/local/bin:/usr/bin:/bin"
FULL="bwrap --unshare-user --unshare-net --unshare-ipc --unshare-uts --unshare-cgroup --unshare-pid --new-session --proc /proc $BASE"
t() { s=$(date +%s%N); i=0; while [ $i -lt $n ]; do "$@" >/dev/null 2>&1; i=$((i+1)); done; e=$(date +%s%N); echo $(( (e-s)/(n*1000000) )); }
out=$($FULL sh -c 'ls /proc | grep -E "^[0-9]+$" | tr "\n" " "' 2>&1); echo "fresh /proc mount: $out"
echo "aws --version bare $(t aws --version) ms, sandbox+fresh /proc $(t $FULL aws --version) ms"
