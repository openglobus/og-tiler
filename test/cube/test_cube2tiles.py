"""
Tests of cube2tiles.py and mergeTiles, in the og-tiler image (make test):
    docker run --rm -v $PWD:/repo -w /repo og-tiler python3 test/cube/test_cube2tiles.py
"""

import json
import os
import sys
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", ".."))

import cube2tiles as ct  # noqa: E402

with open(os.path.join(HERE, "cube_ref.json")) as f:
    REF = json.load(f)


def lon_diff(a, b):
    return np.abs((np.asarray(a) - np.asarray(b) + 180.0) % 360.0 - 180.0)


def test_to_lonlat_matches_openglobus():
    for name, face, a, b, lon, lat in REF["toLonLat"]:
        rlon, rlat = ct.face_to_lonlat(face, np.array([a]), np.array([b]), name == "equiangular")
        assert lon_diff(rlon[0], lon) < 1e-9, (name, face, a, b)
        assert abs(rlat[0] - lat) < 1e-9, (name, face, a, b)


def test_from_lonlat_matches_openglobus():
    for name, lon, lat, nlon, nlat in REF["fromLonLat"]:
        face, a, b = ct.lonlat_to_face(np.array([lon]), np.array([lat]), name == "equiangular")
        assert abs(face[0] + a[0] - nlon) < 1e-9, (name, lon, lat)
        assert abs(b[0] - nlat) < 1e-9, (name, lon, lat)


def test_round_trip():
    rng = np.random.default_rng(1)
    for eq in (False, True):
        for face in range(6):
            a = rng.uniform(0.001, 0.999, 1000)
            b = rng.uniform(0.001, 0.999, 1000)
            lon, lat = ct.face_to_lonlat(face, a, b, eq)
            f2, a2, b2 = ct.lonlat_to_face(lon, lat, eq)
            assert np.all(f2 == face)
            assert np.max(np.abs(a2 - a)) < 1e-9
            assert np.max(np.abs(b2 - b)) < 1e-9


# Gnomonic faces as PROJ CRS, the plane of a face is from -1 to 1
GNOMONIC_FACES = [
    "+proj=gnom +lat_0=90 +lon_0=-90 +R=1",
    "+proj=gnom +lat_0=0 +lon_0=0 +R=1",
    "+proj=gnom +lat_0=0 +lon_0=90 +R=1",
    "+proj=gnom +lat_0=0 +lon_0=180 +R=1",
    "+proj=gnom +lat_0=0 +lon_0=-90 +R=1",
    "+proj=gnom +lat_0=-90 +lon_0=-90 +R=1",
]


def test_gnomonic_faces_are_proj_gnom():
    from osgeo import osr

    geog = osr.SpatialReference()
    geog.ImportFromProj4("+proj=longlat +R=1 +no_defs")
    geog.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    rng = np.random.default_rng(2)
    for face, proj in enumerate(GNOMONIC_FACES):
        srs = osr.SpatialReference()
        srs.ImportFromProj4(proj)
        ct_ = osr.CoordinateTransformation(geog, srs)
        a = rng.uniform(0.01, 0.99, 50)
        b = rng.uniform(0.01, 0.99, 50)
        lon, lat = ct.face_to_lonlat(face, a, b, False)
        for i in range(a.size):
            x, y, _ = ct_.TransformPoint(float(lon[i]), float(lat[i]))
            assert abs(x - (2 * a[i] - 1)) < 1e-9, (face, a[i], b[i], x)
            assert abs(y - (2 * b[i] - 1)) < 1e-9, (face, a[i], b[i], y)


