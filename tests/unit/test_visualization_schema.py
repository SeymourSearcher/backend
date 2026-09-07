"""The caps on VisualizationRequest.

/visualization is unauthenticated and hands its input straight to a kd-tree walk
on a shared thread pool, so the schema is the only thing standing between a
caller and an unbounded amount of work. Non-hex input used to reach int(h, 16)
in app/services/compute.py and surface as a 500.
"""
import pytest
from pydantic import ValidationError

from app.pieces import Piece
from app.schemas.visualization import MAX_INPUTS, VisualizationRequest


@pytest.mark.parametrize("payload", [
    {"input_hex_codes": ["ZZZZZZ"]},
    {"input_hex_codes": ["FF00"]},
    {"input_seymours": [("GGGGGG", 0)]},
])
def test_non_hex_input_is_rejected_before_it_reaches_compute(payload):
    with pytest.raises(ValidationError):
        VisualizationRequest(**payload)


@pytest.mark.parametrize("field", ["input_hex_codes", "input_seymours"])
def test_neither_input_list_may_exceed_the_cap(field):
    item = "FF0000" if field == "input_hex_codes" else ("FF0000", 0)

    VisualizationRequest(**{field: [item] * MAX_INPUTS})
    with pytest.raises(ValidationError):
        VisualizationRequest(**{field: [item] * (MAX_INPUTS + 1)})


def test_an_inverted_delta_e_band_is_rejected():
    with pytest.raises(ValidationError):
        VisualizationRequest(min_de=4.0, max_de=1.0)


def test_hex_input_is_upper_cased_for_the_caller():
    req = VisualizationRequest(input_hex_codes=["00ff00"], input_seymours=[("ff0000", 0)])

    assert req.input_hex_codes == ["00FF00"]
    assert req.input_seymours == [("FF0000", Piece.HELMET)]
