#!/bin/bash
#
# Makes the tiles of the GeoTIFFs of a folder and merges them into a tile set, file by file. It runs in the og-tiler
# image, tif2tiles.sh starts it, the options are the same, see ./tif2tiles.sh --help.
#
# Every file is cut into <dest>/__temp__ first, then mergeFolders merges the tiles into <dest>, then the temporary data
# (<dest>/__temp__ and ./temp of the container) is removed. The files go by their names.
#
#   earth, epsg4326: gdalwarp to EPSG:3857 or EPSG:4326, rio rgbify of the heights, gdal2tiles
#   cube, equiangular: cube2tiles.py, a source with no overviews is made a cloud optimized GeoTIFF first
#
# Options:
#   --folder      The folder of the GeoTIFFs (*.tif, *.tiff).
#   --dest        The tile set.
#   --zoom        Zoom levels, 1-14.
#   [--strategy]  The OpenGlobus quad tree strategy of the tiles:
#                   earth       - EarthQuadTreeStrategy, EPSG:3857 (Web Mercator), by default
#                   epsg4326    - EPSG4326QuadTreeStrategy, EPSG:4326 (geodetic)
#                   cube        - CubeQuadTreeStrategy, <dest>/<face>/<z>/<x>/<y>
#                   equiangular - EquiangularCubeQuadTreeStrategy, <dest>/<face>/<z>/<x>/<y>
#   [--cube]      gnomonic | equiangular, the same as --strategy cube | equiangular.
#   [--s_epsg]    EPSG code of the files.
#   [--t_srs]     Reprojection of the files before the tiles (PROJ string), as a geoid model.
#   [--b]         "left top right bottom" in degrees, the bounds of the files with no georeferencing.
#   [--h] [--r]   minHeight and resolution of the heights, -10000 and 0.1 by default (*).
#   [--norgbify]  Imagery: the files are images, not heights.
#   [--format]    webp | png. By default the format of the tiles in the destination, webp for a new one.
#   [--quality]   1..100, lossy webp of the imagery. The heights are always lossless.
#   [--tilesize]  128 | 256 | 512 px. By default the size of the tiles in the destination, 256 for a new one.
#   [--processes] Processes of the tiles, the cpus by default.
#
# (*) - minHeight + resolution * (r * 256 * 256 + g * 256 + b), where r,g,b pixel color
# -----------------------------------------------------------------------------

norgbify=false

while [ $# -gt 0 ]; do
    case "$1" in
        --norgbify)
            norgbify=true
            shift
            ;;
        --*)
            if [ $# -lt 2 ]; then
                echo "$1 has no value"
                exit 1
            fi
            declare "${1/--/}"="$2"
            shift 2
            ;;
        *)
            shift
            ;;
    esac
done

# Options

if [ -n "$cube" ]; then
    case "$cube" in
        gnomonic) strategy=cube ;;
        equiangular) strategy=equiangular ;;
        *) echo "Unknown --cube $cube, gnomonic or equiangular"; exit 1 ;;
    esac
fi
strategy=${strategy:-earth}
case "$strategy" in
    earth | epsg4326 | cube | equiangular) ;;
    *) echo "Unknown --strategy $strategy: earth, epsg4326, cube or equiangular"; exit 1 ;;
esac

h=${h:--10000}
r=${r:-0.1}
processes=${processes:-$(nproc)}

mkdir -p $dest

# Tile format and size: the ones of the tiles in the destination, webp and 256 for a new one
tile=$(find $dest -path $dest/__temp__ -prune -o -type f \( -name '*.png' -o -name '*.webp' \) -print -quit 2>/dev/null)
existing=${tile:+${tile##*.}}
existing_size=""
[ -n "$tile" ] && existing_size=$(gdalinfo "$tile" | sed -n 's/^Size is \([0-9]*\),.*/\1/p')
format=${format:-${existing:-webp}}
tilesize=${tilesize:-${existing_size:-256}}
echo "Tile format: $format, size: $tilesize px, strategy: $strategy"
if [ -n "$existing" ] && [ "$existing" != "$format" ]; then
    echo "The destination $dest has $existing tiles, one tile set has one format: run with --format $existing"
    exit 1
fi
case "$tilesize" in
    128 | 256 | 512) ;;
    *) echo "Unknown --tilesize $tilesize, 128, 256 or 512"; exit 1 ;;
esac
if [ -n "$existing_size" ] && [ "$existing_size" != "$tilesize" ]; then
    echo "The destination $dest has $existing_size px tiles, one tile set has one size: run with --tilesize $existing_size"
    exit 1
fi

# gdal2tiles options of the heights and of the imagery
dem_driver=()
sat_driver=()
if [ "$format" = webp ]; then
    dem_driver=(--tiledriver=WEBP --webp-lossless)
    if [ -n "$quality" ]; then
        sat_driver=(--tiledriver=WEBP --webp-quality=$quality)
    else
        sat_driver=(--tiledriver=WEBP --webp-lossless)
    fi
elif [ "$format" != png ]; then
    echo "Unknown --format $format, png or webp"
    exit 1
fi

# Files

