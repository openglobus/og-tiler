#!/bin/bash
#
# Installs og-tiler: builds and tests its Docker image, pulls the OpenDroneMap image for the point clouds.
# Run it again after an update of the repository.
#
# Usage: ./install.sh
#
# The image is og-tiler, the environment variable OG_TILER_IMAGE sets another one.
# -----------------------------------------------------------------------------

set -eo pipefail

if [ $# -gt 0 ]; then
    sed -n '2,/^# ---/p' "$0" | sed 's/^# \{0,1\}//'
    exit 0
fi

if ! command -v docker >/dev/null; then
    echo "Docker is not found, install it first: https://docs.docker.com/engine/install/"
    exit 1
fi

cd "$(dirname "$0")"
image=${OG_TILER_IMAGE:-og-tiler}

echo ">>> BUILDING THE IMAGE $image"
docker build -t "$image" .

echo ">>> TESTS"
docker run --rm -v "$PWD":/repo -w /repo "$image" python3 -u test/cube/test_cube2tiles.py | grep -E "^(ok|FAIL|skip)"

echo ">>> OPENDRONEMAP IMAGE"
# Only the LAS point clouds need it, the tiler works without it
if ! docker pull opendronemap/odm; then
    echo "WARNING: opendronemap/odm is not pulled, las2tif.sh, las2tif_folder.sh and las2tiles.sh do not work until 'docker pull opendronemap/odm' succeeds"
fi

echo "Installed."
echo "./tif2tiles.sh --help prints the options."
