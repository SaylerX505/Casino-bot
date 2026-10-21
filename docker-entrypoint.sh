#!/bin/sh
set -eu

data_dir="/app/data"
seed_dir="/opt/casino/data-seed"

mkdir -p "$data_dir"

# Seed missing files only. Never replace any existing live data file.
if [ -d "$seed_dir" ]; then
    for source in "$seed_dir"/*; do
        [ -f "$source" ] || continue

        name=${source##*/}
        destination="$data_dir/$name"

        if [ ! -e "$destination" ] && [ ! -L "$destination" ]; then
            cp -p "$source" "$destination"
            printf '[entrypoint] Initialized missing data file: %s\n' "$name"
        fi
    done
fi

# Persistent volumes may be mounted root-owned by the host.
chown -R bot:bot "$data_dir"

# Replace the shell with the bot process so SIGTERM/SIGINT are forwarded.
exec gosu bot "$@"