shopt -s nullglob nocaseglob
files=($folder/*.tif $folder/*.tiff)
shopt -u nocaseglob
if [ ${#files[@]} -eq 0 ]; then
    echo "No GeoTIFF in $folder"
    exit 1
fi

# The tiles of the cube strategies, cube2tiles.py
cube_tiles() {
    local src=$1 z=$2 cube=gnomonic
    [ "$strategy" = equiangular ] && cube=equiangular

    if [ -n "$t_srs" ]; then
        echo "...REPROJECTING TO $t_srs:"
        gdalwarp ${s_epsg:+-s_srs EPSG:$s_epsg} -t_srs "$t_srs" -r average -of COG -co BIGTIFF=YES \
            -co COMPRESS=ZSTD -co PREDICTOR=YES -co NUM_THREADS=ALL_CPUS $src ./temp/source_tsrs.tif || return 1
        src=./temp/source_tsrs.tif
    elif ! gdalinfo $src | grep -q "Overviews:"; then
        # A source with no overviews is made a cloud optimized GeoTIFF first
        echo "...MAKING CLOUD OPTIMIZED GEOTIFF:"
        gdal_translate -of COG -co BLOCKSIZE=512 -co COMPRESS=ZSTD -co PREDICTOR=YES -co BIGTIFF=YES \
            -co OVERVIEW_RESAMPLING=AVERAGE -co NUM_THREADS=ALL_CPUS $src ./temp/source_cog.tif || return 1
        src=./temp/source_cog.tif
    fi

    local args=(--zoom $z --cube $cube --format $format --tile-size $tilesize --processes $processes)
    [ -n "$b" ] && args+=(--bounds $b)
    [ -n "$s_epsg" ] && [ -z "$t_srs" ] && args+=(--s_srs EPSG:$s_epsg)

    echo "...CREATING CUBE TILES:"
    if [ "$norgbify" = false ]; then
        python3 ./cube2tiles.py $src $dest/__temp__ "${args[@]}" --encoding rgb --min-height $h --resolution $r
    else
        python3 ./cube2tiles.py $src $dest/__temp__ "${args[@]}" ${quality:+--quality $quality} --encoding image
    fi
}

# The tiles of the earth (EPSG:3857) and epsg4326 strategies, rio rgbify and gdal2tiles
gdal_tiles() {
    local src=$1 z=$2 s_srs=()

    if [ -n "$b" ]; then
        # The bounds of a file with no georeferencing
        gdal_translate -q -of VRT -a_ullr $b -a_srs EPSG:4326 $src ./temp/source_bounds.vrt || return 1
        src=./temp/source_bounds.vrt
    elif [ -n "$s_epsg" ]; then
        s_srs=(-s_srs EPSG:$s_epsg)
    fi

    if [ -n "$t_srs" ]; then
        echo "...REPROJECTING TO $t_srs:"
        gdalwarp -co BIGTIFF=YES "${s_srs[@]}" -t_srs "$t_srs" -r average -of GTiff $src ./temp/source_tsrs.tif || return 1
        src=./temp/source_tsrs.tif
        s_srs=(-s_srs "$t_srs")
    fi

    local t_epsg=EPSG:3857 profile=(--profile=mercator)
    if [ "$strategy" = epsg4326 ]; then
        t_epsg=EPSG:4326
        profile=(--profile=geodetic --tmscompatible)
    fi

    echo "...REPROJECTING TO $t_epsg:"
    gdalwarp -co BIGTIFF=YES -co TILED=YES -co COMPRESS=DEFLATE "${s_srs[@]}" -t_srs $t_epsg -r near $src \
        ./temp/source_proj.tif || return 1

    if [ "$norgbify" = false ]; then
        echo "...SETTING NODATA VALUES:"
        gdalwarp -co BIGTIFF=YES -dstnodata None -co TILED=YES -co COMPRESS=DEFLATE ./temp/source_proj.tif \
            ./temp/source_nodata.tif || return 1
        echo "...CREATING RGB:"
        rio rgbify --co BIGTIFF=YES -b $h -i $r ./temp/source_nodata.tif ./temp/source_rgb.tif || return 1
        gdal2tiles.py "${profile[@]}" -w none --no-kml --xyz --zoom=$z -r near --tilesize=$tilesize \
            --processes=$processes "${dem_driver[@]}" ./temp/source_rgb.tif $dest/__temp__ --config GDAL_PAM_ENABLED NO
    else
        local sat=./temp/source_proj.tif
        # WebP has no gray with alpha, the gray is taken as rgb
        if [ "$format" = webp ] && [ "$(gdalinfo $sat | grep -c '^Band ')" = 1 ]; then
            gdal_translate -q -of VRT -b 1 -b 1 -b 1 $sat ./temp/source_rgb.vrt
            sat=./temp/source_rgb.vrt
        fi
        gdal2tiles.py "${profile[@]}" -w none --no-kml --xyz --zoom=$z -r near --tilesize=$tilesize \
            --processes=$processes "${sat_driver[@]}" $sat $dest/__temp__ --config GDAL_PAM_ENABLED NO
    fi
}

# Every file: tiles to the temporary folder, merge, clean up

for f in "${files[@]}"; do
    echo ">>> PROCESSING $f"
    rm -rf $dest/__temp__ ./temp
    mkdir -p $dest/__temp__ ./temp

    if [ "$strategy" = cube ] || [ "$strategy" = equiangular ]; then
        cube_tiles $f $zoom || exit 1
    else
        gdal_tiles $f $zoom || exit 1
    fi

    echo "...MERGING:"
    if [ "$norgbify" = false ]; then
        ./mergeFolders $dest/__temp__ $dest $h $r || exit 1
    else
        ./mergeFolders $dest/__temp__ $dest --alpha ${quality:+--quality $quality} || exit 1
    fi

    rm -rf $dest/__temp__ ./temp
    echo $'\n'
done
