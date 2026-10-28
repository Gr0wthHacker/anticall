#!/bin/sh
# ARI liveness. Credentials go through curl's stdin config so they never appear in `ps`.
set -eu
printf 'user = "callshield:%s"\n' "$CALLSHIELD_ASTERISK_ARI_PASSWORD" \
  | curl -fsS -K - --max-time 4 -o /dev/null http://127.0.0.1:8088/ari/asterisk/info
