# Linux test image for the tray backend and install-linux.sh.
# BASE: ubuntu:26.04 (Python 3.14) or debian:bullseye (Python 3.9, the oldest supported).
# GUI=1 adds PyGObject + Ayatana AppIndicator + Xvfb + D-Bus; GUI=0 is python3 only, to test the
# installer's "missing libraries" path.
ARG BASE=ubuntu:26.04
FROM ${BASE}
ARG GUI=1
ENV DEBIAN_FRONTEND=noninteractive
# bullseye left deb.debian.org for archive.debian.org
RUN if grep -q bullseye /etc/os-release; then \
      sed -i -e 's|deb.debian.org|archive.debian.org|' -e '/security/d' -e '/bullseye-updates/d' /etc/apt/sources.list; fi
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 procps curl ca-certificates \
 && if [ "$GUI" = 1 ]; then apt-get install -y --no-install-recommends python3-gi \
      gir1.2-gtk-3.0 gir1.2-ayatanaappindicator3-0.1 xvfb xauth dbus dbus-x11 \
      adwaita-icon-theme; fi \
 && rm -rf /var/lib/apt/lists/*
RUN useradd -m tester
USER tester
WORKDIR /home/tester
