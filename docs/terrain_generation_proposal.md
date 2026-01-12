
## 1. Algorithm overview

### Core pipeline (layers are independently tunable)

1. **Base scalar fields (continuous)**

* `H` = elevation (float32)
* `M` = moisture (float32)
* `R` = “rockiness / forest density” helper fields (float32)

2. **Island shaping**

* Apply **centered radial falloff mask** to guarantee one main island + 3-tile water border.
* Choose **sea level** by quantile to hit ~70–75% land.

3. **Coastal refinement**

* Keep **largest connected land component** (8-connected).
* Light morphological smoothing to make “thin is rare” without killing peninsulas.

4. **Hydrology**

* Build a **drainage-safe surface** `H_fill` via Priority-Flood (or a variant) to route flow consistently. This is a standard DEM preprocessing move. ([arXiv][1])
* Compute **D8 flow direction + flow accumulation** on `H_fill`. D8 is the classic simple flow model for gridded DEMs. ([ArcGIS Pro][2])
* Select 2–4 river sources, trace to ocean, widen to 2–3 tiles, add mild meander.
* Create 1–3 fords per river by converting short river segments to shallow-water (walkable).

5. **Terrain classification**

* Water (deep vs shallow), beach sand (0–3), mountains (≤10% of land), grass/dirt by moisture + slope + distance-to-water.

6. **Natural features (objects)**

* Forests/bush/rocks as clustered stochastic fields (dense cores + soft edges).
* Multi-size rocks by quantiles of a “rockiness” field + proximity to mountains/slope.

7. **Validation & repair**

* Enforce: single island, border water, mountain cap, river count & reach-ocean, ford count.

---

## 2. Noise configuration (what to use, and concrete parameters)

You asked for specific noise types. In Python, the *practical* trick is: use “Perlin/Simplex-like” fields via either:

* **A noise library** (Simplex/OpenSimplex/FastNoiseLite), OR
* **Gaussian-filtered random fields** (fast in SciPy, surprisingly good, and very implementable).

Either approach can be wrapped as the same interface: `noise2(x,y,freq,seed) → [-1,1]`.

### Recommended field recipes

#### Elevation `H` (smooth/blobby coastline, interesting interior)

* **fBm** (fractal Brownian motion): sum of octaves at increasing frequency and decreasing amplitude. ([The Book of Shaders][3])
* Add **ridged multifractal** component for mountains/ridges. ([Libnoise][4])
* Add **domain warping** to avoid “contour bands” and make coast organic. ([3DWorld][5])

Concrete parameters (tuned for 4000×4000):

* fBm:

  * octaves: 6
  * base wavelength: 900 tiles (freq ≈ 1/900)
  * lacunarity: 2.0
  * gain: 0.5
  * amplitude: 1.0
* domain warp:

  * warp wavelength: 700 tiles
  * warp amplitude: 120 tiles (big, but blobby coast likes it)
  * warp octaves: 3
* ridged (mountain candidate field):

  * wavelength: 1100 tiles
  * octaves: 4
  * weight: +0.55 into elevation (before mountain classification)

#### Moisture `M`

* fBm:

  * octaves: 5
  * base wavelength: 1200 tiles
  * gain: 0.55
* then bias by distance-to-water:

  * `M = 0.65*M_noise + 0.35*(1 - norm_dist_to_water)`

#### Forest density helper `F`

* fBm or Worley-ish “cellular” (optional), then smoothstep to get clustered blobs
* base wavelength: 600 tiles, octaves 4
* `F = smoothstep(0.2, 0.75, F_noise)`

(We can add Worley/cellular later if you want explicit “groves”; for v1, blobbed fBm is plenty.)

---

## 3. Island shape generation (guarantee single island, 70% land, 3-tile border)

### Centered falloff mask

Let `r` be normalized distance from center (0 at center, ~1 at corners). Use a smooth falloff:

* `fall = smoothstep(r_start, r_end, r)`
* Typical: `r_start=0.62`, `r_end=0.98`
* Apply: `H = H - fall * coast_drop`, with `coast_drop ~ 2.2`

