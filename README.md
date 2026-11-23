# **og-tiler**

### **Overview**
`og-tiler` makes OpenGlobus tiles of GeoTIFFs in Docker: the heights as RGB encoded tiles and the imagery, for the Web Mercator, EPSG:4326 and cube quad tree strategies, any planet, in WebP or PNG. The tiles of new GeoTIFFs are merged into an existing tile set. It is built on the `osgeo/gdal` Docker image.

---

## **Installation**

Only Docker is needed:

```bash
./install.sh
```

It builds the `og-tiler` image, checks it by the tests and pulls the `opendronemap/odm` image of the LAS point clouds. Run it again after an update of the repository.

`mergeFolders` is compiled from `main.cpp` in the image, no compiler is needed on the machine and the repository has no binary. To run it outside Docker, `make build` compiles it on the machine, it needs `g++`, `make` and `libwebp-dev`.

For the development, the image and the tests alone:

```bash
make image
make test
```

**Scripts:**

| Script                 | Runs on            | Description                                                                                     |
|------------------------|--------------------|-------------------------------------------------------------------------------------------------|
| `install.sh`           | the host           | The installation: the image, its tests and the OpenDroneMap image.                              |
| `tif2tiles.sh`         | the host           | Makes the tiles of GeoTIFFs, see below. `./tif2tiles.sh --help` prints the options.             |
| `rgbifyff.sh`          | the image          | The tiles of every file and their merge, `tif2tiles.sh` runs it.                                |
| `cube2tiles.py`        | the image          | The cube tiles of a file, `rgbifyff.sh` runs it for the cube strategies.                        |
| `mergeFolders`         | the image          | Merges the tiles of one folder into another one, see Merge Existing Tiles.                      |
| `las2tiles.sh`         | the host           | Makes the tiles of LAS and LAZ point clouds, the same options as `tif2tiles.sh`, see LAS Point Clouds. |
| `las2tif.sh`, `las2tif_folder.sh` | the host | A DEM GeoTIFF of a LAS point cloud, or of a folder of them, by OpenDroneMap, see LAS Point Clouds. |

---

## **Parameters**

`tif2tiles.sh` makes the tiles of a GeoTIFF, of several ones, of a folder or of a ZIP of them. The tiles of every file are merged into the destination, so the tiles of new files can be added to a tile set later the same way. `./tif2tiles.sh --help` prints the options.

| **Parameter**         | **Description**                                                                                     | **Example**                                            |
|-----------------------|-----------------------------------------------------------------------------------------------------|--------------------------------------------------------|
| `--s_file`            | A source GeoTIFF, it can be given several times.                                                    | `./Lunar_LDEM_118m.tif`                                |
| `--s_folder`          | A folder of GeoTIFFs.                                                                               | `/mnt/d/terrain/andorra`                               |
| `--s_zip`             | A ZIP of GeoTIFFs.                                                                                  | `/mnt/d/terrain/nz/lds-canterbury.zip`                 |
| `--dest`              | The tile set, it is made when there is none.                                                        | `/mnt/d/terrain/dest`                                  |
| `--zoom`              | Zoom levels `<min>-<max>`.                                                                          | `1-14`                                                 |
| `[--strategy]`        | The OpenGlobus quad tree strategy of the tiles, see below. `earth` by default.                      | `equiangular`                                          |
| `[--cube]`            | `gnomonic` or `equiangular`, the same as `--strategy cube` or `--strategy equiangular`.              | `equiangular`                                          |
| `[--s_epsg]`          | The EPSG code of the files.                                                                         | `27563`                                                |
| `[--t_srs]`           | Reprojection of the files before the tiles (PROJ string), as a geoid model.                         | `+proj=latlong +datum=WGS84 +geoidgrids=egm08_25.gtx`  |
| `[--b]`               | Bounds `"left top right bottom"` in degrees of the files with no georeferencing.                    | `"-180 90 180 -90"`                                    |
| `[--h]`, `[--r]`      | (*) Minimal height and resolution, `-10000` and `0.1` by default.                                   | `--h -20000 --r 0.1022`                                |
| `[--norgbify]`        | Imagery: the files are images, not heights.                                                         |                                                        |
| `[--format]`          | `webp` or `png`, by default the one of the destination, `webp` for a new one, see WebP Tiles.       | `png`                                                  |
| `[--quality]`         | `1..100`, lossy WebP of the imagery. The heights are always lossless.                               | `85`                                                   |
| `[--tilesize]`        | `128`, `256` or `512` px, by default the one of the destination, `256` for a new one, see Tile Size. | `512`                                                  |
| `[--processes]`       | Processes of the tiles, the cpus by default.                                                        | `8`                                                    |
| `[--cpus]`            | Docker cpus, `0-4` by default.                                                                      | `0-7`                                                  |

