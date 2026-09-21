#!/bin/sh
# Remove one track from the edge stream cache (takedown). Run on every edge:
#   infra/scripts/purge-track.sh <track_id> <file_size_bytes>
# Cache keys are "s:<id>:bytes=<start>-<end>" per 1 MiB slice and "t:<id>" for the thumbnail;
# nginx stores each entry under levels=1:2 of md5(key).
set -eu
TRACK_ID="$1"
SIZE="$2"
CACHE_DIR="${CACHE_DIR:-/var/cache/nginx/stream}"
CONTAINER="${CONTAINER:-edge-nginx-1}"
SLICE=1048576

remove() {
    hash=$(printf '%s' "$1" | md5sum | cut -d' ' -f1)
    l1=$(printf '%s' "$hash" | tail -c 1)
    l2=$(printf '%s' "$hash" | tail -c 3 | head -c 2)
    docker exec "$CONTAINER" rm -f "$CACHE_DIR/$l1/$l2/$hash"
}

start=0
while [ "$start" -lt "$SIZE" ]; do
    end=$((start + SLICE - 1))
    remove "s:${TRACK_ID}:bytes=${start}-${end}"
    start=$((start + SLICE))
done
remove "t:${TRACK_ID}"
echo "purged track ${TRACK_ID}"
