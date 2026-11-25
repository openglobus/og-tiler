#!/usr/bin/env python3
# Copyright (c) 2026 Michael Gevlich, MIT License
"""
Cuts a GeoTIFF into the OpenGlobus tiles of CubeQuadTreeStrategy (gnomonic) and EquiangularCubeQuadTreeStrategy
(equiangular) of any planet: heights for RgbTerrain or imagery. tif2tiles.sh --strategy cube | equiangular runs it and
merges the tiles into the tile set.
"""

import argparse
import math
import multiprocessing as mp
import os
import sys
import time

import numpy as np
import rasterio
from osgeo import gdal, osr
from PIL import Image
from rasterio.crs import CRS
from rasterio.enums import Resampling
from rasterio.transform import from_bounds
from rasterio.windows import Window

gdal.PushErrorHandler("CPLQuietErrorHandler")

# Tile side in pixels by default, and the ones of --tile-size
TILE_SIZE = 256
TILE_SIZES = (128, 256, 512)

WEBP_LOSSLESS_QUALITY = 25
WEBP_LOSSLESS_METHOD = 1

WORKER_GDAL_CACHE = 256 * 1024 * 1024

# Center, east and north directions of the faces, CUBE_FACES of openglobus/src/proj/cube.ts
CUBE_FACES = np.array(
    [
        [0, 0, 1, 1, 0, 0, 0, 1, 0],  # 0 top
        [1, 0, 0, 0, 1, 0, 0, 0, 1],  # 1 front, lon 0
        [0, 1, 0, -1, 0, 0, 0, 0, 1],  # 2 right, lon 90
        [-1, 0, 0, 0, -1, 0, 0, 0, 1],  # 3 back, lon 180
        [0, -1, 0, 1, 0, 0, 0, 0, 1],  # 4 left, lon -90
        [0, 0, -1, 1, 0, 0, 0, -1, 0],  # 5 bottom
    ],
    dtype=np.float64,
)

MAX_POSITION = 1.0 - 2.0**-50

# Samples of a tile side to find out if it meets the source
PROBE_SAMPLES = 17

MAX_WINDOW_PIXELS = 4096 * 4096


# -----------------------------------------------------------------------------
# Cube geometry
# -----------------------------------------------------------------------------


def to_plane(position, equiangular):
    if equiangular:
        return np.tan(0.5 * np.pi * (position - 0.5))
    return 2.0 * position - 1.0


def from_plane(coordinate, equiangular):
    if equiangular:
        return np.arctan(coordinate) / (0.5 * np.pi) + 0.5
    return (coordinate + 1.0) * 0.5


def face_to_lonlat(face, a, b, equiangular):
    f = CUBE_FACES[face]
    pa = to_plane(a, equiangular)
    pb = to_plane(b, equiangular)
    x = f[0] + pa * f[3] + pb * f[6]
    y = f[1] + pa * f[4] + pb * f[7]
    z = f[2] + pa * f[5] + pb * f[8]
    return np.degrees(np.arctan2(y, x)), np.degrees(np.arctan2(z, np.hypot(x, y)))


def lonlat_to_face(lon, lat, equiangular):
    lon = np.radians(np.asarray(lon, dtype=np.float64))
    lat = np.radians(np.asarray(lat, dtype=np.float64))
    c = np.cos(lat)
    x, y, z = c * np.cos(lon), c * np.sin(lon), np.sin(lat)
    ax, ay, az = np.abs(x), np.abs(y), np.abs(z)

    face = np.where(
        (az >= ax) & (az >= ay),
        np.where(z > 0, 0, 5),
        np.where(ax >= ay, np.where(x > 0, 1, 3), np.where(y > 0, 2, 4)),
    )

    f = CUBE_FACES[face]
    d = x * f[..., 0] + y * f[..., 1] + z * f[..., 2]
    a = (x * f[..., 3] + y * f[..., 4] + z * f[..., 5]) / d
    b = (x * f[..., 6] + y * f[..., 7] + z * f[..., 8]) / d

    return face, np.minimum(from_plane(a, equiangular), MAX_POSITION), from_plane(b, equiangular)


