"""Where this microscope saves its pictures: set up once, like the limits and the orientation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from zmart_drivers.leica.stellaris_y42h93.navigator_expert.driver_setup import (
    data_location as location,
)
from zmart_drivers.leica.stellaris_y42h93.navigator_expert.driver_setup import store

NOTEBOOKS = Path(location.__file__).resolve().parent / "notebooks"


@pytest.fixture
def machine(tmp_path):
    return store.MachineProfile(programdata_root=tmp_path / "ProgramData")


def test_nothing_is_set_up_until_it_is(machine):
    assert location.data_location(machine) is None


def test_a_set_location_is_saved_and_loaded(machine, tmp_path):
    folder = tmp_path / "ZMART-data"
    folder.mkdir()

    saved = location.set_data_location(folder, machine=machine)

    assert saved.name == "data_location.json"
    assert saved.parent.parent == machine.subsystem_root("data_location")
    assert json.loads(saved.read_text(encoding="utf-8")) == {
        "folder": str(folder.resolve())
    }
    assert location.data_location(machine) == folder.resolve()


def test_the_newest_location_wins(machine, tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()

    location.set_data_location(first, machine=machine)
    location.set_data_location(second, machine=machine)

    assert location.data_location(machine) == second.resolve()


@pytest.mark.parametrize(
    ("make", "says"),
    [
        (lambda tmp: tmp / "missing", "does not exist"),
        (
            lambda tmp: (tmp / "a-file").write_text("x") and tmp / "a-file",
            "not a folder",
        ),
        (lambda tmp: Path("relative/folder"), "full path"),
        (lambda tmp: Path(r"\\server\share\data"), "network"),
    ],
)
def test_a_folder_that_cannot_hold_the_data_is_refused(machine, tmp_path, make, says):
    folder = make(tmp_path)

    assert any(says in problem for problem in location.check(folder))
    with pytest.raises(ValueError, match=says):
        location.set_data_location(folder, machine=machine)
    assert location.data_location(machine) is None


def test_a_good_folder_has_no_problems(tmp_path):
    assert location.check(tmp_path) == []


def test_the_set_up_notebook_is_there():
    notebook = json.loads(
        (NOTEBOOKS / "set_data_location.ipynb").read_text(encoding="utf-8")
    )
    code = "".join(
        "".join(cell["source"])
        for cell in notebook["cells"]
        if cell["cell_type"] == "code"
    )
    assert "set_data_location(" in code
