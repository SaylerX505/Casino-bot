#!/bin/sh
set -eu

data_dir="/app/data"
seed_dir="/opt/casino/data-seed"

mkdir -p "$data_dir"

# --- SAFE DATA SEEDING ---
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

# --- VOLUME PERMISSIONS ---
chown -R bot:bot "$data_dir"

# --- PROCESS SIGNALS ---
exec gosu bot "$@"