def tile_positions(x, y, z, n):
    s = (np.arange(n, dtype=np.float64) + 0.5) / n
    pz = float(1 << z)
    a = (x + s) / pz
    b = 1.0 - (y + s) / pz
    return np.meshgrid(a, b)


# Pixels of a projected source (not the equirectangular one) go through PROJ only in the knots of a grid every 16
# pixels, the others are interpolated, as gdalwarp does. If the interpolation in the middles of the cells is off by
# more than 1/8 of a source pixel, as on the low zooms, every pixel goes through PROJ.
APPROX_STEP = 16
APPROX_MAX_ERROR = 0.125


def approx_grid(n):
    knots = np.append(np.arange(0, n, APPROX_STEP), n - 1)
    checks = (knots[:-1] + knots[1:]) // 2
    cell = np.minimum(np.searchsorted(knots, np.arange(n), side="right") - 1, len(knots) - 2)
    frac = (np.arange(n) - knots[cell]) / (knots[cell + 1] - knots[cell])
    return knots, checks, cell, frac


def interpolate_knots(grid, cell, frac):
    """Bilinear interpolation."""
    r = grid[cell] * (1.0 - frac)[:, None] + grid[cell + 1] * frac[:, None]
    return r[:, cell] * (1.0 - frac) + r[:, cell + 1] * frac


def tile_probe_positions(x, y, z, n=PROBE_SAMPLES):
    s = np.linspace(0.0, 1.0, n)
    pz = float(1 << z)
    return np.meshgrid((x + s) / pz, 1.0 - (y + s) / pz)


# -----------------------------------------------------------------------------
# Source
# -----------------------------------------------------------------------------


def transform_points(ct, xs, ys):
    xs = np.asarray(xs, dtype=np.float64)
    ys = np.asarray(ys, dtype=np.float64)
    if xs.size == 0:
        return xs.copy(), ys.copy()
    r = np.array(ct.TransformPoints(np.column_stack([xs, ys]).tolist()), dtype=np.float64)
    x, y = r[:, 0], r[:, 1]
    bad = ~(np.isfinite(x) & np.isfinite(y))
    x[bad] = np.nan
    y[bad] = np.nan
    return x, y


def equirectangular_xy(p, lon, lat, west=-180.0):
    a, lon0, lat_ts, lat0, fe, fn = p
    dlon = west + np.mod(np.asarray(lon, dtype=np.float64) - lon0 - west, 360.0)
    x = fe + a * np.cos(np.radians(lat_ts)) * np.radians(dlon)
    y = fn + a * np.radians(np.asarray(lat, dtype=np.float64) - lat0)
    return x, y