def make_source(path, width=720, height=360, crs="+proj=longlat +R=1737400 +no_defs"):
    """A global source with the height of the longitude and latitude: h = 10 * lon + lat."""
    import rasterio
    from rasterio.transform import from_bounds

    lon = -180 + (np.arange(width) + 0.5) * 360.0 / width
    lat = 90 - (np.arange(height) + 0.5) * 180.0 / height
    h = (10.0 * lon[None, :] + lat[:, None]).astype(np.float32)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=width,
        height=height,
        count=1,
        dtype="float32",
        crs=crs,
        transform=from_bounds(-180, -90, 180, 90, width, height),
        nodata=-32768,
    ) as d:
        d.write(h, 1)


def read_rgba(path):
    """RGBA pixels of a tile of any color type, lodepng of mergeTiles writes the smallest one of png."""
    from PIL import Image

    return np.asarray(Image.open(path).convert("RGBA"))


def write_rgba(path, pixels):
    """A tile of one row of RGBA pixels, webp lossless with the rgb under alpha 0."""
    from PIL import Image

    image = Image.fromarray(np.array([pixels], dtype=np.uint8), "RGBA")
    if path.endswith(".webp"):
        image.save(path, "WEBP", lossless=True, exact=True)
    else:
        image.save(path, "PNG")


def decode_rgb(path, min_height=-10000.0, resolution=0.1):
    im = read_rgba(path).astype(np.int64)
    code = im[..., 0] * 65536 + im[..., 1] * 256 + im[..., 2]
    return min_height + resolution * code, im[..., 3]


def test_tiles_have_the_heights_of_their_points():
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "src.tif")
        make_source(src)
        for cube, size in (("gnomonic", 256), ("equiangular", 256), ("gnomonic", 128), ("equiangular", 512)):
            dest = os.path.join(tmp, f"{cube}{size}")
            ct.main([src, dest, "--zoom", "0-2", "--cube", cube, "--tile-size", str(size), "--resampling", "near",
                     "--processes", "1"])
            for face in range(6):
                for z, x, y in ((0, 0, 0), (2, 1, 2), (2, 3, 0)):
                    h, alpha = decode_rgb(os.path.join(dest, str(face), str(z), str(x), f"{y}.webp"))
                    assert h.shape == (size, size), h.shape
                    assert np.all(alpha == 255)
                    a, b = ct.tile_positions(x, y, z, size)
                    lon, lat = ct.face_to_lonlat(face, a, b, cube == "equiangular")
                    # Nearest pixel of the half degree source, away from the antimeridian where h jumps
                    far = np.abs(lon) < 179
                    expected = 10.0 * lon + lat
                    err = np.abs(h - expected)[far]
                    # A half degree pixel is up to 2.5 m + 0.25 m off, and the 0.1 m encoding step
                    assert np.max(err) < 2.9, (cube, face, z, x, y, np.max(err))


def test_projected_source_is_the_same_as_geographic():
    with tempfile.TemporaryDirectory() as tmp:
        geo = os.path.join(tmp, "geo.tif")
        make_source(geo)
        # The same pixels in the simple cylindrical meters of the Moon sphere, as the USGS files
        eqc = os.path.join(tmp, "eqc.tif")
        import rasterio
        from rasterio.transform import from_bounds

        r = 1737400 * np.pi
        with rasterio.open(geo) as g:
            data = g.read(1)
            with rasterio.open(
                eqc,
                "w",
                driver="GTiff",
                width=g.width,
                height=g.height,
                count=1,
                dtype="float32",
                crs="+proj=eqc +lat_ts=0 +lat_0=0 +lon_0=0 +x_0=0 +y_0=0 +R=1737400 +units=m +no_defs",
                transform=from_bounds(-r, -r / 2, r, r / 2, g.width, g.height),
                nodata=-32768,
            ) as d:
                d.write(data, 1)
        for name in ("geo", "eqc"):
            ct.main([os.path.join(tmp, f"{name}.tif"), os.path.join(tmp, name), "--zoom", "0-1", "-p", "1"])
        for face in range(6):
            for z, x, y in ((0, 0, 0), (1, 1, 0)):
                p = os.path.join(str(face), str(z), str(x), f"{y}.webp")
                hg, _ = decode_rgb(os.path.join(tmp, "geo", p))
                he, _ = decode_rgb(os.path.join(tmp, "eqc", p))
                assert np.max(np.abs(hg - he)) < 0.11, (face, z, x, y)


