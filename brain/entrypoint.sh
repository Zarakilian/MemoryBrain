#!/bin/sh
# Fix /app/data ownership at runtime (volume may be mounted as root)
# then drop to non-root brain user to run the server.
if [ "$(id -u)" = "0" ]; then
    chown -R brain:brain /app/data 2>/dev/null || true
    exec gosu brain "$@"
else
    exec "$@"
fi
