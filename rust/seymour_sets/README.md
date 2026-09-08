# seymour_sets

The quadruple search behind `find_custom_sets`, in Rust. Everything else in
`app/services/compute.py` is numpy-vectorised and fast enough; this one function
is a superlinear KD-tree walk with a Python-level selection loop, and at n=10,000
it cost more than every other function in that module combined.

## Build

Needs a Rust toolchain. `maturin develop` installs it into the active venv:

    maturin develop --release -m rust/seymour_sets/Cargo.toml

`app/services/compute.py` imports it as an optional dependency: a tree without
the wheel falls back to `_search_numpy`, which stays the reference
implementation. **That fallback is silent** -- if the deployed image has no
build step for this crate, production quietly runs the slow path.

## What it does and does not own

The boundary is the numeric kernel only. Piece dedup (`list(set(...))`) and the
smallest-piece-first argsort stay in `compute.py`, because those decide the
indices the kernel returns and Python's set iteration order is not something to
reproduce in Rust. The crate takes four per-piece LAB arrays and returns the
selected `(score, i0, i1, i2, i3)` quadruples.

Three things the port has to preserve exactly, all of them load-bearing for
*which* of several equally-scoring sets gets emitted:

- **f32 summation order** in `score_quad`, matching `_score_quads`.
- **Ascending neighbour lists**, matching `query_ball_point`, which is what makes
  candidates come out in lexicographic index order.
- **A stable sort** in `select` -- hence `par_sort_by`, not `par_sort_unstable_by`.

The radius test is inclusive and computed in f64, and the threshold comparison
widens the f32 score to f64, both because that is what scipy and numpy do.

## Correctness

`tests/unit/test_custom_sets_rust.py` runs both searches in one process and
asserts bit-identical output. It is differential rather than value-pinned on
purpose: the `*_output_is_unchanged` tests hold floats captured from a `--docker`
run and fail on a host interpreter for reasons unrelated to this crate.

## Measured

Host run, pinned numpy 1.26.4 / scipy 1.13.0, 16 cores, both paths timed in one
process (`_search_numpy` against `_search_rust`, best of 3):

| n | numpy | rust | |
|---|---|---|---|
| 500 | 0.3ms | 0.1ms | 4x |
| 2,000 | 6.2ms | 1.1ms | 5.8x |
| 5,000 | 77.8ms | 6.2ms | 12.5x |
| 10,000 | 638ms | 48ms | 13.3x |
| 15,000 | 2,210ms | 218ms | 10.1x |
| 30,000 | 15,901ms | 2,573ms | 6.2x |

Two findings worth keeping:

- **The selection sort was the ceiling, not the tree walk.** Serial `sort_by`
  held 30,000 to 1.5x; `par_sort_by` took it to 6.2x. Parallelising the scoring
  fan-out alone was not enough.
- **Grid cells are budgeted against the point count, not the bounding box.** LAB
  spread is wide enough that a radius-sized cell over the full box is ~800k
  cells; allocating that for a few hundred points made small inventories 7x
  *slower* than numpy before it was fixed.

Re-measure rather than trusting these; the numpy path OOMs at n=30,000 on a
16GB box roughly one run in three (it materialises 46.6M candidate rows, 712MB
for the index array alone), which is its own argument.