def test_projected_source_east_of_180():
    """
    The simple cylindrical meters of a source from 180 to 270 east, as the WAC_EMP files of the Moon: the longitudes
    of the cube from -180 to 180 are taken on its side of 180.
    """
    import rasterio
    from rasterio.transform import from_bounds

    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "east.tif")
        width, height = 180, 240
        lon = 180 + (np.arange(width) + 0.5) * 90.0 / width
        lat = 60 - (np.arange(height) + 0.5) * 120.0 / height
        h = (10.0 * (lon[None, :] - 180.0) + lat[:, None]).astype(np.float32)
        m = 1737400 * np.pi / 180.0
        with rasterio.open(
            src,
            "w",
            driver="GTiff",
            width=width,
            height=height,
            count=1,
            dtype="float32",
            crs="+proj=eqc +lat_ts=0 +lat_0=0 +lon_0=0 +x_0=0 +y_0=0 +R=1737400 +units=m +no_defs",
            transform=from_bounds(180 * m, -60 * m, 270 * m, 60 * m, width, height),
            nodata=-32768,
        ) as d:
            d.write(h, 1)
        dest = os.path.join(tmp, "out")
        ct.main([src, dest, "--zoom", "0-2", "--cube", "equiangular", "--resampling", "near", "-p", "1"])

        # 180..270 east is the east half of the face 3 and the west half of the face 4, and the corners of the poles
        assert sorted(os.listdir(dest)) == ["0", "3", "4", "5"], os.listdir(dest)
        _, alpha = decode_rgb(os.path.join(dest, "4", "0", "0", "0.webp"))
        assert 0.4 < np.mean(alpha == 255) < 0.6, np.mean(alpha == 255)

        tiles = 0
        for face in os.listdir(dest):
            for z in os.listdir(os.path.join(dest, face)):
                for x in os.listdir(os.path.join(dest, face, z)):
                    for name in os.listdir(os.path.join(dest, face, z, x)):
                        y = int(name.split(".")[0])
                        hv, alpha = decode_rgb(os.path.join(dest, face, z, x, name))
                        a, b = ct.tile_positions(int(x), y, int(z), hv.shape[0])
                        tlon, tlat = ct.face_to_lonlat(int(face), a, b, True)
                        expected = 10.0 * (np.mod(tlon, 360.0) - 180.0) + tlat
                        ok = alpha == 255
                        assert np.any(ok), (face, z, x, y)
                        # A half degree pixel is up to 2.5 m + 0.25 m off, and the 0.1 m encoding step
                        err = np.abs(hv - expected)[ok]
                        assert np.max(err) < 2.9, (face, z, x, y, np.max(err))
                        tiles += 1
        assert tiles > 10, tiles


def test_scaled_source_is_in_meters():
    import rasterio

    with tempfile.TemporaryDirectory() as tmp:
        meters = os.path.join(tmp, "meters.tif")
        make_source(meters)
        # The same heights in half meters with the scale 0.5, as the USGS LOLA files
        half = os.path.join(tmp, "half.tif")
        with rasterio.open(meters) as m:
            profile = m.profile
            data = m.read(1)
        profile.update(dtype="int16")
        with rasterio.open(half, "w", **profile) as d:
            d.write(np.round(data * 2).astype(np.int16), 1)
            d.scales = (0.5,)
            d.offsets = (0.0,)
        for name in ("meters", "half"):
            ct.main([os.path.join(tmp, f"{name}.tif"), os.path.join(tmp, name), "--zoom", "0", "-p", "1"])
        for face in range(6):
            p = os.path.join(str(face), "0", "0", "0.webp")
            hm, _ = decode_rgb(os.path.join(tmp, "meters", p))
            hh, _ = decode_rgb(os.path.join(tmp, "half", p))
            assert np.max(np.abs(hm - hh)) < 0.6, face