This yields a big landmass that naturally tapers to sea, not a hard circle.

### 3-tile buffer

After all shaping but before classification:

* Force `H[y<3 or y>=H-3 or x<3 or x>=W-3] = very_low`

### Hit ~70–75% land

Compute sea level as a quantile of the masked height:

* `sea = quantile(H, q = 0.30)`  (because 70% land)
  Then classify water as `H < sea` (later refined into shallow/deep).

This is stable and seed-to-seed consistent.

---

## 4. Coastal refinement (smooth, blobby, thin strips rare)

After initial land/water:

1. **Keep largest land component** (8-connectivity).

   * Any secondary islands become water.

2. **Soft anti-spaghetti**

   * 1–2 iterations of a majority filter:

     * if land and has ≤2 land neighbors → flip to water (rare thin spikes)
     * if water and has ≥6 land neighbors → flip to land (fills pinholes)
       This keeps coastline blobby without over-sanding.

---

## 5. Hydrology (2–4 rivers, 2–3 wide, meanders, fords, optional lakes)

### 5.1 Drainage surface via Priority-Flood (but keep lakes as “natural outcomes”)

Priority-Flood is a clean, implementable algorithm that “floods” inward from edges using a priority queue, producing a filled surface where everything can drain. ([arXiv][1])

We’ll compute:

* `H_fill = priority_flood_fill(H, boundary=ocean_cells)`

Then:

* **Lake candidates** are where `H_fill > H + eps`. Those are *natural depressions*; we’re not aiming for them, but if they exist, they exist.

### 5.2 Flow direction + accumulation

Compute D8 flow directions on `H_fill` (each cell points to its steepest downslope neighbor). ([ArcGIS Pro][2])
Compute accumulation (number of upstream contributors). Use:

* coarse grid (e.g. 1000×1000) for speed OR full-res if you’re patient.

### 5.3 River selection

Pick `K ~ randint(2,4)` river sources:

* candidates: inland (distance-to-coast > 250), not ocean, `H_fill` in top ~20%
* enforce min spacing between sources (e.g. ≥600 tiles)

Trace each river by following flow direction until reaching ocean.

### 5.4 Meander + width

You’ll get “too straight” rivers if you strictly follow steepest descent. Fix:

* At each step, choose next cell via **biased stochastic downhill choice**:

  * sample among downhill neighbors weighted by `exp(-ΔH / t)` with a small temperature `t`
  * this introduces gentle meander while still “respecting topology”

Then widen:

* width = 2 normally, 3 if accumulation above threshold
* rasterize a centerline and dilate (disk radius 1) for width 3

### 5.5 Fords (1–3 per river)

For each river polyline:

* choose candidate segments where slope is low and not near the mouth/source
* place `n_fords = randint(1,3)`
* convert a short span (length 6–10 tiles) across the full river width to `shallow_water` (walkable)

---

## 6. Terrain type assignment (discrete tiles, smooth transitions)

We’ll compute helper maps:

* `dist_to_water` on land (integer)
* `dist_to_land` on ocean (integer)
* `slope = |∇H|` (finite difference)

### 6.1 Water: deep vs shallow

* deep water: all ocean by default
* shallow water (coastal patches, 1–2 wide):

  * for ocean cells where `dist_to_land <= 2` AND `shallow_noise > threshold`
  * plus: all ford segments are shallow (overrides)

### 6.2 Sand beaches: width 0–3

For each land cell:

* compute `beach_w = floor(3 * smoothstep(0.2, 0.8, beach_noise))` → {0,1,2,3}
* if `dist_to_water <= beach_w` → sand (unless mountain)

This naturally makes some coastlines beachless.

### 6.3 Mountains: cap at ≤10% of land

Mountains should be impassable and rare:

* only eligible if:

  * inland (dist_to_water > 20)
  * high elevation (e.g. `H_fill > quantile(land_heights, 0.88)`)
  * and/or ridged_noise high
