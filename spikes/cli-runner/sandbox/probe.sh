#!/bin/sh
# Prints one line per sandbox technique: OK / FAIL <reason>.
t() { name=$1; shift; out=$("$@" 2>&1); rc=$?; if [ $rc -eq 0 ]; then echo "OK   $name: $(echo "$out" | head -1)"; else echo "FAIL $name: $(echo "$out" | tail -1)"; fi; }
BW="bwrap --unshare-all --die-with-parent --new-session --ro-bind /usr /usr --symlink usr/lib /lib --symlink usr/bin /bin --ro-bind /etc/ssl /etc/ssl --proc /proc --dev /dev --tmpfs /tmp --tmpfs /home/runner --setenv HOME /home/runner --clearenv"
t "bwrap+gh --version"            $BW /usr/local/bin/gh --version
t "bwrap net isolated (must FAIL)" $BW /usr/bin/curl -sS -m 3 https://api.github.com/zen
t "unshare -Urn"                  unshare -Urn /usr/local/bin/gh --version
t "direct net (no sandbox)"       curl -sS -m 3 -o /dev/null -w '%{http_code}' https://api.github.com/zen
