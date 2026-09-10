#!/bin/sh
set -e

# Wait for the database, then run the rest of the command.
n=0
until python manage.py migrate --noinput; do
    n=$((n + 1))
    if [ "$n" -ge 30 ]; then
        echo "giving up waiting for the database" >&2
        exit 1
    fi
    echo "waiting for the database... ($n)"
    sleep 1
done

exec sh -c "$*"