* then adjust threshold so that `mountain_count <= 0.10 * land_count`

### 6.4 Grass vs dirt

On remaining land:

* dirt if `(M < 0.42)` OR `(slope > slope_thresh)` OR (near mountain and lower moisture)
* else grass

This avoids hard biome stripes.

---

## 7. Objects (trees/bushes/rocks) with natural clustering

One object per tile; floor decides walkability.

### 7.1 Forests (trees)

Use `F` (forest density field) and taper by distance-to-coast and slope:

* `tree_p = tree_base * F^2 * clamp(dist_to_water/80, 0, 1) * clamp(1 - slope/slope_max, 0, 1)`
* sample `rand < tree_p` → tree

This gives dense cores (because `F^2`) and naturally thinning edges.

### 7.2 Bushes

* higher near forest edges and near water but not sand:

  * `bush_p = bush_base * (1 - F) * smoothstep(10, 60, dist_to_water)`

### 7.3 Rocks (multi-size)

Define `rockiness`:

* `rockiness = 0.6*ridged_noise + 0.4*slope_norm`
  Then sample rocks where `rockiness` is high and floor is grass/dirt/mountain-adjacent.

Sizes by quantiles of `rockiness` at placed rock cells:

* top 2%: boulder
* next 5%: large rock
* next 10%: medium
* next 20%: small
* rest: pebble/small rock

(“Erosion plausibility” without simulating erosion everywhere. If you later add erosion passes, keep the same interface and just change `rockiness`.)

---

## 8. Edge cases and how to handle them

### Disconnected land / secondary islands

* keep largest component
* optionally “heal” tiny channels by 1 iteration of majority filter

### Rivers that fail to reach ocean

* shouldn’t happen if flow is on `H_fill` with boundary ocean, because everything drains outward by construction. ([arXiv][1])
* if you later allow weird masks, fallback: re-pick source.

### Too many lakes / ugly pits

* you said fine if natural; still, you can prune tiny lakes not connected to rivers:

  * if lake_area < A_min and not adjacent to river → convert to land (or shallow)

### Mountain overgrowth

* enforce hard cap by threshold adjustment.

---

## 9. Validation checks (post-generation)

1. Border: 3-tile rim all deep water ✅
2. Land fraction: within tolerance, e.g. 0.70–0.78 ✅
3. Largest land component: ≥ 0.99 of land (or just “largest wins”) ✅
4. Mountain fraction: ≤ 0.10 of land ✅
5. Rivers: count ∈ [2,4], each reaches ocean ✅
6. Fords: 1–3 per river ✅
7. No sand on mountains; no trees on sand/water; etc ✅

If any fails:

* prefer **local repair** (threshold tweak, component prune, ford placement retry) over full regen,
* but regen is cheap if you want simplicity.

---

## 10. Pseudocode / implementation sketch

