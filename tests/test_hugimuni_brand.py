from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


PATH = Path(__file__).parents[1] / "media" / "brand" / "hugimuni" / "build.py"
SPEC = spec_from_file_location("hugimuni_brand", PATH)
assert SPEC and SPEC.loader
BUILD = module_from_spec(SPEC)
SPEC.loader.exec_module(BUILD)


def test_print_eps_is_process_cmyk_and_texture_free():
    out = BUILD.eps("amber-sky")
    assert "setcmykcolor" in out
    assert "setrgbcolor" not in out
    assert "false setoverprint" in out
    assert "arc fill" not in out
    assert out.count("0.000 0.000 0.000 0.000 setcmykcolor") >= 5


def test_lockup_spells_name_with_leading_initials_on_two_registers():
    out = BUILD.eps("amber-sky", lockup=True)
    for text in ("(UGI) show", "(UNI) show"):
        assert text in out
    assert "(H) show" not in out
    assert "(M) show" not in out
    assert "(HugiMuni)" not in out
    assert "%%BoundingBox: 0 0 512 690" in out


def test_print_svg_is_a_flat_visual_proof_of_the_lockup():
    out = BUILD.print_svg("amber-sky", lockup=True)
    assert "filter=" not in out
    assert ">UGI</text>" in out
    assert ">UNI</text>" in out
    assert "M142 535V580" in out
