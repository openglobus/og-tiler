// Makes cube_ref.json, the reference points of the OpenGlobus cube projections for test_cube2tiles.py.
//
// From the openglobus repository folder:
//   NODE_PATH=. node_modules/.bin/esbuild <og-tiler>/test/cube/cube_ref.ts --bundle --platform=node --outfile=cube_ref.js
//   node cube_ref.js > <og-tiler>/test/cube/cube_ref.json
//
// NODE_PATH=. takes the imports src/... from the openglobus folder.
import { cube, cubeEquiangular } from "src/proj/cube";
import { LonLat } from "src/LonLat";

let s = 12345;
const rnd = () => (s = (s * 1103515245 + 12345) % 2147483648) / 2147483648;

const out: any = { toLonLat: [], fromLonLat: [] };

for (const [name, p] of [
    ["gnomonic", cube],
    ["equiangular", cubeEquiangular]
] as const) {
    for (let i = 0; i < 300; i++) {
        const face = i % 6,
            a = rnd(),
            b = rnd();
        const ll = p.toLonLat(face + a, b, face);
        out.toLonLat.push([name, face, a, b, ll.lon, ll.lat]);
    }
    for (let i = 0; i < 300; i++) {
        const lon = rnd() * 360 - 180,
            lat = (Math.asin(rnd() * 2 - 1) * 180) / Math.PI;
        const n = p.fromLonLat(new LonLat(lon, lat));
        out.fromLonLat.push([name, lon, lat, n.lon, n.lat]);
    }
}

console.log(JSON.stringify(out));