```python
def generate_island(seed: int, W=4000, H=4000):
    rng = np.random.default_rng(seed)

    # --- Stage A: base continuous fields ---
    H0 = make_elevation(W, H, rng)         # fBm + ridged + domain warp + radial falloff
    M  = make_moisture(W, H, rng, H0)

    enforce_border_ocean(H0, border=3)

    # --- Stage B: sea level for ~70% land ---
    sea = np.quantile(H0, 0.30)
    land = (H0 >= sea)

    # --- Stage C: coastal refinement ---
    land = keep_largest_component_8(land)
    land = majority_smooth(land, iters=2)

    # recompute sea with land lock? optional:
    # (usually keep sea fixed; land mask fixes stragglers)

    # --- Stage D: hydrology ---
    # Use height that guarantees drainage (Priority-Flood)
    H_fill = priority_flood_fill(H0, ocean_mask=~land)

    flow_dir = d8_flow_direction(H_fill)          # each cell -> one neighbor index
    acc = flow_accumulation(flow_dir)             # maybe coarse grid version

    rivers = carve_rivers(flow_dir, acc, land, rng, k_min=2, k_max=4)
    rivers = widen_and_meander(rivers, acc, rng, width_min=2, width_max=3)

    rivers, fords = place_fords(rivers, H_fill, rng, min_per=1, max_per=3)

    # --- Stage E: distance fields ---
    dist_to_water = distance_to_water_on_land(land, rivers)   # treat rivers as water too
    dist_to_land  = distance_to_land_in_ocean(land)

    # --- Stage F: classify floor types ---
    floor = np.full((H, W), GRASS, dtype=np.uint16)

    # water
    floor[~land] = DEEP_WATER

    # shallow ocean patches (1-2 tiles)
    shallow_noise = fBm_noise(W, H, rng, base_wavelength=800, octaves=3)
    shallow = (~land) & (dist_to_land <= 2) & (shallow_noise > 0.55)
    floor[shallow] = SHALLOW_WATER

    # rivers override
    floor[rivers] = DEEP_WATER
    floor[fords]  = SHALLOW_WATER

    # beaches (0-3)
    beach_noise = fBm_noise(W, H, rng, base_wavelength=500, octaves=3)
    beach_w = np.floor(3 * smoothstep(0.2, 0.8, beach_noise)).astype(np.int8)
    sand = land & (dist_to_water <= beach_w)
    floor[sand] = SAND

    # mountains <= 10% land
    ridged = ridged_noise(W, H, rng, base_wavelength=1100, octaves=4)
    slope = gradient_magnitude(H0)
    mountain_candidate = land & (dist_to_water > 20) & ((ridged > 0.6) | (H_fill > np.quantile(H_fill[land], 0.88)))
    floor = apply_mountain_cap(floor, mountain_candidate, cap_frac=0.10)

    # grass vs dirt
    dirt = land & (floor != SAND) & (floor != MOUNTAIN) & ((M < 0.42) | (slope > slope_thresh))
    floor[dirt] = DIRT

    # --- Stage G: objects ---
    obj = np.zeros((H, W), dtype=np.uint16)  # 0 = none

    F = forest_density_field(W, H, rng)
    obj = place_trees(obj, floor, F, dist_to_water, slope, rng, tree_base=...)
    obj = place_bushes(obj, floor, F, dist_to_water, rng, bush_base=...)
    obj = place_rocks_by_size(obj, floor, ridged, slope, rng, rock_base=...)

    # --- Stage H: validate + debug dumps ---
    validate(floor, obj, rivers, fords, land)
    dump_debug_images(seed, H0, H_fill, land, acc, floor, obj, rivers, fords)

    return floor, obj
```

Notes:

* Priority-Flood is short to implement (priority queue over grid) and well-documented in the Barnes/Lehman/Mulla work. ([arXiv][1])
* D8 flow direction/accumulation is simple and standard; you can swap in MFD later if you want braided systems. ([ArcGIS Pro][2])
* If you later want more “real erosion”, Olsen’s survey is a good practical reference for game terrain erosion methods. ([Massachusetts Institute of Technology][6])

[1]: https://arxiv.org/abs/1511.04463?utm_source=chatgpt.com "Priority-Flood: An Optimal Depression-Filling and Watershed-Labeling Algorithm for Digital Elevation Models"
[2]: https://pro.arcgis.com/en/pro-app/3.4/tool-reference/spatial-analyst/how-flow-direction-works.htm?utm_source=chatgpt.com "How Flow Direction works—ArcGIS Pro | Documentation"
[3]: https://thebookofshaders.com/13/?utm_source=chatgpt.com "Fractal Brownian Motion"
[4]: https://libnoise.sourceforge.net/docs/classnoise_1_1module_1_1RidgedMulti.html?utm_source=chatgpt.com "noise::module::RidgedMulti Class Reference [Generator ..."
[5]: https://3dworldgen.blogspot.com/2017/05/domain-warping-noise.html?utm_source=chatgpt.com "Domain Warping Noise"
[6]: https://web.mit.edu/cesium/Public/terrain.pdf?utm_source=chatgpt.com "Realtime Procedural Terrain Generation"