class Source:

    def __init__(self, path, s_srs=None, bounds=None, unscale=True):
        self.path = path
        self.ds = rasterio.open(path)
        self.width = self.ds.width
        self.height = self.ds.height

        if bounds is not None:
            left, top, right, bottom = bounds
            self.transform = from_bounds(left, bottom, right, top, self.width, self.height)
            crs = CRS.from_user_input(s_srs) if s_srs else CRS.from_proj4("+proj=longlat +R=1 +no_defs")
        else:
            self.transform = self.ds.transform
            crs = CRS.from_user_input(s_srs) if s_srs else self.ds.crs
            if crs is None:
                raise SystemExit(f"{path} has no CRS, set --s_srs or --bounds")

        self.crs = crs
        self.inverse = ~self.transform

        # The geographic CRS of the source itself, so the longitudes and latitudes of the cube are not shifted to
        # another datum
        self.projected = not crs.is_geographic
        self.eqc = None
        if self.projected:
            srs = osr.SpatialReference()
            srs.ImportFromWkt(crs.to_wkt())
            srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
            geog = srs.CloneGeogCS()
            geog.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
            self.to_proj = osr.CoordinateTransformation(geog, srs)
            self.to_geog = osr.CoordinateTransformation(srs, geog)
            self.eqc = self.equirectangular(srs)

        self.eqc_west = -180.0
        if self.eqc is not None and self.transform.a > 0:
            a, _, lat_ts, _, fe, _ = self.eqc
            self.eqc_west = float(np.degrees((self.transform.c - fe) / (a * np.cos(np.radians(lat_ts)))))

        self.bands = list(range(1, self.ds.count + 1))
        self.nodata = self.ds.nodata

        self.scales = np.array([self.ds.scales[b - 1] for b in self.bands], dtype=np.float32)
        self.offsets = np.array([self.ds.offsets[b - 1] for b in self.bands], dtype=np.float32)
        self.unscale = unscale and bool(np.any(self.scales != 1) or np.any(self.offsets != 0))

        c0, _ = self.to_pixels(np.array([0.0]), np.array([0.0]))
        c1, _ = self.to_pixels(np.array([1.0]), np.array([0.0]))
        period = abs(c1[0] - c0[0]) * 360.0
        self.wrap = bool(np.isfinite(period) and abs(period - self.width) < 1.5)

    def equirectangular(self, srs):
        if (srs.GetAttrValue("PROJECTION") or "").lower() != "equirectangular":
            return None
        p = (
            srs.GetSemiMajor(),
            srs.GetProjParm(osr.SRS_PP_CENTRAL_MERIDIAN, 0.0),
            srs.GetProjParm(osr.SRS_PP_STANDARD_PARALLEL_1, 0.0),
            srs.GetProjParm(osr.SRS_PP_LATITUDE_OF_ORIGIN, 0.0),
            srs.GetProjParm(osr.SRS_PP_FALSE_EASTING, 0.0),
            srs.GetProjParm(osr.SRS_PP_FALSE_NORTHING, 0.0),
        )
        rng = np.random.default_rng(0)
        lon = rng.uniform(-180.0, 180.0, 1000)
        lat = rng.uniform(-89.0, 89.0, 1000)
        x, y = equirectangular_xy(p, lon, lat)
        ex, ey = transform_points(self.to_proj, lon, lat)
        if not (np.all(np.isfinite(ex)) and np.max(np.abs(x - ex)) < 1e-3 and np.max(np.abs(y - ey)) < 1e-3):
            return None
        return p

    def to_pixels(self, lon, lat):
        shape = np.shape(lon)
        lon = np.ravel(lon)
        lat = np.ravel(lat)
        if self.projected and self.eqc is not None:
            xs, ys = equirectangular_xy(self.eqc, lon, lat, self.eqc_west)
        elif not self.projected:
            west = self.transform.c
            if self.transform.a > 0:
                lon = west + np.mod(lon - west, 360.0)
            xs, ys = lon, lat
        else:
            xs, ys = transform_points(self.to_proj, lon, lat)
            # Only the points that come back, the far side of the planet gets wrong coordinates in a transverse Mercator
            ok = np.isfinite(xs) & np.isfinite(ys)
            blon, blat = transform_points(self.to_geog, xs[ok], ys[ok])
            dlon = np.abs((blon - lon[ok] + 180.0) % 360.0 - 180.0) * np.cos(np.radians(lat[ok]))
            dlat = np.abs(blat - lat[ok])
            back = np.zeros_like(ok)
            back[ok] = (dlon < 1e-5) & (dlat < 1e-5)
            xs = np.where(back, xs, np.nan)
            ys = np.where(back, ys, np.nan)
        cols, rows = self.inverse * (xs, ys)
        return np.reshape(cols, shape), np.reshape(rows, shape)

    def footprint_lonlat(self, n=64):
        g = np.linspace(0.0, 1.0, n)
        e = np.linspace(0.0, 1.0, 8 * n)
        u = np.concatenate([np.repeat(g, n), e, e, np.zeros_like(e), np.ones_like(e)])
        v = np.concatenate([np.tile(g, n), np.zeros_like(e), np.ones_like(e), e, e])
        cols = u * self.width
        rows = v * self.height
        xs, ys = self.transform * (cols, rows)
        if not self.projected:
            return np.asarray(xs), np.asarray(ys)
        lon, lat = transform_points(self.to_geog, np.asarray(xs), np.asarray(ys))
        ok = np.isfinite(lon) & np.isfinite(lat)
        return lon[ok], lat[ok]

    def read(self, col0, col1, row0, row1, fx, fy, resampling):
        out_h = max(1, round((row1 - row0) / fy))
        sy = (row1 - row0) / out_h
        parts = []
        if self.wrap:
            c = col0
            while c < col1:
                start = c % self.width
                end = min(self.width, start + (col1 - c))
                parts.append((start, end))
                c += end - start
        else:
            parts.append((col0, col1))

        data_parts = []
        valid_parts = []
        for start, end in parts:
            out_w = max(1, round((end - start) / fx))
            window = Window(start, row0, end - start, row1 - row0)
            m = self.ds.read(
                self.bands,
                window=window,
                out_shape=(len(self.bands), out_h, out_w),
                resampling=resampling,
                masked=True,
            )
            d = np.ma.getdata(m).astype(np.float32)
            if self.unscale:
                d = d * self.scales[:, None, None] + self.offsets[:, None, None]
            data_parts.append(d)
            mask = np.ma.getmaskarray(m)
            valid_parts.append(~np.any(mask, axis=0))

        data = np.concatenate(data_parts, axis=2)
        sx = (col1 - col0) / data.shape[2]
        return data, np.concatenate(valid_parts, axis=1), sx, sy


