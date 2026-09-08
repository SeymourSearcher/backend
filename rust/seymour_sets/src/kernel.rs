//! The custom-set search: expand piece 0 outwards to quadruples, score, select.
//!
//! A port of the numpy walk in app/services/compute.py, and it has to agree with
//! it exactly -- tests/unit/test_custom_sets_rust.py runs both and compares.
//! Two things that agreement rests on:
//!
//! * Scores are f32 and summed in the order `_score_quads` uses. These are
//!   float32 means of float32 distances; reassociating them shifts the last
//!   digits, which moves ties, which silently selects a different set.
//! * Neighbours come back ascending (see grid::Grid::query), so candidates are
//!   emitted in lexicographic index order -- the insertion order the stable
//!   sort in `select` falls back on for equal scores.

use rayon::prelude::*;

use crate::grid::Grid;

pub struct Candidate {
    pub score: f32,
    pub idx: [u32; 4],
}

/// Triples per parallel chunk. Only affects scheduling: chunks are concatenated
/// in order, so the candidate sequence is identical at any chunk size.
const TRIPLE_CHUNK: usize = 4_096;

fn widen(p: [f32; 3]) -> [f64; 3] {
    [p[0] as f64, p[1] as f64, p[2] as f64]
}

fn norm(d: [f32; 3]) -> f32 {
    (d[0] * d[0] + d[1] * d[1] + d[2] * d[2]).sqrt()
}

/// Mean distance-from-centroid of the four labs, in `_score_quads`'s exact order.
fn score_quad(l: [[f32; 3]; 4]) -> f32 {
    let mut c = [0.0f32; 3];
    for axis in 0..3 {
        c[axis] = (l[0][axis] + l[1][axis] + l[2][axis] + l[3][axis]) * 0.25;
    }
    let sub = |a: [f32; 3]| [a[0] - c[0], a[1] - c[1], a[2] - c[2]];

    let mut total = norm(sub(l[3]));
    total += norm(sub(l[0]));
    total += norm(sub(l[1]));
    total += norm(sub(l[2]));
    total * 0.25
}

/// `threshold` is f64 on purpose. numpy compares a float32 score against a
/// Python float by widening the score, so a threshold that is not exactly
/// representable in f32 (1.3, say) would cut in a different place if compared
/// as f32 here.
pub fn search(
    labs: &[Vec<[f32; 3]>; 4],
    radius: f64,
    threshold: f64,
) -> Vec<(f32, [u32; 4])> {
    let grids: Vec<Grid> = (1..4)
        .map(|slot| Grid::build(labs[slot].iter().copied().map(widen).collect(), radius))
        .collect();
    let (g1, g2, g3) = (&grids[0], &grids[1], &grids[2]);

    if labs[0].is_empty() || g1.len() == 0 || g2.len() == 0 || g3.len() == 0 {
        return Vec::new();
    }

    let mut neighbours = Vec::new();

    let mut pairs: Vec<(u32, u32)> = Vec::new();
    for (i0, l0) in labs[0].iter().enumerate() {
        g1.query(widen(*l0), radius, &mut neighbours);
        pairs.extend(neighbours.iter().map(|&i1| (i0 as u32, i1)));
    }
    if pairs.is_empty() {
        return Vec::new();
    }

    let mut triples: Vec<(u32, u32, u32)> = Vec::new();
    for &(i0, i1) in &pairs {
        let a = labs[0][i0 as usize];
        let b = labs[1][i1 as usize];
        let mid = [
            (a[0] + b[0]) * 0.5,
            (a[1] + b[1]) * 0.5,
            (a[2] + b[2]) * 0.5,
        ];
        g2.query(widen(mid), radius, &mut neighbours);
        triples.extend(neighbours.iter().map(|&i2| (i0, i1, i2)));
    }
    if triples.is_empty() {
        return Vec::new();
    }

    // Over-threshold quadruples are dropped here rather than after scoring the
    // lot. The numpy version cannot: its selection segments candidates by triple
    // using run lengths, so a gap would break the offsets. Selection here carries
    // explicit indices, and dropping them leaves the surviving order untouched.
    let blocks: Vec<Vec<Candidate>> = triples
        .par_chunks(TRIPLE_CHUNK)
        .map(|chunk| {
            let mut found = Vec::new();
            let mut local = Vec::new();
            for &(i0, i1, i2) in chunk {
                let a = labs[0][i0 as usize];
                let b = labs[1][i1 as usize];
                let c = labs[2][i2 as usize];
                let centroid = [
                    (a[0] + b[0] + c[0]) / 3.0,
                    (a[1] + b[1] + c[1]) / 3.0,
                    (a[2] + b[2] + c[2]) / 3.0,
                ];
                g3.query(widen(centroid), radius, &mut local);
                for &i3 in &local {
                    let score = score_quad([a, b, c, labs[3][i3 as usize]]);
                    if (score as f64) < threshold {
                        found.push(Candidate { score, idx: [i0, i1, i2, i3] });
                    }
                }
            }
            found
        })
        .collect();

    select(blocks.into_iter().flatten().collect(), labs)
}

/// Lowest-scoring candidates whose four piece indices are all still unused.
///
/// The stable sort is the whole contract: equal scores keep insertion order, and
/// `test_greedy_select_agrees_with_sorting_every_candidate` is what pins the
/// numpy heap to this same sequence.
fn select(candidates: Vec<Candidate>, labs: &[Vec<[f32; 3]>; 4]) -> Vec<(f32, [u32; 4])> {
    // par_sort_by, not par_sort_unstable_by: rayon's stable sort is what keeps
    // equal scores in insertion order, and that ordering picks which of several
    // equally scoring sets gets emitted.
    let mut order: Vec<u32> = (0..candidates.len() as u32).collect();
    order.par_sort_by(|&a, &b| {
        candidates[a as usize]
            .score
            .partial_cmp(&candidates[b as usize].score)
            .expect("scores are finite")
    });

    let mut used: [Vec<bool>; 4] = std::array::from_fn(|slot| vec![false; labs[slot].len()]);
    let mut selected = Vec::new();
    for position in order {
        let c = &candidates[position as usize];
        if (0..4).any(|slot| used[slot][c.idx[slot] as usize]) {
            continue;
        }
        for slot in 0..4 {
            used[slot][c.idx[slot] as usize] = true;
        }
        selected.push((c.score, c.idx));
    }
    selected
}
