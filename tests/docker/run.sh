#!/bin/bash
# Linux checks in Docker: builds a scratch bundle and installer, then runs
#   1. plain (Argos) mode in python:3.9-slim with fake credentials and a fake API,
#   2. the tray backend under Xvfb + D-Bus in ubuntu:24.04 and debian:bullseye (Python 3.9),
#   3. install-linux.sh non-interactively (tray, Argos, auto-detect, missing PyGObject).
# Usage: tests/docker/run.sh [WORKDIR]   (never touches plugin/ or dist/)
set -u
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
WORK=${1:-$(mktemp -d)}
mkdir -p "$WORK"
chmod 755 "$WORK"  # the container user may have another uid than the host user (CI runners)
FAILED=0
step() { printf '\n########## %s\n' "$*"; }
run() { "$@" || { echo "FAILED: $*"; FAILED=1; }; }

python3 "$ROOT/tools/bundle.py" --out "$WORK/linux-bundle.py" || exit 1
python3 - "$ROOT/installer/install-linux.sh" "$WORK/linux-bundle.py" "$WORK/ai-usage-install-linux.sh" <<'PY' || exit 1
import sys
tmpl, plugin, out = sys.argv[1:]
t = open(tmpl, encoding="utf-8").read()
p = open(plugin, encoding="utf-8").read().rstrip("\n")
assert t.count("__PLUGIN__") == 1, "template needs exactly one __PLUGIN__ marker"
assert "PLUGIN_EOF" not in p, "plugin must not contain the heredoc delimiter"
open(out, "w", encoding="utf-8").write(t.replace("__PLUGIN__", p))
PY
bash -n "$WORK/ai-usage-install-linux.sh" || exit 1
awk '/<<.PLUGIN_EOF./{f=1;next}/^PLUGIN_EOF$/{f=0}f' "$WORK/ai-usage-install-linux.sh" |
  diff -q - "$WORK/linux-bundle.py" >/dev/null || { echo "embedded plugin differs"; exit 1; }
echo "built $WORK/linux-bundle.py and $WORK/ai-usage-install-linux.sh"

MOUNTS=(-v "$ROOT:/repo:ro" -v "$WORK:/work:ro")
step "plain mode, python:3.9-slim"
run docker run --rm --init "${MOUNTS[@]}" -e PYTHONPATH=/repo/tests python:3.9-slim \
  python3 /repo/tests/docker/plain_check.py /work/linux-bundle.py

for base in ubuntu:24.04 debian:bullseye; do
  tag="cub-linux-test:${base//[:.]/-}"
  docker build -q --build-arg BASE="$base" -t "$tag" "$ROOT/tests/docker" >/dev/null || { FAILED=1; continue; }
  step "tray backend, $base"
  run docker run --rm --init "${MOUNTS[@]}" "$tag" \
    xvfb-run -a dbus-run-session -- bash /repo/tests/docker/tray_check.sh /work/linux-bundle.py
done

step "installer, ubuntu:24.04 with PyGObject"
run docker run --rm --init "${MOUNTS[@]}" cub-linux-test:ubuntu-24-04 \
  xvfb-run -a dbus-run-session -- bash /repo/tests/docker/installer_check.sh /work/ai-usage-install-linux.sh gui

step "installer, ubuntu:24.04 without PyGObject"
docker build -q --build-arg GUI=0 -t cub-linux-test:nogui "$ROOT/tests/docker" >/dev/null || FAILED=1
run docker run --rm --init "${MOUNTS[@]}" cub-linux-test:nogui \
  bash /repo/tests/docker/installer_check.sh /work/ai-usage-install-linux.sh nogui

echo; [ "$FAILED" = 0 ] && echo "ALL LINUX CHECKS PASSED" || echo "SOME LINUX CHECKS FAILED"
exit $FAILED
