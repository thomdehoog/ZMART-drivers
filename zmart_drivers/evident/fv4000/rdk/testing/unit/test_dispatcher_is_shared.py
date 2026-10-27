"""The dispatcher is the same in every driver: this driver's copy is byte for byte the Leica driver's.

The anatomy says a driver copies the dispatcher unchanged. This test makes
that a red test instead of a code-review comment: a change to one copy must
be made to both, or moved into the vendor profile where it belongs.
"""

from pathlib import Path

DRIVER_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = DRIVER_ROOT.parents[3]
LEICA_DISPATCHER = (
    REPO_ROOT
    / "zmart_drivers"
    / "leica"
    / "stellaris"
    / "navigator_expert"
    / "dispatcher"
)


def test_the_dispatcher_is_byte_for_byte_the_leica_drivers():
    ours = {p.name: p.read_bytes() for p in (DRIVER_ROOT / "dispatcher").glob("*.py")}
    theirs = {p.name: p.read_bytes() for p in LEICA_DISPATCHER.glob("*.py")}
    assert (
        ours.keys()
        == theirs.keys()
        == {"__init__.py", "dispatch.py", "read.py", "write.py", "limits.py"}
    )
    different = sorted(name for name in ours if ours[name] != theirs[name])
    assert different == [], (
        f"the dispatcher differs from the Leica driver's in {different}; the dispatcher is shared"
    )


def test_the_dispatcher_imports_nothing_of_either_driver():
    for path in (DRIVER_ROOT / "dispatcher").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "primitives" not in text.replace("primitives/", "").replace(
            "primitive", ""
        ), path.name
        assert "from .." not in text and "zmart_drivers." not in text, path.name
