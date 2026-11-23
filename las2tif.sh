#!/bin/bash
#
# Makes a DEM GeoTIFF of a LAS or LAZ point cloud by pc2dem.py of OpenDroneMap, in its Docker image opendronemap/odm
# (./install.sh pulls it). The GeoTIFF is next to the point cloud with its name: LAS_17758875.las -> LAS_17758875.tif.
#
# Usage: ./las2tif.sh --mount <folder> --filename <point cloud> --resolution <size> [--type dsm|dtm] [--out <file>]
#
# Options:
#   --mount        The folder of the point cloud, writable: OpenDroneMap writes dsm.tif or dtm.tif there first.
#   --filename     The point cloud in the folder, .las or .laz.
#   --resolution   Pixel size of the DEM in the units of the point cloud, meters as a rule: about the spacing of the
#                  points, 0.25 - 1 for airborne lidar.
#   [--type]       dsm - the surface with the buildings and the trees, the highest points, by default;
#                  dtm - the ground, only the points classified as ground (class 2), the point cloud must have them.
#   [--out]        The GeoTIFF, <folder>/<name>.tif by default.
#   -h, --help     This help.
#
# Example:
#
# ./las2tif.sh --mount /mnt/d/terrain/usa/chicago --filename LAS_17758875.las --resolution 0.25 --type dtm
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
        --mount | --filename | --resolution | --type | --out)
            if [ $# -lt 2 ]; then
                echo "$1 has no value, see ./las2tif.sh --help"
                exit 1
            fi
            declare "${1/--/}"="$2"
            shift 2
            ;;
        *)
            echo "Unknown argument $1, see ./las2tif.sh --help"
            exit 1
            ;;
    esac
done

if [ -z "$mount" ] || [ -z "$filename" ] || [ -z "$resolution" ]; then
    echo "Set --mount, --filename and --resolution, see ./las2tif.sh --help"
    exit 1
fi
case "$type" in
    dsm | dtm) ;;
    *)
        echo "Unknown --type $type, dsm or dtm"
        exit 1
        ;;
esac
if [ ! -f "$mount/$filename" ]; then
    echo "No file $mount/$filename"
    exit 1
fi

mount=$(realpath "$mount")
output=${out:-$mount/${filename%.*}.tif}

if [ -e "$mount/$type.tif" ]; then
    echo "$mount/$type.tif exists, OpenDroneMap writes its DEM there: remove or rename it"
    exit 1
fi

tty=""
[ -t 0 ] && tty="-it"

# pc2dem.py writes dsm.tif or dtm.tif next to the point cloud
docker run $tty --rm -v "$mount":/input --entrypoint /code/contrib/pc2dem/pc2dem.py opendronemap/odm \
    "/input/$filename" --type "$type" --resolution "$resolution" || exit 1

mkdir -p "$(dirname "$output")"
mv "$mount/$type.tif" "$output" || exit 1
echo "$output"
