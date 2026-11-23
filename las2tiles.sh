#!/bin/bash
#
# Makes OpenGlobus tiles of heights from LAS and LAZ point clouds: a file, several files, a folder or a ZIP. The new
# tiles are merged into the tile set, so new point clouds can be added to it later.
#
# Usage: ./las2tiles.sh --s_file <file> | --s_folder <folder> | --s_zip <zip> --dest <tile set> --zoom <min>-<max>
#                       --resolution <size> [options]
#
# Options:
#   --s_file       A point cloud, .las or .laz, it can be given several times.
#   --s_folder     A folder of point clouds.
#   --s_zip        A ZIP of point clouds.
#   --dest         The tile set.
#   --zoom         Zoom levels <min>-<max>.
#   --resolution   Pixel size of the DEMs, see ./las2tif.sh --help.
#   [--type]       dsm (by default) or dtm, see ./las2tif.sh --help.
#   [--strategy] [--cube] [--s_epsg] [--t_srs] [--h] [--r] [--format] [--tilesize] [--processes] [--cpus]
#                  The same as in tif2tiles.sh, see ./tif2tiles.sh --help.
#   -h, --help     This help.
#
# Example:
#
# ./las2tiles.sh --s_folder /mnt/d/terrain/usa/chicago --resolution 0.25 --type dtm --dest /mnt/d/terrain/usa/dest --zoom 10-19
# -----------------------------------------------------------------------------

help() {
    sed -n '2,/^# ---/p' "$0" | sed 's/^# \{0,1\}//'
}

if [ $# -eq 0 ]; then
    help
    exit 1
fi

s_files=()
tile_options=()
type=dsm

while [ $# -gt 0 ]; do
    case "$1" in
        -h | --help)
            help
            exit 0
            ;;
        --s_file | --s_folder | --s_zip | --dest | --zoom | --resolution | --type | --strategy | --cube | --s_epsg | \
            --t_srs | --h | --r | --format | --tilesize | --processes | --cpus)
            if [ $# -lt 2 ]; then
                echo "$1 has no value, see ./las2tiles.sh --help"
                exit 1
            fi
            case "$1" in
                --s_file) s_files+=("$2") ;;
                --s_folder | --s_zip | --dest | --zoom | --resolution | --type) declare "${1/--/}"="$2" ;;
                *) tile_options+=("$1" "$2") ;;
            esac
            shift 2
            ;;
        *)
            echo "Unknown argument $1, see ./las2tiles.sh --help"
            exit 1
            ;;
    esac
done

sources=0
[ ${#s_files[@]} -gt 0 ] && sources=$((sources + 1))
[ -n "$s_folder" ] && sources=$((sources + 1))
[ -n "$s_zip" ] && sources=$((sources + 1))
if [ $sources -ne 1 ] || [ -z "$dest" ] || [ -z "$zoom" ] || [ -z "$resolution" ]; then
    echo "Set one of --s_file, --s_folder or --s_zip, and --dest, --zoom and --resolution"
    exit 1
fi
case "$type" in
    dsm | dtm) ;;
    *)
        echo "Unknown --type $type, dsm or dtm"
        exit 1
        ;;
esac

here=$(dirname "$0")

temp_tif=""
zip_folder=""
trap 'rm -f "$temp_tif"; [ -n "$zip_folder" ] && rm -rf "$zip_folder"' EXIT

# The point clouds, by their names
if [ -n "$s_zip" ]; then
    zip_folder=./_temp_$(basename "$s_zip" .zip)
    rm -rf "$zip_folder"
    unzip -q "$s_zip" -d "$zip_folder" || exit 1
    s_folder=$zip_folder
fi
if [ -n "$s_folder" ]; then
    shopt -s nullglob nocaseglob
    s_files=("$s_folder"/*.la[sz])
    shopt -u nullglob nocaseglob
fi
if [ ${#s_files[@]} -eq 0 ]; then
    echo "No LAS or LAZ in $s_folder"
    exit 1
fi
for f in "${s_files[@]}"; do
    if [ ! -f "$f" ]; then
        echo "No file $f"
        exit 1
    fi
done

failed=0
for f in "${s_files[@]}"; do
    echo ">>> POINT CLOUD $f"
    name=$(basename "$f")
    temp_tif="$(dirname "$f")/_temp_${name%.*}.tif"

    if ! "$here/las2tif.sh" --mount "$(dirname "$f")" --filename "$name" --resolution "$resolution" --type "$type" \
        --out "$temp_tif"; then
        echo "...FAILED: $f"
        failed=$((failed + 1))
        continue
    fi

    "$here/tif2tiles.sh" --s_file "$temp_tif" --dest "$dest" --zoom "$zoom" "${tile_options[@]}" || exit 1
    rm -f "$temp_tif"
    echo
done

if [ $failed -gt 0 ]; then
    echo "$failed of ${#s_files[@]} point clouds failed"
    exit 1
fi
