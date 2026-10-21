"""
Unit tests: LAS X's Navigator canvas, cleared to let go of its hidden copies.
================================================================================
Every capture leaves a hidden copy in LAS X's cache (``TileScan <n>``, about
twice the capture's pixels), held while the Navigator canvas shows its tile;
the canvas's Clear button lets go of them all (simulator, 2026-10-14). The
button sits in the Navigator's action bar; the driver finds it once and
keeps it, presses it only when it is enabled (the canvas shows tiles), and
finds it again when the kept one no longer answers.
"""

from types import SimpleNamespace

import pytest

from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_specific.primitives.interface import lasx_navigator


class StandInButton:
    def __init__(self, enabled=True):
        self.enabled, self.pressed, self.gone = enabled, 0, False

    def is_enabled(self):
        if self.gone:
            raise OSError("the element is not available any more")
        return self.enabled

    def press(self):
        if self.gone:
            raise OSError("the element is not available any more")
        self.pressed += 1


@pytest.fixture
def buttons(monkeypatch):
    made = []

    def find():
        made.append(StandInButton())
        return made[-1]

    monkeypatch.setattr(lasx_navigator, "find_clear_button", find)
    lasx_navigator.forget()
    yield made
    lasx_navigator.forget()


def test_a_canvas_with_tiles_is_cleared(buttons):
    assert lasx_navigator.clear_canvas() is True
    assert buttons[0].pressed == 1


def test_an_empty_canvas_is_left_alone(buttons):
    lasx_navigator.clear_canvas()
    buttons[0].enabled = False
    assert lasx_navigator.clear_canvas() is False
    assert buttons[0].pressed == 1


def test_the_button_is_found_once_and_kept(buttons):
    lasx_navigator.clear_canvas()
    lasx_navigator.clear_canvas()
    assert len(buttons) == 1
    assert buttons[0].pressed == 2


def test_a_kept_button_that_no_longer_answers_is_found_again(buttons):
    lasx_navigator.clear_canvas()
    buttons[0].gone = True
    assert lasx_navigator.clear_canvas() is True
    assert len(buttons) == 2
    assert buttons[1].pressed == 1


def test_no_navigator_raises(monkeypatch):
    def nowhere():
        raise lasx_navigator.NavigatorUnavailable("the Navigator is not shown")

    monkeypatch.setattr(lasx_navigator, "find_clear_button", nowhere)
    lasx_navigator.forget()
    with pytest.raises(lasx_navigator.NavigatorUnavailable, match="not shown"):
        lasx_navigator.clear_canvas()


class NullPointer:
    """What comtypes returns when UI Automation finds nothing: not None, but false."""

    def __bool__(self):
        return False

    def FindFirst(self, scope, condition):
        raise ValueError("NULL COM pointer access")


CHILDREN, DESCENDANTS = "children", "descendants"
NAME, CLASS, AUTOMATION_ID, CONTROL_TYPE, BUTTON, TOGGLE = (
    "name",
    "class",
    "id",
    "type",
    "button",
    "toggle",
)


class StandInElement:
    """One element of LAS X's window: its properties and children; every search it answers is noted."""

    def __init__(self, searches, children=(), pattern=None, **properties):
        self.searches, self.children, self.pattern, self.properties = (
            searches,
            list(children),
            pattern,
            properties,
        )

    def matches(self, condition):
        if condition[0] == "and":
            return all(self.matches(part) for part in condition[1:])
        return self.properties.get(condition[1]) == condition[2]

    def below(self):
        for child in self.children:
            yield child
            yield from child.below()

    def FindFirst(self, scope, condition):
        self.searches.append(
            (self.properties.get(AUTOMATION_ID) or self.properties.get(CLASS), scope)
        )
        candidates = self.children if scope == CHILDREN else self.below()
        return next((e for e in candidates if e.matches(condition)), NullPointer())

    def GetCurrentPattern(self, pattern_id):
        return (
            self.pattern
            if pattern_id == TOGGLE and self.pattern is not None
            else NullPointer()
        )


