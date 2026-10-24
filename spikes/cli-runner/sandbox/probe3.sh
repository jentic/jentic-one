#!/bin/sh
# Usage: probe3.sh <label> <cmd...>  — run cmd in the spike bwrap sandbox (no fresh /proc) and report.
label=$1; shift
BW="bwrap --unshare-user --unshare-net --unshare-ipc --unshare-uts --unshare-cgroup --unshare-pid --die-with-parent --new-session \
 --ro-bind /usr /usr --symlink usr/lib /lib --symlink usr/bin /bin --symlink usr/lib64 /lib64 --ro-bind /etc/ssl /etc/ssl \
 --dev /dev --tmpfs /tmp --tmpfs /home/sbx --setenv HOME /home/sbx --setenv PATH /usr/local/bin:/usr/bin:/bin --chdir /home/sbx"
[ -d /usr/lib64 ] || BW=$(echo "$BW" | sed 's| --symlink usr/lib64 /lib64||')
out=$($BW "$@" 2>&1); rc=$?
echo "$label: exit=$rc  $(echo "$out" | tr '\n' ' ' | cut -c1-220)"