def test_local_source_makes_only_its_tiles():
    import rasterio
    from rasterio.transform import from_bounds

    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "local.tif")
        # A degree around lon 10, lat 20, on the front face
        with rasterio.open(
            src,
            "w",
            driver="GTiff",
            width=200,
            height=200,
            count=1,
            dtype="float32",
            crs="EPSG:4326",
            transform=from_bounds(10, 20, 11, 21, 200, 200),
            nodata=-32768,
        ) as d:
            d.write(np.full((200, 200), 1234.5, dtype=np.float32), 1)
        dest = os.path.join(tmp, "out")
        ct.main([src, dest, "--zoom", "0-8", "-p", "1"])
        faces = sorted(os.listdir(dest))
        assert faces == ["1"]
        for z in range(0, 9):
            tiles = os.listdir(os.path.join(dest, "1", str(z)))
            assert 1 <= len(tiles) <= 4, (z, tiles)
        h, alpha = decode_rgb(os.path.join(dest, "1", "0", "0", "0.webp"))
        assert np.all(np.abs(h[alpha == 255] - 1234.5) < 0.06)
        assert np.any(alpha == 0)


def test_utm_source():
    """A transverse Mercator has no far side of the planet, its tiles are only the ones around the source."""
    import rasterio
    from rasterio.transform import from_bounds

    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "utm.tif")
        # 10 km square of UTM 33N, near lon 15, lat 40.6
        with rasterio.open(
            src,
            "w",
            driver="GTiff",
            width=200,
            height=200,
            count=1,
            dtype="float32",
            crs="EPSG:32633",
            transform=from_bounds(500000, 4490000, 510000, 4500000, 200, 200),
            nodata=-32768,
        ) as d:
            d.write(np.full((200, 200), 1234.0, dtype=np.float32), 1)
        dest = os.path.join(tmp, "out")
        ct.main([src, dest, "--zoom", "0-10", "--cube", "equiangular", "-p", "1"])
        assert sorted(os.listdir(dest)) == ["1"]
        for z in range(0, 11):
            tiles = [os.path.join(r, f) for r, _, fs in os.walk(os.path.join(dest, "1", str(z))) for f in fs]
            # 10 km is about a tile of zoom 10, it can touch 3 x 3 of them
            assert 1 <= len(tiles) <= 9, (z, len(tiles))
            for t in tiles:
                h, alpha = decode_rgb(t)
                assert np.any(alpha == 255), t
                assert np.all(np.abs(h[alpha == 255] - 1234.0) < 0.06), t


def test_knots_of_every_tile_size():
    """The source pixels of a projected source interpolated between the knots are the exact ones, every tile size."""
    import rasterio
    from rasterio.transform import from_bounds

    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "utm.tif")
        with rasterio.open(
            src,
            "w",
            driver="GTiff",
            width=200,
            height=200,
            count=1,
            dtype="float32",
            crs="EPSG:32633",
            transform=from_bounds(500000, 4490000, 510000, 4500000, 200, 200),
        ) as d:
            d.write(np.zeros((200, 200), dtype=np.float32), 1)
        for size in ct.TILE_SIZES:
            tiler = ct.Tiler(ct.parse_args([src, tmp, "--cube", "equiangular", "--tile-size", str(size)]))
            # The tile of zoom 10 at the source center, lon 15.06, lat 40.6
            z = 10
            face, a, b = ct.lonlat_to_face(15.06, 40.6, True)
            x, y = int(a * (1 << z)), int((1.0 - b) * (1 << z))
            a, b = ct.tile_positions(x, y, z, size)
            approx = tiler.approx_pixels(int(face), a, b)
            assert approx is not None, size
            cols, rows = tiler.source.to_pixels(*ct.face_to_lonlat(int(face), a, b, True))
            assert approx[0].shape == (size, size)
            error = max(np.max(np.abs(approx[0] - cols)), np.max(np.abs(approx[1] - rows)))
            assert error < ct.APPROX_MAX_ERROR, (size, error)