# -----------------------------------------------------------------------------
# Sampling
# -----------------------------------------------------------------------------


def sample(data, valid, u, v, bilinear):
    _, h, w = data.shape

    if not bilinear:
        j = np.floor(u + 0.5).astype(np.int64)
        i = np.floor(v + 0.5).astype(np.int64)
        inside = (i >= 0) & (i < h) & (j >= 0) & (j < w)
        ic = np.clip(i, 0, h - 1)
        jc = np.clip(j, 0, w - 1)
        return data[:, ic, jc], inside & valid[ic, jc]

    j0 = np.floor(u).astype(np.int64)
    i0 = np.floor(v).astype(np.int64)
    fu = (u - j0).astype(np.float32)
    fv = (v - i0).astype(np.float32)

    values = np.zeros((data.shape[0], u.size), dtype=np.float32)
    weights = np.zeros(u.size, dtype=np.float32)

    for di, dj, wgt in (
        (0, 0, (1 - fu) * (1 - fv)),
        (0, 1, fu * (1 - fv)),
        (1, 0, (1 - fu) * fv),
        (1, 1, fu * fv),
    ):
        i = i0 + di
        j = j0 + dj
        inside = (i >= 0) & (i < h) & (j >= 0) & (j < w)
        ic = np.clip(i, 0, h - 1)
        jc = np.clip(j, 0, w - 1)
        wgt = np.where(inside & valid[ic, jc], wgt, 0.0).astype(np.float32)
        values += data[:, ic, jc] * wgt
        weights += wgt

    ok = weights > 0
    values[:, ok] /= weights[ok]
    return values, ok


def pixel_steps(a, axis, period=None):
    d = np.diff(a, axis=axis)
    if period:
        d = (d + period / 2.0) % period - period / 2.0
    d = np.abs(d)
    before = np.concatenate([np.take(d, [0], axis=axis), d], axis=axis)
    after = np.concatenate([d, np.take(d, [-1], axis=axis)], axis=axis)
    mean = (before + after) / 2.0
    return np.where(np.isfinite(mean), mean, np.fmax(before, after))