The image is `og-tiler`, the environment variable `OG_TILER_IMAGE` sets another one.

(*) - `minHeight + resolution * (r * 256 * 256 + g * 256 + b)`, where r,g,b pixel color. The Terrarium encoding of Mapterhorn is the same formula with `--h -32768 --r 0.00390625` (`encoding: "terrarium"` of `RgbTerrain`): 3.9 mm instead of 0.1 m, ±32 km, about a third larger tiles.

**Strategies.** The tiles are made for the quad tree strategy of the globe (`quadTreeStrategyPrototype` of `Globe`), the tiles of one strategy do not fit another one:

| `--strategy`   | OpenGlobus strategy               | Tiles                                     |
|----------------|-----------------------------------|-------------------------------------------|
| `earth`        | `EarthQuadTreeStrategy` (default) | EPSG:3857 (Web Mercator), `<z>/<x>/<y>`   |
| `epsg4326`     | `EPSG4326QuadTreeStrategy`        | EPSG:4326 (geodetic), `<z>/<x>/<y>`       |
| `cube`         | `CubeQuadTreeStrategy`            | gnomonic cube, `<face>/<z>/<x>/<y>`       |
| `equiangular`  | `EquiangularCubeQuadTreeStrategy` | equiangular cube, `<face>/<z>/<x>/<y>`    |

`earth` and `epsg4326` reproject the files by `gdalwarp` to EPSG:3857 or EPSG:4326 of the Earth, then `rio rgbify` and `gdal2tiles` make the tiles. A file of another planet is given there with `--b`, then it is taken as the longitudes and latitudes of the bounds. The cube tiles are made by `cube2tiles.py` in the CRS of the file itself, so they fit any planet.

**Every file** is cut into `<dest>/__temp__` first, then `mergeFolders` merges the tiles into `<dest>`, then the temporary data is removed. The files go by their names, every next file is merged over the ones before. When one file must be over another one, as a more detailed DEM over a global one, they are given by two commands, the second one after the first.

---

## **Usage**

**Earth, a ZIP or a folder:**

```bash
./tif2tiles.sh --s_zip /mnt/d/terrain/nz/lds-canterbury-lidar.zip --dest /mnt/d/terrain/nz/dest --zoom 1-4
./tif2tiles.sh --s_folder /mnt/d/terrain/andorra --dest /dest --zoom 1-4 --s_epsg 27563
./tif2tiles.sh --s_folder ~/usgs/ --dest ~/myterrain/zion --zoom 1-19
```

**The Moon for `EquiangularCubeQuadTreeStrategy`**: the heights of LOLA, then of the more detailed SLDEM (60N-60S) over it, then the imagery:

```bash
./tif2tiles.sh --s_file ./Lunar_LRO_LOLA_Global_LDEM_118m_Mar2014.tif --dest ./moon/dem --zoom 0-7 --strategy equiangular
./tif2tiles.sh --s_file ./Lunar_LRO_LOLAKaguya_DEMmerge_60N60S_512ppd.tif --dest ./moon/dem --zoom 0-8 --strategy equiangular
./tif2tiles.sh --s_file ./Lunar_LRO_LROC-WAC_Mosaic_global_100m_June2013.tif --dest ./moon/sat --zoom 0-7 --strategy equiangular --norgbify
```

**Mars**, the imagery file has no georeferencing, so it has the bounds:

