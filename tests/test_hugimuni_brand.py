from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


PATH = Path(__file__).parents[1] / "media" / "brand" / "hugimuni" / "build.py"
SPEC = spec_from_file_location("hugimuni_brand", PATH)
assert SPEC and SPEC.loader
BUILD = module_from_spec(SPEC)
SPEC.loader.exec_module(BUILD)


def test_print_eps_is_process_cmyk_and_texture_free():
    out = BUILD.eps()
    assert "setcmykcolor" in out
    assert "setrgbcolor" not in out
    assert "false setoverprint" in out
    assert "arc fill" not in out
    # One H∩M crossing stroke per (H component, M component) pair — the
    # intersection ink is painted once per pairwise region, never per letter.
    intersection = f"{BUILD._ps_cmyk(BUILD.PRINT_INTERSECTION)} setcmykcolor"
    expected = len(BUILD.h_components()) * len(BUILD.m_components())
    assert out.count(intersection) == expected


def test_lockup_spells_name_with_leading_initials_on_two_registers():
    out = BUILD.eps(lockup=True)
    for text in ("(ugi) show", "(uni) show"):
        assert text in out
    assert "(H) show" not in out
    assert "(M) show" not in out
    assert "(HugiMuni)" not in out
    assert f"%%BoundingBox: 0 0 {BUILD.BOARD} {BUILD.LOCKUP_HEIGHT}" in out


def test_print_svg_is_a_flat_visual_proof_of_the_lockup():
    out = BUILD.print_proof_svg(lockup=True)
    assert "filter=" not in out
    assert ">ugi</text>" in out
    assert ">uni</text>" in out
    # The authored H geometry itself is drawn, not just the wordmark text.
    x1, y1, x2, y2, width = BUILD.h_components()[0]
    assert f'<path d="M {x1:g} {y1:g} L {x2:g} {y2:g}" ' in out
    assert f'stroke-width="{width:g}"' in out
