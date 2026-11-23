#!/bin/bash
#
# Makes OpenGlobus tiles of heights or imagery from GeoTIFFs: a file, several files, a folder or a ZIP. The new tiles
# are merged into the tile set, so new files can be added to it later.
#
# Usage: ./tif2tiles.sh --s_file <file> | --s_folder <folder> | --s_zip <zip> --dest <tile set> --zoom <min>-<max> [options]
#
# Options:
#   --s_file       A GeoTIFF, it can be given several times.
#   --s_folder     A folder of GeoTIFFs (*.tif, *.tiff).
#   --s_zip        A ZIP of GeoTIFFs.
#   --dest         The tile set, it is made when there is none.
#   --zoom         Zoom levels, 1-14.
#   [--strategy]   The OpenGlobus quad tree strategy (quadTreeStrategyPrototype of Globe) of the tiles:
#                    earth       - EarthQuadTreeStrategy, EPSG:3857 (Web Mercator), <dest>/<z>/<x>/<y>, by default
#                    epsg4326    - EPSG4326QuadTreeStrategy, EPSG:4326 (geodetic), <dest>/<z>/<x>/<y>
#                    cube        - CubeQuadTreeStrategy, <dest>/<face>/<z>/<x>/<y>
#                    equiangular - EquiangularCubeQuadTreeStrategy, <dest>/<face>/<z>/<x>/<y>
#                  earth and epsg4326 reproject the files to EPSG:3857 or EPSG:4326 of the Earth, the files of the
#                  other planets are given with --b there. The cube tiles are made in the CRS of the files, any planet.
#   [--cube]       gnomonic | equiangular, the same as --strategy cube | equiangular.
#   [--s_epsg]     EPSG code of the files, when they have no or a wrong one.
#   [--t_srs]      Reprojection of the files before the tiles (PROJ string), as a geoid model:
#                  "+proj=latlong +datum=WGS84 +geoidgrids=/usr/share/gdal/egm08_25.gtx"
#   [--b]          "left top right bottom" in degrees: the files are taken as the longitudes and latitudes of these
#                  bounds, for the files with no georeferencing.
#   [--h] [--r]    minHeight and resolution of the heights, -10000 and 0.1 by default (*).
#   [--norgbify]   Imagery: the files are images, not heights.
#   [--format]     webp | png. By default the format of the tiles in the destination, webp for a new one.
#                  One tile set has one format.
#   [--quality]    1..100, lossy webp of the imagery. The heights are always lossless.
#   [--tilesize]   128 | 256 | 512, the tile side in pixels. By default the size of the tiles in the destination, 256
#                  for a new one. One tile set has one size, the heights of other sizes need imageSize of RgbTerrain.
#   [--processes]  Processes of the tiles, the cpus by default.
#   [--cpus]       Docker cpus, 0-4 by default.
#   -h, --help     This help.
#
#   The image is og-tiler, the environment variable OG_TILER_IMAGE sets another one.
#
# Examples:
#
# 1) ./tif2tiles.sh --s_zip /mnt/d/terrain/nz/lds-canterbury-amberley-lidar-1m-dem-2012-GTiff.zip --dest /mnt/d/terrain/nz/dest --zoom 1-4
#
# 2) ./tif2tiles.sh --s_folder /mnt/d/terrain/andorra --dest /dest --zoom 1-4 --s_epsg 27563
#
# 3) The Moon heights for EquiangularCubeQuadTreeStrategy, LOLA, then the more detailed SLDEM over it:
#    ./tif2tiles.sh --s_file ./Lunar_LRO_LOLA_Global_LDEM_118m_Mar2014.tif --dest ./moon/dem --zoom 0-7 --strategy equiangular
#    ./tif2tiles.sh --s_file ./Lunar_LRO_LOLAKaguya_DEMmerge_60N60S_512ppd.tif --dest ./moon/dem --zoom 0-8 --strategy equiangular
#
# 4) Imagery of a file with no georeferencing:
#    ./tif2tiles.sh --s_file ./Mars_Viking_MDIM21_ClrMosaic_global_232m_BASE.tif --b "-180 90 180 -90" --norgbify \
#        --dest ./mars/sat --zoom 0-7 --strategy equiangular
#
# 5) A planet in EPSG:4326, the file is taken as the longitudes and latitudes of the bounds:
#    ./tif2tiles.sh --s_file ./Lunar_LRO_LOLA_Global_LDEM_118m_Mar2014.tif --b "-180 90 180 -90" --h -20000 --r 0.1022 \
#        --zoom 0-4 --dest ./dest --strategy epsg4326
#
# (*) - minHeight + resolution * (r * 256 * 256 + g * 256 + b), where r,g,b pixel color
# -----------------------------------------------------------------------------

# The help is the comment at the top of this file
help() {
    sed -n '2,/^# ---/p' "$0" | sed 's/^# \{0,1\}//'
}

if [ $# -eq 0 ]; then
    help
    exit 1
fi

s_files=()
norgbify=false

while [ $# -gt 0 ]; do
    case "$1" in
        -h | --help)
            help
            exit 0
            ;;
        --norgbify)
            norgbify=true
            shift
            ;;
        --*)
            if [ $# -lt 2 ]; then
                echo "$1 has no value, see ./tif2tiles.sh --help"
                exit 1
            fi
            if [ "$1" = --s_file ]; then
                s_files+=("$2")
            else
                declare "${1/--/}"="$2"
            fi
            shift 2
            ;;
        *)
            echo "Unknown argument $1, see ./tif2tiles.sh --help"
            exit 1
            ;;
    esac
done

sources=0
[ ${#s_files[@]} -gt 0 ] && sources=$((sources + 1))
[ -n "$s_folder" ] && sources=$((sources + 1))
[ -n "$s_zip" ] && sources=$((sources + 1))
if [ $sources -ne 1 ] || [ -z "$dest" ] || [ -z "$zoom" ]; then
    echo "Set one of --s_file, --s_folder or --s_zip, and --dest and --zoom"
    exit 1
fi

mkdir -p "$dest"
dest=$(realpath "$dest")
mounts=(-v "$dest":/__dest__)

if [ ${#s_files[@]} -gt 0 ]; then
    for f in "${s_files[@]}"; do
        if [ ! -f "$f" ]; then
            echo "No file $f"
            exit 1
        fi
        mounts+=(-v "$(realpath "$f")":/__processing__/"$(basename "$f")":ro)
    done
elif [ -n "$s_folder" ]; then
    mounts+=(-v "$(realpath "$s_folder")":/__processing__:ro)
else
    zip_folder=./_temp_$(basename "$s_zip" .zip)
    rm -rf "$zip_folder"
    unzip -q "$s_zip" -d "$zip_folder" || exit 1
    mounts+=(-v "$(realpath "$zip_folder")":/__processing__:ro)
fi

options=(--folder /__processing__ --dest /__dest__ --zoom "$zoom")
for o in strategy cube s_epsg t_srs b h r format quality tilesize processes; do
    [ -n "${!o}" ] && options+=(--$o "${!o}")
done
[ "$norgbify" = true ] && options+=(--norgbify)

tty=""
[ -t 0 ] && tty="-it"

docker run --cpuset-cpus ${cpus:-0-4} $tty --rm "${mounts[@]}" ${OG_TILER_IMAGE:-og-tiler} /bin/bash ./rgbifyff.sh "${options[@]}"
status=$?

[ -n "$zip_folder" ] && rm -rf "$zip_folder"
exit $status