def stripes_source(path, width=2048, height=1024):
    """A global source of stripes along the longitude, 10 degrees: h = 1000 * sin(36 * lon), with overviews."""
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.transform import from_bounds

    lon = -180 + (np.arange(width) + 0.5) * 360.0 / width
    h = np.repeat((1000.0 * np.sin(np.radians(lon) * 36.0))[None, :], height, axis=0).astype(np.float32)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=width,
        height=height,
        count=1,
        dtype="float32",
        crs="+proj=longlat +R=1737400 +no_defs",
        transform=from_bounds(-180, -90, 180, 90, width, height),
        tiled=True,
    ) as d:
        d.write(h, 1)
        d.build_overviews([2, 4, 8, 16], Resampling.average)


# Errors of the stripes at the pole: the area average gives 1.9 and 128, one read resolution for the whole tile with
# the bilinear sample gave 2.8 and 1084 (noise and moire at the pole)
MEAN_POLE_ERROR = 2.5
MAX_POLE_ERROR = 400


def test_pole_is_the_area_average():
    """A tile pixel at the pole takes all the longitudes under it, the stripes along the longitude average out there."""
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "stripes.tif")
        stripes_source(src)
        for cube in ("gnomonic", "equiangular"):
            tiler = ct.Tiler(ct.parse_args([src, tmp, "--zoom", "3", "--cube", cube]))
            # The pole is the lower right corner of the tile
            face, x, y, z = 0, 3, 3, 3
            values, ok = tiler.render(face, x, y, z)
            # The average of every tile pixel area, 16 x 16 points of a pixel
            n, k = ct.TILE_SIZE, 16
            s = (np.arange(n * k) + 0.5) / (n * k)
            a, b = np.meshgrid((x + s) / (1 << z), 1.0 - (y + s) / (1 << z))
            lon, _ = ct.face_to_lonlat(face, a, b, cube == "equiangular")
            truth = (1000.0 * np.sin(np.radians(lon) * 36.0)).reshape(n, k, n, k).mean(axis=(1, 3))
            err = np.abs(values[0] - truth)
            assert np.all(ok), cube
            assert err.mean() < MEAN_POLE_ERROR and err.max() < MAX_POLE_ERROR, (cube, err.mean(), err.max())


def test_global_source_takes_every_tile():
    """The tiles of a global source are all the tiles of the cube, and only the faces are probed."""
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "src.tif")
        make_source(src)
        tiler = ct.Tiler(ct.parse_args([src, tmp, "--zoom", "0-6"]))
        probes = []
        meets = tiler.tile_meets_source
        tiler.tile_meets_source = lambda *tile: probes.append(tile) or meets(*tile)
        tiles = tiler.find_tiles(0, 6)
        for z in range(7):
            every = [(f, x, y) for f in range(6) for x in range(1 << z) for y in range(1 << z)]
            assert sorted(tiles[z]) == every, z
        assert len(probes) == 6, len(probes)


def merge_folders_binary():
    for path in (os.environ.get("MERGE_TILES"), "/work/mergeTiles"):
        if path and os.path.exists(path):
            return path
    return None


