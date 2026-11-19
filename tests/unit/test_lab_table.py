import numpy as np
import pytest

from app.services import compute
from app.services.lab_table import build_lab_table


def test_the_shared_tables_are_read_only():
    assert not compute.ALL_LAB.flags.writeable
    if compute.USE_PRECOMPUTED:
        assert not compute.HEX_MATCH_KEYS_IDX.flags.writeable
    with pytest.raises(ValueError):
        compute.ALL_LAB[0] = 0


def test_the_mapped_table_is_exactly_what_the_builder_produces():
    """The cKDTrees and every hex_* table are built from ALL_LAB, so a stale
    lab_table.npy would silently shift every ΔE."""
    assert np.array_equal(compute.ALL_LAB, build_lab_table())