```bash
./tif2tiles.sh --s_file ./Mars_MGS_MOLA_DEM_mosaic_global_463m.tif --dest ./mars/dem --zoom 0-6 --strategy equiangular
./tif2tiles.sh --s_file ./Mars_Viking_MDIM21_ClrMosaic_global_232m_BASE.tif --b "-180 90 180 -90" --dest ./mars/sat --zoom 0-7 \
    --strategy equiangular --norgbify
```

**A planet in EPSG:4326**, the file is taken as the longitudes and latitudes of the bounds:

```bash
./tif2tiles.sh --s_file ./Lunar_LRO_LOLA_Global_LDEM_118m_Mar2014.tif --b "-180 90 180 -90" --h -20000 --r 0.1022 --zoom 0-4 \
    --dest ./dest --strategy epsg4326
```

---

### **Cube Tiles**

- `<dest>/<face>/<z>/<x>/<y>.webp` (or `.png`), 256x256 px (`--tilesize`), `face` is `segment.tileGroup`: 0 top, 1 front (lon 0), 2 right (lon 90), 3 back (lon 180), 4 left (lon -90), 5 bottom. `x` goes from the west and `y` from the north of the face.
- DEM tiles have the same rgb encoding as the other tile sets, no data has alpha 0. Imagery tiles are RGBA.
- The heights are taken with the scale and offset of the file, as the USGS DEMs in half meters with the scale 0.5.
- The longitude and latitude of a cube point are the ones of the geographic CRS of the source itself, so the tiles fit any body.
- A small source can be less than a tile pixel at the low zooms, then such a tile has no data and is not written.
- A tile has only the data, no border. A tile pixel smaller than a source pixel is the bilinear interpolation of the source, a larger one (the low zooms, the poles of a longitude and latitude source, where a pixel takes many columns) is the average of the source pixels of its area, as `gdalwarp -r average`, so there is no aliasing at the poles.
- At a pole a longitude and latitude source has only what its rows there have. A polar stereographic DEM of a pole can be merged over a global one by a second command, as SLDEM over LOLA.

**Zoom levels.** A tile pixel is not coarser than the source pixel from the zoom `ceil(log2(π/2 · R / (tile size · pixel size)))`: 7 for the 118 m LOLA and 8 for the 59 m SLDEM of the Moon with 256 px tiles, one less with 512 px. Gnomonic tiles are up to twice coarser in the middle of a face than at its edges.

**Large sources.** The low zooms of the cube tiles read the overviews of a source instead of the full resolution. A source without them, as the USGS files in the stripes of one row, is made a cloud optimized GeoTIFF in the temporary folder of the container first, then removed, so the original files are given to the commands as they are. A COG made once can be given instead, the scale of the heights is kept:

```bash
gdal_translate -of COG -co BLOCKSIZE=512 -co COMPRESS=DEFLATE -co PREDICTOR=YES -co BIGTIFF=YES \
    -co OVERVIEW_RESAMPLING=AVERAGE -co NUM_THREADS=ALL_CPUS \
    Lunar_LRO_LOLAKaguya_DEMmerge_60N60S_512ppd.tif moon-sldem-cog.tif
```

**OpenGlobus.** The imagery is an `XYZ` layer and the heights are a `CubeRgbTerrain`, both with the `{f}` face index in the url:

```js
const sat = new XYZ("sat", { url: "./sat/{f}/{z}/{x}/{y}.webp", maxNativeZoom: 7 });
const terrain = new CubeRgbTerrain("dem", { url: "./dem/{f}/{z}/{x}/{y}.webp", maxNativeZoom: 8, maxZoom: 10 });
```

See `openglobus/sandbox/moon2` and `openglobus/sandbox/mars2` for the whole examples.

---

### **WebP Tiles**

The tiles are WebP by default. A destination that has tiles already keeps their format, so the new tiles of an existing png set are png. `--format png` or `--format webp` sets it:

