//! Fixed-radius neighbour search over 3D LAB points.
//!
//! A uniform grid rather than a KD-tree: every query in the custom-set walk uses
//! the same radius, which is what a grid is for, and it keeps the crate free of
//! a tree dependency. Coordinates are f64 because scipy's cKDTree promotes its
//! float32 input to double before comparing, and a candidate sitting exactly on
//! the radius must fall the same side of it here.

pub struct Grid {
    cell: f64,
    reach: i64,
    min: [f64; 3],
    dims: [i64; 3],
    starts: Vec<u32>,
    items: Vec<u32>,
    pts: Vec<[f64; 3]>,
}

/// Cells are budgeted against the point count, not the bounding box. LAB spread
/// is wide (L 0-100, a and b each ~256 across) so a radius-sized cell over the
/// full box is ~800k cells -- allocating and prefix-summing that for a few
/// hundred points cost ~2ms per call, which at small inventories was most of
/// the runtime.
fn cell_budget(n: usize) -> usize {
    (2 * n).max(1024)
}

impl Grid {
    pub fn build(pts: Vec<[f64; 3]>, radius: f64) -> Self {
        let mut min = [f64::INFINITY; 3];
        let mut max = [f64::NEG_INFINITY; 3];
        for p in &pts {
            for axis in 0..3 {
                min[axis] = min[axis].min(p[axis]);
                max[axis] = max[axis].max(p[axis]);
            }
        }
        if pts.is_empty() {
            min = [0.0; 3];
            max = [0.0; 3];
        }

        let budget = cell_budget(pts.len());
        let mut cell = if radius > 0.0 { radius } else { 1.0 };
        let mut dims = span_dims(min, max, cell);
        while cell_count(dims) > budget {
            cell *= 2.0;
            dims = span_dims(min, max, cell);
        }
        let reach = (radius / cell).ceil().max(1.0) as i64;

        let ncells = cell_count(dims);
        let mut counts = vec![0u32; ncells + 1];
        let index_of = |p: &[f64; 3]| -> usize {
            let mut flat = 0i64;
            for axis in 0..3 {
                let c = (((p[axis] - min[axis]) / cell).floor() as i64).clamp(0, dims[axis] - 1);
                flat = flat * dims[axis] + c;
            }
            flat as usize
        };

        for p in &pts {
            counts[index_of(p) + 1] += 1;
        }
        for i in 1..counts.len() {
            counts[i] += counts[i - 1];
        }

        let starts = counts.clone();
        let mut cursor = counts;
        let mut items = vec![0u32; pts.len()];
        for (i, p) in pts.iter().enumerate() {
            let cell_index = index_of(p);
            items[cursor[cell_index] as usize] = i as u32;
            cursor[cell_index] += 1;
        }

        Grid { cell, reach, min, dims, starts, items, pts }
    }

    pub fn len(&self) -> usize {
        self.pts.len()
    }

    /// Indices within `radius` of `q`, ascending.
    ///
    /// Ascending order is load-bearing: it is what makes the expansion emit
    /// candidates in lexicographic (i0, i1, i2, i3) order, which is the
    /// insertion order the selection's stable sort breaks ties by.
    pub fn query(&self, q: [f64; 3], radius: f64, out: &mut Vec<u32>) {
        out.clear();
        if self.pts.is_empty() {
            return;
        }
        let r2 = radius * radius;
        let base: Vec<i64> = (0..3)
            .map(|axis| ((q[axis] - self.min[axis]) / self.cell).floor() as i64)
            .collect();

        for dx in -self.reach..=self.reach {
            let x = base[0] + dx;
            if x < 0 || x >= self.dims[0] {
                continue;
            }
            for dy in -self.reach..=self.reach {
                let y = base[1] + dy;
                if y < 0 || y >= self.dims[1] {
                    continue;
                }
                for dz in -self.reach..=self.reach {
                    let z = base[2] + dz;
                    if z < 0 || z >= self.dims[2] {
                        continue;
                    }
                    let flat = ((x * self.dims[1] + y) * self.dims[2] + z) as usize;
                    for &i in &self.items[self.starts[flat] as usize..self.starts[flat + 1] as usize]
                    {
                        let p = &self.pts[i as usize];
                        let d0 = p[0] - q[0];
                        let d1 = p[1] - q[1];
                        let d2 = p[2] - q[2];
                        if d0 * d0 + d1 * d1 + d2 * d2 <= r2 {
                            out.push(i);
                        }
                    }
                }
            }
        }
        out.sort_unstable();
    }
}

fn span_dims(min: [f64; 3], max: [f64; 3], cell: f64) -> [i64; 3] {
    let mut dims = [1i64; 3];
    for axis in 0..3 {
        let n = ((max[axis] - min[axis]) / cell).floor() as i64 + 1;
        dims[axis] = n.max(1);
    }
    dims
}

fn cell_count(dims: [i64; 3]) -> usize {
    dims.iter().fold(1i128, |acc, &d| acc * d as i128).min(usize::MAX as i128) as usize
}
