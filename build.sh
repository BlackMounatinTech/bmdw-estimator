#!/usr/bin/env bash
set -euo pipefail
pip install -r requirements.txt
mkdir -p .bin
curl -fsSL --retry 3 https://github.com/caddyserver/caddy/releases/download/v2.11.4/caddy_2.11.4_linux_amd64.tar.gz -o /tmp/bmdw-caddy.tar.gz
printf '%s  %s\n' 8220d1f013b6f27510247b2360c9e0ca9f018feebd82515f07635318b34ff9777ccc8fd0b6e6f2486ce3a33fe389fbb7db12d05baa474f4587509fb4f5ebf1c9 /tmp/bmdw-caddy.tar.gz | sha512sum -c -
tar -xzf /tmp/bmdw-caddy.tar.gz -C .bin caddy
chmod +x .bin/caddy