```bash
# A new set: webp
./tif2tiles.sh --s_folder /mnt/d/terrain/andorra --dest /dest --zoom 1-14 --s_epsg 27563
# Lossy imagery
./tif2tiles.sh --s_file ./Lunar_LRO_LROC-WAC_Mosaic_global_100m_June2013.tif --dest ./moon/sat --zoom 0-7 --strategy equiangular --norgbify --quality 85
# png, as before
./tif2tiles.sh --s_folder /mnt/d/terrain/andorra --dest /dest --zoom 1-14 --s_epsg 27563 --format png
```

- The heights are lossless WebP, the decoded rgb is exactly the png one (the rgb of no data under alpha 0 too). The tiles are about 2/3 of the png size and are written faster.
- The imagery is lossless too, or lossy with `--quality 1..100`: about 1/3 of the png size at 90, 1/5 at 80. A lossy tile is encoded again when `mergeFolders` updates it. A gray image is written as rgb, WebP has no gray.
- One tile set is either png or webp: `mergeFolders` takes the format by the file extension, does not convert one to the other and stops with an error on the folders of the other formats.
- OpenGlobus loads them as any image, by `createImageBitmap` of the browser, the url is `./sat/{f}/{z}/{x}/{y}.webp`. `RgbTerrain` and `CubeRgbTerrain` read the webp heights as the png ones, `MapterhornTerrain` uses webp too.

---

### **Tile Size**

The tiles are 256x256 px by default, `--tilesize 128` or `--tilesize 512` sets another size, for every strategy. A tile of a zoom covers the same area at any size, so a 512 px tile has the detail of the four 256 px tiles of the next zoom: a zoom less for the same detail, a quarter of the files. A destination that has tiles already keeps their size, one tile set has one size.

```bash
./tif2tiles.sh --s_file ./Lunar_LRO_LOLA_Global_LDEM_118m_Mar2014.tif --dest ./moon/dem512 --zoom 0-6 --strategy equiangular --tilesize 512
```

The imagery of any size is loaded as it is. The heights of a size other than 256 are given to `RgbTerrain` and `CubeRgbTerrain` by `imageSize`:

```js
const terrain = new CubeRgbTerrain("dem", { url: "./dem512/{f}/{z}/{x}/{y}.webp", imageSize: 512, maxNativeZoom: 6, maxZoom: 10 });
```

---

### **Merge Existing Tiles**

To merge RGB tiles from one collection into another, first run the `og-tiler` Docker container:

```bash
docker run -ti -v /media:/terrain og-tiler
```

Then merge tiles:

```bash
./mergeFolders /terrain/heights/public/eu10/ /terrain/heights/public/all/
```

Imagery tiles are merged by the alpha:

```bash
./mergeFolders /terrain/sat/new/ /terrain/sat/all/ --alpha
```

The tiles are `.png` or `.webp`, both folders have the same format, otherwise nothing is merged and the exit code is 1. WebP is written lossless, the imagery can be lossy:

```bash
./mergeFolders /terrain/sat/new/ /terrain/sat/all/ --alpha --quality 85
```

How the new tiles go in: a tile or a folder the tile set does not have yet is moved there as it is. A tile the tile set has already is merged pixel by pixel: the new pixel replaces the old one, except when the new pixel has no data, then the old pixel stays. No data is a transparent pixel (alpha 0), and for the heights also the height `minHeight` (rgb 0) or exactly 0. So a new source changes only the place it covers.

The same tiles come faster in three cases: a new tile that replaces every pixel (a detailed DEM inside its area) is moved over the old one, a new tile with no data is dropped, only the new tile is decoded then, and a tile the merge does not change (the same source again) is not written. A merged new tile is deleted, so the folder of the new tiles is left empty but for the tiles that failed, and its removal after the merge takes no time. The folders are read by one `readdir` each, not a `stat` of every tile, which is slow on the Windows disks (`/mnt/c`).

The tiles of both folders are merged by several threads, the cpus of the process by default (`--threads N`). Every tile is written to a temporary file first and renamed, so a stopped merge leaves no cut tile. Do not run two merges into one folder at the same time.

The tiles and the folders the tile set has not yet are moved (renamed), so the new tiles and the tile set must be on one disk. A tile that can not be decoded or encoded is reported on the screen, the merge goes on.

`mergeFolders` is built from `main.cpp` by the `Makefile` in the Docker image, with libwebp linked statically.