def test_merge_updates_only_the_new_data():
    import subprocess

    import rasterio
    from rasterio.transform import from_bounds

    merge = merge_folders_binary()
    if merge is None:
        print("skip: no mergeTiles, set MERGE_TILES")
        return

    with tempfile.TemporaryDirectory() as tmp:
        base = os.path.join(tmp, "base.tif")
        make_source(base)
        # Local update, 5000 m and the gray 200 on the front face around lon 10, lat 20
        local = os.path.join(tmp, "local.tif")
        with rasterio.open(
            local,
            "w",
            driver="GTiff",
            width=400,
            height=400,
            count=1,
            dtype="float32",
            crs="+proj=longlat +R=1737400 +no_defs",
            transform=from_bounds(5, 15, 25, 35, 400, 400),
            nodata=-32768,
        ) as d:
            d.write(np.full((400, 400), 5000, dtype=np.float32), 1)

        cases = (
            ("rgb", "png", None, ["-10000", "0.1"]),
            ("image", "png", None, ["--alpha"]),
            ("rgb", "webp", None, ["-10000", "0.1"]),
            ("image", "webp", None, ["--alpha"]),
            ("image", "webp", 90, ["--alpha", "--quality", "90"]),
        )
        for encoding, fmt, quality, extra in cases:
            case = f"{encoding} {fmt} {quality}"
            dest = os.path.join(tmp, f"dest_{encoding}_{fmt}_{quality}")
            update = os.path.join(tmp, f"update_{encoding}_{fmt}_{quality}")
            opts = ["--zoom", "0-3", "--encoding", encoding, "-p", "1", "--resampling", "near", "--format", fmt]
            if quality:
                opts += ["--quality", str(quality)]
            if encoding == "image":
                opts += ["--scale", "0", "25.5"]  # the image is h / 10 of the base, 200 of the update
            ct.main([base, dest] + opts)
            ct.main([local, update] + opts)

            path = os.path.join("1", "3", "4", f"2.{fmt}")
            before = read_rgba(os.path.join(dest, path))
            new = read_rgba(os.path.join(update, path))
            # no data of the update keeps rgb 0 under alpha 0 when lossless
            if not quality:
                assert np.all(new[new[..., 3] == 0][:, :3] == 0), case
            subprocess.run([merge, update, dest] + extra, check=True, stdout=subprocess.DEVNULL)
            after = read_rgba(os.path.join(dest, path))

            changed = new[..., 3] > 0
            assert np.any(changed) and np.any(~changed), case
            assert np.array_equal(after[..., 3], np.maximum(new[..., 3], before[..., 3])), case
            if quality:
                # lossy imagery: the tile is encoded again
                diff = np.abs(after.astype(int) - np.where(changed[..., None], new, before).astype(int))
                assert diff[..., :3].mean() < 3, (case, diff.mean())
            else:
                assert np.array_equal(after[changed], new[changed]), case
                assert np.array_equal(after[~changed], before[~changed]), case
            # The tiles of the update only are moved, the others are kept
            assert os.path.exists(os.path.join(dest, "0", "0", "0", f"0.{fmt}")), case


def test_webp_heights_are_the_png_ones():
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "src.tif")
        make_source(src)
        for fmt in ("png", "webp"):
            ct.main([src, os.path.join(tmp, fmt), "--zoom", "0-2", "-p", "1", "--format", fmt])
        for face in range(6):
            for z, x, y in ((0, 0, 0), (2, 1, 2), (2, 3, 0)):
                t = os.path.join(str(face), str(z), str(x), str(y))
                hp, ap = decode_rgb(os.path.join(tmp, "png", t + ".png"))
                hw, aw = decode_rgb(os.path.join(tmp, "webp", t + ".webp"))
                assert np.array_equal(hp, hw) and np.array_equal(ap, aw), t


