//! Python entry point for the custom-set search.
//!
//! The boundary is deliberately narrow: this crate takes the four per-piece LAB
//! arrays already ordered by app/services/compute.py and returns the selected
//! quadruples. Everything order-sensitive on the Python side -- the `set()`
//! dedup of each piece's hexes and the argsort that puts the smallest piece
//! first -- stays in Python, because that ordering decides the indices this
//! returns and reproducing Python's set iteration in Rust would be a bug farm.

mod grid;
mod kernel;

use numpy::PyReadonlyArray2;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

fn rows(labs: &PyReadonlyArray2<f32>, slot: usize) -> PyResult<Vec<[f32; 3]>> {
    let view = labs.as_array();
    if view.ncols() != 3 {
        return Err(PyValueError::new_err(format!(
            "labs{slot} must be (n, 3), got (_, {})",
            view.ncols()
        )));
    }
    Ok(view.rows().into_iter().map(|r| [r[0], r[1], r[2]]).collect())
}

/// Quadruples under `threshold` whose four piece indices are pairwise unused.
///
/// Returns `(score, i0, i1, i2, i3)` best-first, indexed into the arrays as
/// passed. `radius` is the neighbour-search radius, `threshold` the maximum
/// mean distance-from-centroid a set may score.
#[pyfunction]
fn select_quads(
    py: Python<'_>,
    labs0: PyReadonlyArray2<'_, f32>,
    labs1: PyReadonlyArray2<'_, f32>,
    labs2: PyReadonlyArray2<'_, f32>,
    labs3: PyReadonlyArray2<'_, f32>,
    radius: f64,
    threshold: f64,
) -> PyResult<Vec<(f32, u32, u32, u32, u32)>> {
    let labs = [
        rows(&labs0, 0)?,
        rows(&labs1, 1)?,
        rows(&labs2, 2)?,
        rows(&labs3, 3)?,
    ];

    // Released for the whole search: this is the reason the crate exists. numpy
    // only drops the GIL inside individual ufunc calls, so concurrent uploads
    // serialise on the Python-level loop between them.
    let selected = py.allow_threads(|| kernel::search(&labs, radius, threshold));

    Ok(selected
        .into_iter()
        .map(|(score, i)| (score, i[0], i[1], i[2], i[3]))
        .collect())
}

#[pymodule]
fn seymour_sets(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(select_quads, m)?)?;
    Ok(())
}