def area_average(data, valid, u, v, hu, hv):
    bands, h, w = data.shape
    values = np.zeros((bands, u.size), dtype=np.float32)
    r0 = max(0, int(math.floor(np.min(v - hv))))
    r1 = min(h, int(math.ceil(np.max(v + hv))))
    c0 = max(0, int(math.floor(np.min(u - hu))))
    c1 = min(w, int(math.ceil(np.max(u + hu))))
    if r1 <= r0 or c1 <= c0:
        return values, np.zeros(u.size, dtype=bool)

    weight = valid[r0:r1, c0:c1].astype(np.float64)
    h, w = weight.shape
    tables = np.zeros((bands + 1, h + 1, w + 1), dtype=np.float64)
    tables[:bands, 1:, 1:] = (data[:, r0:r1, c0:c1] * weight).cumsum(axis=1).cumsum(axis=2)
    tables[bands, 1:, 1:] = weight.cumsum(axis=0).cumsum(axis=1)
    flat = tables.reshape(bands + 1, -1)

    def integral(x, y):
        # The sum of the pixels from 0 to x, y: the bilinear interpolation of the table is exact for it
        x = np.clip(x - c0, 0.0, w)
        y = np.clip(y - r0, 0.0, h)
        j = np.minimum(x.astype(np.int64), w - 1)
        i = np.minimum(y.astype(np.int64), h - 1)
        fx = x - j
        fy = y - i
        k = i * (w + 1) + j
        return (
            flat[:, k] * ((1 - fx) * (1 - fy))
            + flat[:, k + 1] * (fx * (1 - fy))
            + flat[:, k + w + 1] * ((1 - fx) * fy)
            + flat[:, k + w + 2] * (fx * fy)
        )

    s = integral(u + hu, v + hv) - integral(u - hu, v + hv) - integral(u + hu, v - hv) + integral(u - hu, v - hv)
    ok = s[bands] > 1e-6
    values[:, ok] = s[:bands, ok] / s[bands, ok]
    return values, ok


def pow2_floor(value):
    if not np.isfinite(value) or value < 2.0:
        return 1
    return 1 << int(math.floor(math.log2(value)))


# -----------------------------------------------------------------------------
# Tiles
# -----------------------------------------------------------------------------


