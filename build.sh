#!/bin/bash
# Build the plugin and the installers for every OS:
#   plugin/claude-usage.1m.py             the single-file plugin, flattened from src/ (committed)
#                                         (name kept on purpose: installs self-update by that path)
#   dist/AI-Usage.dmg                     macOS: the SwiftBar installer in a disk image
#   dist/ai-usage-install.command         macOS: the same installer, bare
#   dist/ai-usage-install-linux.sh        Linux: Argos (GNOME) or tray installer
#   dist/AI-Usage-Windows.zip             Windows: tray installer (.cmd launcher + .ps1)
set -euo pipefail
cd "$(dirname "$0")"

PY=python3
[ -x /usr/bin/python3 ] && PY=/usr/bin/python3
PLUGIN=plugin/claude-usage.1m.py
DIST=dist
DMG_ENTRY="Cài đặt AI Usage.command"

rm -rf "$DIST" && mkdir -p "$DIST"
"$PY" tools/bundle.py

# Substitute the plugin into each installer template: a heredoc line (marker __PLUGIN__) or
# base64 (marker __PLUGIN_B64__), and check the embedded copy matches byte for byte.
"$PY" - "$PLUGIN" "$DIST" <<'EOF'
import base64, codecs, os, re, sys, zipfile
plugin, dist = sys.argv[1:]
p = open(plugin, encoding="utf-8").read().rstrip("\n")
assert "PLUGIN_EOF" not in p, "plugin must not contain the heredoc delimiter"

def heredoc(tmpl, out):
    t = open(tmpl, encoding="utf-8").read()
    assert t.count("__PLUGIN__") == 1, tmpl + " needs exactly one __PLUGIN__ marker"
    text = t.replace("__PLUGIN__", p)
    m = re.search(r"<<'PLUGIN_EOF'\n(.*?)\nPLUGIN_EOF\n", text, re.S)
    assert m and m.group(1) == p, "embedded plugin differs in " + out
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.chmod(out, 0o755)

def crlf(text):
    return text.replace("\r\n", "\n").replace("\n", "\r\n")

heredoc("installer/install.tmpl.sh", os.path.join(dist, "ai-usage-install.command"))
if os.path.exists("installer/install-linux.sh"):
    heredoc("installer/install-linux.sh", os.path.join(dist, "ai-usage-install-linux.sh"))

if os.path.exists("installer/install.ps1"):
    t = open("installer/install.ps1", encoding="utf-8").read()
    assert t.count("__PLUGIN_B64__") == 1, "install.ps1 needs exactly one __PLUGIN_B64__ marker"
    b64 = base64.b64encode((p + "\n").encode("utf-8")).decode("ascii")
    ps1 = t.replace("__PLUGIN_B64__", b64)
    cmd = open("installer/install.cmd", encoding="utf-8").read()
    with zipfile.ZipFile(os.path.join(dist, "AI-Usage-Windows.zip"), "w", zipfile.ZIP_DEFLATED) as z:
        # Windows PowerShell 5.1 reads a script without BOM as ANSI: add a BOM, use CRLF.
        z.writestr("install.ps1", codecs.BOM_UTF8 + crlf(ps1).encode("utf-8"))
        z.writestr("Cài đặt AI Usage.cmd", crlf(cmd).encode("utf-8"))
EOF

bash -n "$DIST/ai-usage-install.command"
if [ -f "$DIST/ai-usage-install-linux.sh" ]; then bash -n "$DIST/ai-usage-install-linux.sh"; fi

if command -v hdiutil >/dev/null 2>&1; then
  mkdir -p "$DIST/dmg"
  cp "$DIST/ai-usage-install.command" "$DIST/dmg/$DMG_ENTRY"
  hdiutil create -quiet -volname "AI Usage" -srcfolder "$DIST/dmg" -fs HFS+ -format UDZO "$DIST/AI-Usage.dmg"
  rm -rf "$DIST/dmg"
fi

echo "Built:"
ls -lh "$PLUGIN" "$DIST"/*
