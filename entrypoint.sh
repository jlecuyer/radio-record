#!/bin/bash

PUID=${PUID:-1000}
PGID=${PGID:-1000}
UMASK=${UMASK:-022}

groupadd -o -g "$PGID" radiouser 2>/dev/null
useradd -o -u "$PUID" -g "$PGID" -d /app -s /bin/bash radiouser 2>/dev/null

chown -R radiouser:radiouser /radio /app
chmod -R 776 /radio

umask "$UMASK"

exec gosu radiouser /app/.venv/bin/radio-record --verbose -o /radio
