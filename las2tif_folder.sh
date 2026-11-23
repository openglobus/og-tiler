#!/bin/bash
#
# Makes the DEM GeoTIFFs of all the LAS and LAZ point clouds of a folder by las2tif.sh, every GeoTIFF is next to its
# point cloud. Then the tiles of the folder are made by ./tif2tiles.sh --s_folder.
#
# Usage: ./las2tif_folder.sh --folder <folder> --resolution <size> [--type dsm|dtm]
#
# Options:
#   --folder       The folder of the point clouds (*.las, *.laz).
#   --resolution   Pixel size of the DEMs, see ./las2tif.sh --help.
#   [--type]       dsm (by default) or dtm, see ./las2tif.sh --help.
#   -h, --help     This help.
#
# A point cloud that fails is reported, the others go on, the exit code is 1 then.
#
# Example:
#
# ./las2tif_folder.sh --folder /mnt/d/terrain/usa/chicago --resolution 0.25 --type dtm
# -----------------------------------------------------------------------------

help() {
    sed -n '2,/^# ---/p' "$0" | sed 's/^# \{0,1\}//'
}

if [ $# -eq 0 ]; then
    help
    exit 1
fi

type=dsm

while [ $# -gt 0 ]; do
    case "$1" in
        -h | --help)
            help
            exit 0
            ;;
        --folder | --resolution | --type)
            if [ $# -lt 2 ]; then
                echo "$1 has no value, see ./las2tif_folder.sh --help"
                exit 1
            fi
            declare "${1/--/}"="$2"
            shift 2
            ;;
        *)
            echo "Unknown argument $1, see ./las2tif_folder.sh --help"
            exit 1
            ;;
    esac
done

if [ -z "$folder" ] || [ -z "$resolution" ]; then
    echo "Set --folder and --resolution, see ./las2tif_folder.sh --help"
    exit 1
fi

shopt -s nullglob nocaseglob
files=("$folder"/*.las "$folder"/*.laz)
shopt -u nocaseglob
if [ ${#files[@]} -eq 0 ]; then
    echo "No LAS or LAZ in $folder"
    exit 1
fi

failed=0
for f in "${files[@]}"; do
    echo ">>> PROCESSING $f"
    "$(dirname "$0")/las2tif.sh" --mount "$folder" --filename "$(basename "$f")" --resolution "$resolution" \
        --type "$type" || failed=$((failed + 1))
    echo
done

if [ $failed -gt 0 ]; then
    echo "$failed of ${#files[@]} point clouds failed"
    exit 1
fi