class StandInToggle:
    """Navigator Expert's Experiment button: pressing it shows the canvas, if the window lets it."""

    def __init__(self, on, show):
        self.CurrentToggleState, self.show, self.pressed = int(on), show, 0

    def QueryInterface(self, interface):
        return self

    def Toggle(self):
        self.pressed += 1
        self.CurrentToggleState = 1
        self.show()


class StandInAutomation:
    TreeScope_Children, TreeScope_Descendants = CHILDREN, DESCENDANTS
    UIA_NamePropertyId, UIA_ClassNamePropertyId, UIA_AutomationIdPropertyId = (
        NAME,
        CLASS,
        AUTOMATION_ID,
    )
    UIA_ControlTypePropertyId, UIA_ButtonControlTypeId = CONTROL_TYPE, BUTTON
    UIA_TogglePatternId, IUIAutomationTogglePattern = (
        TOGGLE,
        "IUIAutomationTogglePattern",
    )

    def __init__(self, root):
        self.root = root

    def GetRootElement(self):
        return self.root

    def CreatePropertyCondition(self, prop, value):
        return ("is", prop, value)

    def CreateAndCondition(self, *parts):
        return ("and", *parts)


def las_x_window(searches, navigator_shown=True, canvas_appears=True):
    """LAS X's window as UI Automation shows it (simulator, 2026-10-14): the canvas and the Jobs/Experiment switch.

    Pressing Experiment puts the canvas into the window, unless *canvas_appears* is False.
    """

    def e(children=(), pattern=None, **props):
        return StandInElement(searches, children, pattern, **props)

    clear = e(**{CONTROL_TYPE: BUTTON, NAME: "Clear"})
    navigator = e(
        [e([e([clear], **{CLASS: "StageOverviewActionBar"})], **{CLASS: "TabItem"})],
        **{CLASS: "ViewStageOverview", AUTOMATION_ID: "Navigator"},
    )
    tabs = [
        e(
            [e([navigator], **{CLASS: "TabItem"})],
            **{CLASS: "TabControl", AUTOMATION_ID: "NavigatorTabControl"},
        )
    ]
    start_screen = e(
        tabs if navigator_shown else [e(**{CLASS: "Grid"})],
        **{CLASS: "TabItem", AUTOMATION_ID: "WorkflowStartScreen"},
    )

    def show():
        if canvas_appears:
            start_screen.children[:] = tabs

    experiment = StandInToggle(navigator_shown, show)
    jobs = StandInToggle(not navigator_shown, lambda: None)
    switch = e(
        [
            e(pattern=jobs, **{CONTROL_TYPE: BUTTON, NAME: "Jobs"}),
            e(pattern=experiment, **{CONTROL_TYPE: BUTTON, NAME: "Experiment"}),
        ],
        **{CLASS: "ModuleSwitchBetweenLDMAndMatrixView"},
    )
    top = e(
        [
            e(
                [
                    e(
                        [switch],
                        **{CLASS: "TabItem", AUTOMATION_ID: "TabLDMAndMatrixSwitch"},
                    )
                ],
                **{CLASS: "TabControl", AUTOMATION_ID: "Tab"},
            )
        ],
        **{CLASS: "ContainerTopView", AUTOMATION_ID: "TopModule"},
    )
    other_views = [
        e(
            [e(**{CONTROL_TYPE: BUTTON, NAME: "Clear"})],
            **{CLASS: "TabItem", AUTOMATION_ID: "Acquire"},
        )
    ]
    workflow = e(
        [start_screen, *other_views],
        **{CLASS: "TabControl", AUTOMATION_ID: "WorkFlowBar"},
    )
    form = e(
        [
            top,
            e(
                [workflow],
                **{CLASS: "ContainerLDMMatrixView", AUTOMATION_ID: "LDMMatrix"},
            ),
        ],
        **{CLASS: "MainFormView"},
    )
    main = e(
        [e([form], **{CLASS: "Pane", NAME: "AddIn"})],
        **{CLASS: "Window", NAME: "LAS X"},
    )
    return SimpleNamespace(
        root=e([main]), clear=clear, experiment=experiment, form=form
    )