---

### **LAS Point Clouds**

A LAS or LAZ point cloud (lidar) is made a DEM GeoTIFF by [`pc2dem.py`](https://github.com/OpenDroneMap/ODM/tree/master/contrib/pc2dem) of OpenDroneMap in its Docker image `opendronemap/odm` (`install.sh` pulls it), then the GeoTIFF is cut into tiles as any other.

**The tiles of point clouds.** `las2tiles.sh` takes the point clouds as `tif2tiles.sh` takes the GeoTIFFs, a file, several files, a folder or a ZIP, with the same options and `--resolution`, `--type` of the DEMs. The point clouds go one by one by their names: `las2tif.sh` makes the GeoTIFF of a point cloud next to it (`_temp_<name>.tif`), `tif2tiles.sh` merges its tiles into the tile set, then the GeoTIFF is removed. So the folder of the point clouds must be writable. A point cloud that fails is reported and the others go on, a failure of the tiles stops all.

```bash
./las2tiles.sh --s_folder /mnt/d/terrain/usa/chicago --resolution 0.25 --type dtm --dest /mnt/d/terrain/usa/dest --zoom 10-19
./las2tiles.sh --s_file ./cloud1.laz --s_file ./cloud2.laz --resolution 0.5 --type dtm --s_epsg 32633 --dest ./dest --zoom 10-18
```

**The GeoTIFFs alone**, to keep them or to look at them first:

```bash
# A point cloud: /mnt/d/terrain/usa/chicago/LAS_17758875.tif
./las2tif.sh --mount /mnt/d/terrain/usa/chicago --filename LAS_17758875.las --resolution 0.25 --type dtm
# Every .las and .laz of a folder
./las2tif_folder.sh --folder /mnt/d/terrain/usa/chicago --resolution 0.25 --type dtm
# Then the tiles of the GeoTIFFs of the folder
./tif2tiles.sh --s_folder /mnt/d/terrain/usa/chicago --dest /mnt/d/terrain/usa/dest --zoom 10-19
```

| **Parameter**                  | **Description**                                                                                     |
|--------------------------------|-----------------------------------------------------------------------------------------------------|
| `--s_file`, `--s_folder`, `--s_zip`, `--dest`, `--zoom` and the others | `las2tiles.sh`: the same as in `tif2tiles.sh`, see Parameters. Not `--b` and `--norgbify`, a point cloud gives heights. |
| `--mount`, `--filename`        | `las2tif.sh`: the folder of the point cloud and its file, `.las` or `.laz`. `[--out]` is the GeoTIFF, `<folder>/<name>.tif` by default. |
| `--folder`                     | `las2tif_folder.sh`: the folder of the point clouds, a point cloud that fails is reported and the others go on. |
| `--resolution`                 | Pixel size of the DEM in the units of the point cloud, meters as a rule. About the spacing of the points, 0.25 - 1 m for airborne lidar: a finer one has no more detail, only the interpolation between the points. |
| `[--type]`                     | `dsm` (default): the surface with the buildings and the trees, the highest points. `dtm`: the ground, only the points classified as ground (class 2). `pc2dem.py` does not classify the points, the public lidar files have the classes as a rule. |

- The GeoTIFF of `las2tif.sh` is next to the point cloud with its name: `LAS_17758875.las` → `LAS_17758875.tif`. The gaps between the points are filled by the interpolation. OpenDroneMap writes `dsm.tif` or `dtm.tif` in the folder of the point cloud first, so `las2tif.sh` stops when there is such a file.
- Check the CRS of a GeoTIFF by `gdalinfo`: when it has none, give the EPSG code of the point cloud by `--s_epsg` (of `las2tiles.sh` or `tif2tiles.sh`). The heights and the resolution are in the units of the point cloud: a point cloud in feet, as some of the USA, gives the heights in feet.
- `./las2tiles.sh --help`, `./las2tif.sh --help` and `./las2tif_folder.sh --help` print the options.

---

## License

[MIT](LICENSE). `lodepng.cpp` and `lodepng.h` are [LodePNG](https://github.com/lvandeve/lodepng) under the zlib license.
