#!/bin/sh
# Which bwrap option makes `aws --version` slower? mean ms over n runs per variant.
n=8
BASE="--die-with-parent --ro-bind /usr /usr --symlink usr/lib /lib --symlink usr/bin /bin --ro-bind /etc/ssl /etc/ssl --dev /dev --tmpfs /tmp --tmpfs /home/sbx --setenv HOME /home/sbx --setenv PATH /usr/local/bin:/usr/bin:/bin"
t() { s=$(date +%s%N); i=0; while [ $i -lt $n ]; do "$@" >/dev/null 2>&1; i=$((i+1)); done; e=$(date +%s%N); echo $(( (e-s)/(n*1000000) )); }
echo "bare                         $(t aws --version) ms"
echo "full (user+net+pid+...+new-session) $(t bwrap --unshare-user --unshare-net --unshare-ipc --unshare-uts --unshare-cgroup --unshare-pid --new-session $BASE aws --version) ms"
echo "user only                    $(t bwrap --unshare-user $BASE aws --version) ms"
echo "user+net                     $(t bwrap --unshare-user --unshare-net $BASE aws --version) ms"
echo "user+pid                     $(t bwrap --unshare-user --unshare-pid $BASE aws --version) ms"
echo "user+net, ro-bind /proc      $(t bwrap --unshare-user --unshare-net --ro-bind /proc /proc $BASE aws --version) ms"
echo "user+net, HOME on real /tmp  $(t bwrap --unshare-user --unshare-net $BASE --bind /tmp /tmp aws --version) ms"
echo "bare, cpu count seen:        $(python3 -c 'import os;print(os.cpu_count())')"
echo "sandbox (no /proc) cpu count: $(bwrap --unshare-user --unshare-net $BASE python3 -c 'import os;print(os.cpu_count(), len(os.sched_getaffinity(0)))' 2>&1)"