class Tiler:
    def __init__(self, opts):
        self.opts = opts
        self.equiangular = opts.cube == "equiangular"
        self.source = Source(opts.source, opts.s_srs, opts.bounds, unscale=opts.encoding == "rgb")
        self.bilinear = opts.resampling == "bilinear"
        self.read_resampling = Resampling.average if self.bilinear else Resampling.nearest
        self.n = opts.tile_size
        self.grid = approx_grid(self.n)
        self.folders = set()

    def tile_meets_source(self, face, x, y, z):
        """Whether the tile meets the source, and whether it is in the source all over."""
        a, b = tile_probe_positions(x, y, z)
        lon, lat = face_to_lonlat(face, a, b, self.equiangular)
        cols, rows = self.source.to_pixels(lon, lat)
        s = self.source
        inside = (cols >= 0) & (cols <= s.width) & (rows >= 0) & (rows <= s.height)
        return bool(np.any(inside)), bool(np.all(inside))

    def find_tiles(self, min_zoom, max_zoom):
        s = self.source
        f_lon, f_lat = s.footprint_lonlat()
        f_face, f_a, f_b = lonlat_to_face(f_lon, f_lat, self.equiangular)

        tiles = {}
        level = [(face, 0, 0, False) for face in range(6)]
        for z in range(0, max_zoom + 1):
            pz = 1 << z
            fx = np.minimum((f_a * pz).astype(np.int64), pz - 1)
            fy = np.minimum(((1.0 - f_b) * pz).astype(np.int64), pz - 1)
            with_points = set(zip(f_face.tolist(), fx.tolist(), fy.tolist()))

            found = []
            for f, x, y, full in level:
                if not full:
                    meets, full = self.tile_meets_source(f, x, y, z)
                    if not meets and (f, x, y) not in with_points:
                        continue
                found.append((f, x, y, full))
            if z >= min_zoom:
                tiles[z] = [(f, x, y) for f, x, y, _ in found]
            level = [(f, 2 * x + i, 2 * y + j, full) for f, x, y, full in found for j in (0, 1) for i in (0, 1)]
        return tiles

    def unwrap(self, cols, center):
        s = self.source
        if not s.wrap:
            return cols
        return np.mod(cols - center + s.width / 2.0, s.width) + center - s.width / 2.0

    def approx_pixels(self, face, a, b):
        s = self.source
        k, c, cell, frac = self.grid
        lon, lat = face_to_lonlat(face, a[np.ix_(k, k)], b[np.ix_(k, k)], self.equiangular)
        gc, gr = s.to_pixels(lon, lat)
        lon, lat = face_to_lonlat(face, a[np.ix_(c, c)], b[np.ix_(c, c)], self.equiangular)
        ec, er = s.to_pixels(lon, lat)
        if not (np.all(np.isfinite(gc)) and np.all(np.isfinite(gr)) and np.all(np.isfinite(ec)) and np.all(np.isfinite(er))):
            return None

        center = gc[len(k) // 2, len(k) // 2]
        gc = self.unwrap(gc, center)
        ec = self.unwrap(ec, center)

        cols = interpolate_knots(gc, cell, frac)
        rows = interpolate_knots(gr, cell, frac)
        error = max(np.max(np.abs(cols[np.ix_(c, c)] - ec)), np.max(np.abs(rows[np.ix_(c, c)] - er)))
        if not error <= APPROX_MAX_ERROR:
            return None
        return cols, rows

    def tile_pixels(self, face, x, y, z):
        n = self.n
        a, b = tile_positions(x, y, z, n)
        if self.source.projected and self.source.eqc is None:
            approx = self.approx_pixels(face, a, b)
            if approx is not None:
                return approx
        lon, lat = face_to_lonlat(face, a, b, self.equiangular)
        cols, rows = self.source.to_pixels(lon, lat)
        return self.unwrap(cols, cols[n // 2, n // 2]), rows

    def render(self, face, x, y, z):
        s = self.source
        n = self.n
        cols, rows = self.tile_pixels(face, x, y, z)

        good = np.isfinite(cols) & np.isfinite(rows)
        if not np.any(good):
            return None

        dc = np.maximum(np.abs(np.diff(cols, axis=1))[:-1, :], np.abs(np.diff(cols, axis=0))[:, :-1])
        dr = np.maximum(np.abs(np.diff(rows, axis=1))[:-1, :], np.abs(np.diff(rows, axis=0))[:, :-1])
        fx = pow2_floor(np.nanpercentile(dc, 10))
        fy = pow2_floor(np.nanpercentile(dr, 10))

        period = s.width if s.wrap else None
        half_c = np.hypot(pixel_steps(cols, 1, period), pixel_steps(cols, 0, period)) / 2.0
        half_r = np.hypot(pixel_steps(rows, 1), pixel_steps(rows, 0)) / 2.0
        half_c = np.where(good & np.isfinite(half_c), half_c, 0.0)
        half_r = np.where(good & np.isfinite(half_r), half_r, 0.0)

        col0 = int(math.floor(np.nanmin((cols - np.maximum(half_c, fx))[good])))
        col1 = int(math.ceil(np.nanmax((cols + np.maximum(half_c, fx))[good])))
        row0 = max(0, int(math.floor(np.nanmin((rows - np.maximum(half_r, fy))[good]))))
        row1 = min(s.height, int(math.ceil(np.nanmax((rows + np.maximum(half_r, fy))[good]))))

        if s.wrap:
            if col1 - col0 > s.width:
                pad = int(math.ceil(min(np.max(half_c), s.width / 2.0)))
                col0 = int(math.floor(cols[n // 2, n // 2] - s.width / 2.0)) - pad
                col1 = col0 + s.width + 2 * pad
        else:
            col0 = max(0, col0)
            col1 = min(s.width, col1)

        if col1 <= col0 or row1 <= row0:
            return None

        while ((col1 - col0) / fx) * ((row1 - row0) / fy) > MAX_WINDOW_PIXELS:
            fx *= 2
            fy *= 2

        if s.wrap:
            # Columns on the multiples of the step, so the parts of the read over the seam fit
            col0 = (col0 // fx) * fx
            col1 = -((-col1) // fx) * fx

        data, valid, sx, sy = s.read(col0, col1, row0, row1, fx, fy, self.read_resampling)

        u = np.where(good, (cols - col0) / sx, -1e9).ravel()
        v = np.where(good, (rows - row0) / sy, -1e9).ravel()

        values, ok = sample(data, valid, u - 0.5, v - 0.5, self.bilinear)

        if self.bilinear:
            hu =np.maximum(half_c.ravel() / sx, 0.5)
            hv = np.maximum(half_r.ravel() / sy, 0.5)
            big = good.ravel() & ((hu > 0.5) | (hv > 0.5))
            if np.any(big):
                values[:, big], ok[big] = area_average(data, valid, u[big], v[big], hu[big], hv[big])

        if not np.any(ok):
            return None
        return values.reshape(-1, n, n), ok.reshape(n, n)

    def encode(self, values, ok):
        n = self.n
        out = np.zeros((n, n, 4), dtype=np.uint8)

        if self.opts.encoding == "rgb":
            h = values[0].astype(np.float64)
            code = np.round((h - self.opts.min_height) / self.opts.resolution)
            # 0 is no data, r 255 is no data in RgbTerrain too
            code = np.clip(code, 1, 255 * 65536 - 1).astype(np.int64)
            code = np.where(ok, code, 0)
            out[..., 0] = (code >> 16) & 255
            out[..., 1] = (code >> 8) & 255
            out[..., 2] = code & 255
        else:
            v = values
            if self.opts.scale is not None:
                lo, hi = self.opts.scale
                v = (v - lo) * (255.0 / (hi - lo))
            v = np.clip(np.round(v), 0, 255).astype(np.uint8)
            if v.shape[0] >= 4:
                ok = ok & (v[3] > 0)
            for k in range(3):
                out[..., k] = np.where(ok, v[k if v.shape[0] >= 3 else 0], 0)

        out[..., 3] = np.where(ok, 255, 0)
        return out

    def tile_path(self, face, x, y, z):
        return os.path.join(self.opts.dest, str(face), str(z), str(x), f"{y}.{self.opts.format}")

    def write(self, path, image):
        rgba = Image.fromarray(image, "RGBA")
        if self.opts.format == "png":
            rgba.save(path, "PNG", compress_level=6)
        elif self.opts.encoding == "image" and self.opts.quality:
            rgba.save(path, "WEBP", quality=self.opts.quality, method=4)
        else:
            # exact keeps the rgb 0 of no data
            rgba.save(path, "WEBP", lossless=True, exact=True, quality=WEBP_LOSSLESS_QUALITY, method=WEBP_LOSSLESS_METHOD)

    def make_tile(self, tile):
        face, x, y, z = tile
        result = self.render(face, x, y, z)
        if result is None:
            return 0
        path = self.tile_path(face, x, y, z)
        folder = os.path.dirname(path)
        if folder not in self.folders:
            os.makedirs(folder, exist_ok=True)
            self.folders.add(folder)
        self.write(path, self.encode(*result))
        return 1


_worker_tiler = None


def _init_worker(opts):
    global _worker_tiler
    gdal.SetCacheMax(WORKER_GDAL_CACHE)
    _worker_tiler = Tiler(opts)


def _make_tile(tile):
    return _worker_tiler.make_tile(tile)


# -----------------------------------------------------------------------------
# Command line
# -----------------------------------------------------------------------------


def parse_zoom(text):
    parts = text.split("-")
    if len(parts) == 1:
        return int(parts[0]), int(parts[0])
    return int(parts[0]), int(parts[1])


def parse_args(argv):
    p = argparse.ArgumentParser(
        description="Cuts a GeoTIFF into the tiles of the OpenGlobus cube quad tree strategies.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("source", help="Source GeoTIFF")
    p.add_argument("dest", help="Destination folder")
    p.add_argument("--zoom", "-z", default="0-5", help="Zoom levels, for example 0-7")
    p.add_argument(
        "--cube",
        choices=["gnomonic", "equiangular"],
        default="gnomonic",
        help="gnomonic for CubeQuadTreeStrategy, equiangular for EquiangularCubeQuadTreeStrategy",
    )
    p.add_argument("--encoding", choices=["rgb", "image"], default="rgb", help="rgb for DEM, image for imagery")
    p.add_argument("--min-height", type=float, default=-10000.0, help="rgb encoding minHeight")
    p.add_argument("--resolution", type=float, default=0.1, help="rgb encoding resolution")
    p.add_argument(
        "--resampling",
        choices=["bilinear", "near"],
        default="bilinear",
        help="bilinear: the bilinear interpolation and the area average of the tile pixels larger than the source ones; "
        "near: the nearest source pixel",
    )
    p.add_argument("--s_srs", help="Source CRS, overrides the one of the file")
    p.add_argument(
        "--bounds",
        type=float,
        nargs=4,
        metavar=("LEFT", "TOP", "RIGHT", "BOTTOM"),
        help="Source bounds in degrees for a file with no georeferencing, as --b of tif2tiles.sh",
    )
    p.add_argument("--scale", type=float, nargs=2, metavar=("MIN", "MAX"), help="Imagery values to 0..255")
    p.add_argument("--format", choices=["webp", "png"], default="webp", help="Tile file format")
    p.add_argument("--tile-size", type=int, choices=TILE_SIZES, default=TILE_SIZE, help="Tile side in pixels")
    p.add_argument(
        "--quality", type=int, choices=range(1, 101), metavar="1..100", help="Lossy webp quality of the imagery"
    )
    # The cpus of the process, docker --cpuset-cpus limits them
    p.add_argument(
        "--processes", "-p", type=int, default=len(os.sched_getaffinity(0)), help="Processes, the cpus by default"
    )
    opts = p.parse_args(argv)
    opts.min_zoom, opts.max_zoom = parse_zoom(opts.zoom)
    return opts


def main(argv=None):
    opts = parse_args(argv)
    tiler = Tiler(opts)
    s = tiler.source

    print(f"Source: {opts.source} {s.width}x{s.height}, bands {s.bands}, nodata {s.nodata}, wraps: {s.wrap}")
    if s.unscale:
        print(f"Values by scale {s.scales.tolist()} and offset {s.offsets.tolist()}")
    if not s.ds.overviews(s.bands[0]):
        print("Warning: the source has no overviews, the low zooms read the full resolution (make a COG or gdaladdo)")
    print(f"Cube: {opts.cube}, zoom {opts.min_zoom}-{opts.max_zoom}, encoding {opts.encoding}, tiles {tiler.n} px")

    t0 = time.time()
    tiles = tiler.find_tiles(opts.min_zoom, opts.max_zoom)
    jobs = [(f, x, y, z) for z in sorted(tiles) for f, x, y in tiles[z]]
    print(f"Tiles to make: {len(jobs)} ({time.time() - t0:.1f}s)")

    written = 0
    done = 0
    shown = -1

    def progress():
        nonlocal shown
        percent = 100 * done // len(jobs)
        if percent != shown:
            shown = percent
            print(f"\r{percent}%", end="", flush=True)

    if opts.processes > 1 and len(jobs) > 1:
        with mp.Pool(opts.processes, initializer=_init_worker, initargs=(opts,)) as pool:
            for r in pool.imap_unordered(_make_tile, jobs, chunksize=4):
                written += r
                done += 1
                progress()
    else:
        for job in jobs:
            written += tiler.make_tile(job)
            done += 1
            progress()

    print(f"\rWritten {written} of {len(jobs)} tiles in {time.time() - t0:.1f}s")


if __name__ == "__main__":
    sys.exit(main())