def stand_in(monkeypatch, root):
    automation = StandInAutomation(root)
    monkeypatch.setattr(lasx_navigator, "automation", lambda: (automation, automation))
    lasx_navigator.forget()


ABOVE_THE_TABS = {
    None,
    "Window",
    "AddIn",
    "MainFormView",
    "LDMMatrix",
    "WorkFlowBar",
    "WorkflowStartScreen",
    "Pane",
    "TopModule",
}


def test_the_clear_button_is_found_along_the_navigators_path(monkeypatch):
    window = las_x_window([])
    stand_in(monkeypatch, window.root)
    assert lasx_navigator.find_clear_button().element is window.clear


def test_a_navigator_not_shown_is_unavailable_and_named(monkeypatch):
    stand_in(monkeypatch, las_x_window([], navigator_shown=False).root)
    with pytest.raises(
        lasx_navigator.NavigatorUnavailable,
        match="NavigatorTabControl.*is the Navigator shown",
    ):
        lasx_navigator.find_clear_button()


@pytest.mark.parametrize("navigator_shown", [True, False])
def test_the_window_is_never_searched_whole(monkeypatch, navigator_shown):
    # Searching all of LAS X's window for a view it does not show took 90 to
    # 158 s (simulator, 2026-10-14); down to the Navigator's tab control, and
    # to the Jobs/Experiment switch, the way is walked one child at a time.
    searches = []
    stand_in(monkeypatch, las_x_window(searches, navigator_shown).root)
    lasx_navigator.show_canvas()
    assert [s for s in searches if s[1] == DESCENDANTS and s[0] in ABOVE_THE_TABS] == []


def test_no_las_x_window_is_unavailable(monkeypatch):
    stand_in(monkeypatch, StandInElement([]))
    with pytest.raises(
        lasx_navigator.NavigatorUnavailable, match="LAS X's window was not found"
    ):
        lasx_navigator.find_clear_button()


class TestShowingTheCanvas:
    """Before a capture that will be released, the canvas is shown, so that its Clear can be pressed after."""

    def test_a_canvas_already_shown_is_left_as_it_is(self, monkeypatch):
        window = las_x_window([])
        stand_in(monkeypatch, window.root)
        assert lasx_navigator.show_canvas() is False
        assert window.experiment.pressed == 0

    def test_another_tab_shown_presses_experiment_and_arrives_at_the_canvas(
        self, monkeypatch
    ):
        window = las_x_window([], navigator_shown=False)
        stand_in(monkeypatch, window.root)
        assert lasx_navigator.show_canvas() is True
        assert window.experiment.pressed == 1
        assert lasx_navigator.find_clear_button().element is window.clear

    def test_arriving_is_the_clear_button_reached_not_the_switch_pressed(
        self, monkeypatch
    ):
        # Experiment pressed, but the canvas never comes: unavailable after the window, said in words.
        monkeypatch.setattr(lasx_navigator.tuning, "WINDOW_S", 0.05)
        window = las_x_window([], navigator_shown=False, canvas_appears=False)
        stand_in(monkeypatch, window.root)
        with pytest.raises(
            lasx_navigator.NavigatorUnavailable, match="Experiment was pressed.*canvas"
        ):
            lasx_navigator.show_canvas()
        assert window.experiment.pressed == 1

    def test_no_switch_is_unavailable_and_named(self, monkeypatch):
        window = las_x_window([], navigator_shown=False)
        window.form.children.pop(
            0
        )  # Navigator Expert's Jobs/Experiment bar is not there
        stand_in(monkeypatch, window.root)
        with pytest.raises(lasx_navigator.NavigatorUnavailable, match="Experiment"):
            lasx_navigator.show_canvas()