def test_merge_pixel_rules():
    """Which pixel stays when a new tile is merged into an old one, pixel by pixel."""
    import subprocess

    merge = merge_folders_binary()
    if merge is None:
        print("skip: no mergeTiles, set MERGE_TILES")
        return

    def code(h):
        c = int(round((h + 10000) / 0.1))
        return [c >> 16, (c >> 8) & 255, c & 255]

    old = [*code(500.0), 255]
    # The new pixels and whether they replace the old one: (heights, imagery)
    cases = [
        ([*code(1234.5), 255], True, True),  # data
        ([7, 8, 9, 0], False, False),  # transparent, any rgb under it
        ([0, 0, 0, 255], False, True),  # rgb 0: no data of the heights, black of the imagery
        ([*code(0.0), 255], False, True),  # the height 0
        ([*code(0.1), 255], True, True),  # a height near 0
        ([*code(-9999.9), 255], True, True),  # a height near minHeight
    ]
    for fmt in ("png", "webp"):
        for alpha in (False, True):
            with tempfile.TemporaryDirectory() as tmp:
                for name, pixels in (("old", [old] * len(cases)), ("new", [c[0] for c in cases])):
                    os.makedirs(os.path.join(tmp, name, "0"))
                    write_rgba(os.path.join(tmp, name, "0", f"0.{fmt}"), pixels)
                args = [merge, os.path.join(tmp, "new"), os.path.join(tmp, "old")] + (["--alpha"] if alpha else [])
                subprocess.run(args, check=True, stdout=subprocess.DEVNULL)
                after = read_rgba(os.path.join(tmp, "old", "0", f"0.{fmt}"))
                for i, (pixel, by_height, by_alpha) in enumerate(cases):
                    replaced = by_alpha if alpha else by_height
                    expected = pixel if replaced else old
                    assert after[0, i].tolist() == expected, (fmt, alpha, i, after[0, i].tolist(), expected)


def test_merge_threads_give_the_same_tiles():
    import filecmp
    import shutil
    import subprocess

    merge = merge_folders_binary()
    if merge is None:
        print("skip: no mergeTiles, set MERGE_TILES")
        return

    with tempfile.TemporaryDirectory() as tmp:
        base = os.path.join(tmp, "base.tif")
        make_source(base)
        # An update over the whole planet, so every tile of zoom 0-3 is merged
        update_src = os.path.join(tmp, "update.tif")
        make_source(update_src, width=360, height=180)
        ct.main([base, os.path.join(tmp, "dest"), "--zoom", "0-3", "-p", "2"])
        ct.main([update_src, os.path.join(tmp, "update"), "--zoom", "0-3", "-p", "2", "--resampling", "near"])
        results = []
        for threads in ("1", "8"):
            dest = os.path.join(tmp, f"dest_{threads}")
            update = os.path.join(tmp, f"update_{threads}")
            shutil.copytree(os.path.join(tmp, "dest"), dest)
            shutil.copytree(os.path.join(tmp, "update"), update)
            subprocess.run([merge, update, dest, "--threads", threads], check=True, stdout=subprocess.DEVNULL)
            files = sorted(os.path.relpath(os.path.join(r, f), dest) for r, _, fs in os.walk(dest) for f in fs)
            assert files and not [f for f in files if f.endswith(".tmp")], threads
            results.append((dest, files))
        (d1, f1), (d8, f8) = results
        assert f1 == f8
        _, mismatch, errors = filecmp.cmpfiles(d1, d8, f1, shallow=False)
        assert not mismatch and not errors, (mismatch[:5], errors[:5])


def test_merge_refuses_the_other_format():
    import subprocess

    merge = merge_folders_binary()
    if merge is None:
        print("skip: no mergeTiles, set MERGE_TILES")
        return

    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "src.tif")
        make_source(src)
        ct.main([src, os.path.join(tmp, "png"), "--zoom", "0-1", "-p", "1", "--format", "png"])
        ct.main([src, os.path.join(tmp, "webp"), "--zoom", "0-2", "-p", "1", "--format", "webp"])
        r = subprocess.run([merge, os.path.join(tmp, "webp"), os.path.join(tmp, "png")], capture_output=True, text=True)
        assert r.returncode != 0 and "format" in r.stdout, r.stdout
        # Nothing is moved
        assert not os.path.exists(os.path.join(tmp, "png", "0", "2"))
        assert os.path.exists(os.path.join(tmp, "webp", "0", "2"))


if __name__ == "__main__":
    failed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("ok  ", name)
            except Exception as e:  # noqa: BLE001
                failed += 1
                print("FAIL", name, repr(e))
    sys.exit(1 if failed else 0)
