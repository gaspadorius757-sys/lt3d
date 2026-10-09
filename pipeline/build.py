#!/usr/bin/env python3
"""OSM PBF -> 1km tiles (gzip JSON, LKS94/EPSG:3346 metres) + country overview + index.
Usage: python pipeline/build.py lithuania-latest.osm.pbf data
Needs ~4-8 GB RAM for the whole country (tiles are buffered, then written)."""
import sys, os, re, gzip, json, time, hashlib
import osmium, shapely.wkb as wkb
from shapely.geometry import box, LineString
from shapely.prepared import prep
from shapely import ops
from pyproj import Transformer

TILE, X0, Y0 = 1000, 500000, 6100000
TR = Transformer.from_crs(4326, 3346, always_xy=True)
proj = lambda g: ops.transform(TR.transform, g)
ROAD = {'motorway':(0,12),'trunk':(0,10),'primary':(1,9),'secondary':(2,8),'tertiary':(3,7),
 'unclassified':(3,6),'residential':(4,6),'living_street':(4,5),'service':(5,4),'track':(6,3),
 'path':(7,1.5),'footway':(7,1.5),'cycleway':(7,2),'pedestrian':(7,4),'motorway_link':(2,7),
 'trunk_link':(2,7),'primary_link':(3,6),'secondary_link':(3,6),'tertiary_link':(4,5)}
r1 = lambda v: round(v, 1)

def flat(coords, tx=0, ty=0): return [r1(c) for x, y in coords for c in (x - tx*TILE, y - ty*TILE)]
def lines_of(g):
    if g.geom_type == 'LineString': return [g]
    return [l for p in getattr(g, 'geoms', []) for l in lines_of(p)]
def polys_of(g):
    if g.geom_type == 'Polygon': return [g]
    return [q for p in getattr(g, 'geoms', []) for q in polys_of(p)]
def tiles_of(g):
    a, b, c, d = g.bounds
    for tx in range(int(a//TILE), int(c//TILE)+1):
        for ty in range(int(b//TILE), int(d//TILE)+1): yield tx, ty
def rings(p, tx=0, ty=0): return [flat(p.exterior.coords, tx, ty)] + [flat(i.coords, tx, ty) for i in p.interiors]
def height(t, i):
    for k in ('height', 'building:height'):
        try: return float(re.match(r'[\d.]+', t[k]).group())
        except Exception: pass
    try: return float(t['building:levels'])*3.2+1
    except Exception: return 6 + int(hashlib.md5(str(i).encode()).hexdigest()[:2], 16) % 7

class H(osmium.SimpleHandler):
    def __init__(s):
        super().__init__(); s.t = {}; s.places = []; s.road_ov = []; s.water_ov = []; s.country = []
        s.f = osmium.geom.WKBFactory()
    def T(s, k): return s.t.setdefault(k, {'r':[], 'b':[], 'w':[], 'f':[], 'p':[]})
    def node(s, n):
        pl = n.tags.get('place')
        if pl in ('city', 'town', 'village') and 'name' in n.tags and n.location.valid():
            x, y = TR.transform(n.location.lon, n.location.lat)
            s.places.append([n.tags['name'], pl, round(x), round(y)])
    def way(s, w):
        t = w.tags; hw = t.get('highway')
        if hw in ROAD: cls, wd = ROAD[hw]
        elif t.get('railway') == 'rail': cls, wd = 8, 3
        elif t.get('waterway') in ('river', 'stream', 'canal'): cls, wd = 9, (6 if t['waterway'] == 'river' else 2)
        else: return
        if t.get('tunnel') not in (None, 'no'): return
        try: g = proj(wkb.loads(s.f.create_linestring(w), hex=True)).simplify(.5)
        except Exception: return
        br = 0 if t.get('bridge') in (None, 'no') else 1
        if cls <= 2: s.road_ov.append([cls, [round(v) for v in flat(g.simplify(100).coords)]])
        for tx, ty in tiles_of(g):
            for l in lines_of(g.intersection(box(tx*TILE, ty*TILE, (tx+1)*TILE, (ty+1)*TILE))):
                s.T((tx, ty))['r'].append([cls, wd, flat(l.coords, tx, ty), br])
    def area(s, a):
        t = a.tags
        if t.get('boundary') == 'administrative' and t.get('admin_level') == '2': kind = 'c'
        elif 'building' in t: kind = 'b'
        elif t.get('natural') == 'water' or t.get('landuse') == 'reservoir' or t.get('waterway') == 'riverbank': kind = 'w'
        elif t.get('landuse') == 'forest' or t.get('natural') == 'wood': kind = 'f'
        elif t.get('leisure') in ('park', 'garden') or t.get('landuse') in ('grass', 'meadow', 'recreation_ground'): kind = 'p'
        else: return
        try:
            g = proj(wkb.loads(s.f.create_multipolygon(a), hex=True))
            if not g.is_valid: g = g.buffer(0)
        except Exception: return
        for p in polys_of(g):
            if kind == 'c': s.country.append([[round(v) for v in r] for r in rings(p.simplify(150))]); continue
            if kind == 'b':
                if p.area < 4: continue
                c = p.centroid; k = (int(c.x//TILE), int(c.y//TILE))
                s.T(k)['b'].append([r1(height(t, a.orig_id())), flat(p.simplify(.3).exterior.coords, *k)]); continue
            if kind == 'w' and p.area > 5e5:
                s.water_ov.append([[round(v) for v in r] for r in rings(p.simplify(100))])
            p = p.simplify(.5); pp = prep(p)
            for tx, ty in tiles_of(p):
                b = box(tx*TILE, ty*TILE, (tx+1)*TILE, (ty+1)*TILE)
                if not pp.intersects(b): continue
                for q in polys_of(p.intersection(b)):
                    if q.area >= 1: s.T((tx, ty))[kind].append(rings(q, tx, ty))

def wj(path, obj):
    with gzip.open(path, 'wt', compresslevel=9) as f: json.dump(obj, f, separators=(',', ':'))

if __name__ == '__main__':
    pbf, out = sys.argv[1], sys.argv[2]
    os.makedirs(f'{out}/tiles', exist_ok=True)
    h = H(); t0 = time.time(); h.apply_file(pbf, locations=True, idx='flex_mem')
    print(f'parsed in {time.time()-t0:.0f}s: {len(h.t)} tiles, {len(h.places)} places')
    for (tx, ty), d in h.t.items(): wj(f'{out}/tiles/{tx}_{ty}.json.gz', d)
    wj(f'{out}/overview.json.gz', {'country': h.country, 'roads': h.road_ov, 'water': h.water_ov,
        'places': [p for p in h.places if p[1] in ('city', 'town')]})
    sp = next((p for p in h.places if p[0] == 'Vilnius' and p[1] == 'city'), h.places[0])
    xs = [k[0] for k in h.t]; ys = [k[1] for k in h.t]
    json.dump({'v': int(time.time()), 'tile': TILE, 'x0': X0, 'y0': Y0, 'spawn': [sp[2], sp[3]],
        'bounds': [min(xs)*TILE, min(ys)*TILE, (max(xs)+1)*TILE, (max(ys)+1)*TILE],
        'tiles': [f'{x}_{y}' for x, y in h.t]}, open(f'{out}/index.json', 'w'))
    print('done')
