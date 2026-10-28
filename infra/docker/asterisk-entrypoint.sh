#!/bin/sh
# Render the config templates from the environment, then exec Asterisk.
# Only the variables listed in VARS are substituted, so Asterisk's own ${EXTEN} etc. survive.
set -eu

SRC=/usr/share/callshield/asterisk
DST=/etc/asterisk

# Defaults for optional values. Required secrets have NO default and fail loudly.
: "${ASTERISK_HTTP_BIND:=0.0.0.0}"
: "${TRUNK_TRANSPORT:=transport-udp}"
: "${TRUNK_PORT:=5060}"
: "${TRUNK_CODECS:=ulaw}"
: "${EXTERNAL_IP:=}"
: "${TRUNK_HOST:?set TRUNK_HOST in .env (your SIP provider host)}"
: "${TRUNK_USERNAME:=}"
: "${TRUNK_PASSWORD:=}"
: "${USER_ENDPOINT_PASSWORD:?set USER_ENDPOINT_PASSWORD in .env}"
: "${CALLSHIELD_ASTERISK_ARI_PASSWORD:?set CALLSHIELD_ASTERISK_ARI_PASSWORD in .env}"
export ASTERISK_HTTP_BIND TRUNK_TRANSPORT TRUNK_PORT TRUNK_CODECS EXTERNAL_IP TRUNK_HOST \
       TRUNK_USERNAME TRUNK_PASSWORD USER_ENDPOINT_PASSWORD CALLSHIELD_ASTERISK_ARI_PASSWORD

VARS='${ASTERISK_HTTP_BIND} ${TRUNK_TRANSPORT} ${TRUNK_PORT} ${TRUNK_CODECS} ${EXTERNAL_IP}
${TRUNK_HOST} ${TRUNK_USERNAME} ${TRUNK_PASSWORD} ${USER_ENDPOINT_PASSWORD}
${CALLSHIELD_ASTERISK_ARI_PASSWORD}'

for name in pjsip.conf extensions.conf ari.conf http.conf rtp.conf modules.conf; do
  envsubst "$VARS" < "$SRC/$name" > "$DST/$name"
  chmod 0640 "$DST/$name"
done

exec "$@"
