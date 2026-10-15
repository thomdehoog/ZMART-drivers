"""
Unit tests: LAS X's warning dialogs, noticed in its log and answered with OK.
================================================================================
A warning LAS X's hardware server raises is shown by LAS X's window as a
dialog, and the server waits until it is answered: on 2026-10-13 one
("Please keep minimum distance of 8nm") held it for 33 hours. The window
writes each dialog to ``LAS X.log`` the moment it opens (``MessageBox
Warning : <text>``) and its answer when it closes (``MessageBox Result:
OK``). On the simulator (2026-10-14) the dialog was a second top-level
window of LAS X's process holding ``MessageBoxContent`` and one button,
``ButtonMessageOk``.

The driver answers a warning whose only button is OK, records its text, and
answers nothing else: a dialog with any other button stays open for the
operator, and the command that met it is refused with the dialog's text.
"""

import logging
import threading
import time

from types import SimpleNamespace

import pytest

from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_interface import (
    lasx_dialogs,
)

SHOWN = (
    "2026-10-14 09:49:33.502 0007  Info DbTracer 'MessageBox Warning : "
    "Please keep minimum distance of 8nm. '\n"
)
ANSWERED = "2026-10-14 09:50:46.635 0007  Info DbTracer 'MessageBox Result: OK'\n"
QUIET = "2026-10-14 09:49:30.000 0001  Info SWP.Command '19202. Do Command:DC Update'\n"


class StandInDialog:
    """One of LAS X's dialog windows: its buttons; pressing one writes the answer to the log."""

    def __init__(self, log, buttons=("ButtonMessageOk",)):
        self.log = log
        self.buttons = tuple(buttons)
        self.pressed = []

    def press(self, button):
        self.pressed.append(button)
        with self.log.open("a", encoding="latin-1") as f:
            f.write(ANSWERED)


class StandInWindows:
    def __init__(self, dialogs=()):
        self.open = list(dialogs)
        self.looks = 0

    def dialogs(self):
        self.looks += 1
        return [d for d in self.open if not d.pressed]


@pytest.fixture
def lasx_log(tmp_path, monkeypatch):
    path = tmp_path / "LAS X.log"
    path.write_text(QUIET, encoding="latin-1")
    monkeypatch.setattr(lasx_dialogs, "LASX_LOG", path)
    lasx_dialogs.forget()
    yield path
    lasx_dialogs.forget()


def show(path, line=SHOWN):
    with path.open("a", encoding="latin-1") as f:
        f.write(line)


def use(monkeypatch, windows):
    monkeypatch.setattr(lasx_dialogs, "lasx_windows", lambda: windows)
    return windows


class TestNoticing:
    def test_without_a_dialog_in_the_log_no_window_is_looked_at(self, lasx_log, monkeypatch):
        windows = use(monkeypatch, StandInWindows())
        assert lasx_dialogs.answer_warnings() is None
        assert windows.looks == 0

    def test_a_dialog_shown_and_answered_is_not_open(self, lasx_log):
        show(lasx_log)
        show(lasx_log, ANSWERED)
        assert lasx_dialogs.pending() is None

    def test_a_dialog_shown_and_not_answered_is_open_with_its_text(self, lasx_log):
        show(lasx_log)
        dialog = lasx_dialogs.pending()
        assert (dialog.caption, dialog.text, dialog.at) == (
            "Warning",
            "Please keep minimum distance of 8nm.",
            "2026-10-14 09:49:33.502",
        )

    def test_only_what_the_log_gained_is_read_again(self, lasx_log):
        assert lasx_dialogs.pending() is None
        show(lasx_log)
        assert lasx_dialogs.pending().text == "Please keep minimum distance of 8nm."
        show(lasx_log, ANSWERED)
        assert lasx_dialogs.pending() is None

    def test_a_new_log_after_the_old_one_was_moved_aside_is_read_from_its_start(self, lasx_log):
        show(lasx_log, QUIET * 50)
        assert lasx_dialogs.pending() is None
        lasx_log.write_text(SHOWN, encoding="latin-1")
        assert lasx_dialogs.pending().text == "Please keep minimum distance of 8nm."

    def test_no_log_means_no_dialog(self, lasx_log):
        lasx_log.unlink()
        assert lasx_dialogs.pending() is None


class TestAnswering:
    def test_a_warning_with_only_ok_is_answered_and_its_text_logged(self, lasx_log, monkeypatch, caplog):
        show(lasx_log)
        dialog = StandInDialog(lasx_log)
        use(monkeypatch, StandInWindows([dialog]))
        with caplog.at_level(logging.WARNING):
            answered = lasx_dialogs.answer_warnings()
        assert dialog.pressed == ["ButtonMessageOk"]
        assert answered.dialog.text == "Please keep minimum distance of 8nm."
        assert "Please keep minimum distance of 8nm." in answered.message
        assert "Please keep minimum distance of 8nm." in caplog.text
        assert lasx_dialogs.pending() is None

    def test_an_information_box_with_only_ok_is_answered_and_its_text_logged(self, lasx_log, monkeypatch, caplog):
        """LAS X refuses a start with an Information box that waits for OK (simulator, 2026-10-15)."""
        show(lasx_log, "2026-10-15 20:04:26.262 0009  Info DbTracer 'MessageBox Information : Can not start scan!\n"
                       "(Reason: No channel active) '\n")
        dialog = StandInDialog(lasx_log)
        use(monkeypatch, StandInWindows([dialog]))
        with caplog.at_level(logging.WARNING):
            answered = lasx_dialogs.answer_warnings()
        assert dialog.pressed == ["ButtonMessageOk"]
        assert answered.dialog.caption == "Information"
        assert "Can not start scan!" in answered.dialog.text and "No channel active" in answered.dialog.text
        assert "No channel active" in caplog.text
        assert "Information" in answered.message

    def test_a_dialog_with_another_button_is_left_open_and_refused(self, lasx_log, monkeypatch, caplog):
        show(lasx_log)
        dialog = StandInDialog(lasx_log, buttons=("ButtonMessageOk", "ButtonMessageCancel"))
        use(monkeypatch, StandInWindows([dialog]))
        with caplog.at_level(logging.WARNING), pytest.raises(lasx_dialogs.DialogOpen, match="8nm"):
            lasx_dialogs.answer_warnings()
        assert dialog.pressed == []
        assert "Please keep minimum distance of 8nm." in caplog.text

    def test_a_question_is_never_answered(self, lasx_log, monkeypatch):
        show(lasx_log, SHOWN.replace("MessageBox Warning", "MessageBox Question"))
        dialog = StandInDialog(lasx_log)
        use(monkeypatch, StandInWindows([dialog]))
        with pytest.raises(lasx_dialogs.DialogOpen, match="Question"):
            lasx_dialogs.answer_warnings()
        assert dialog.pressed == []

    def test_two_dialogs_at_once_are_left_open(self, lasx_log, monkeypatch):
        show(lasx_log)
        first, second = StandInDialog(lasx_log), StandInDialog(lasx_log)
        use(monkeypatch, StandInWindows([first, second]))
        with pytest.raises(lasx_dialogs.DialogOpen):
            lasx_dialogs.answer_warnings()
        assert first.pressed == second.pressed == []

    def test_a_dialog_answered_by_hand_meanwhile_is_left_alone(self, lasx_log, monkeypatch):
        show(lasx_log)
        use(monkeypatch, StandInWindows([]))
        threading.Timer(0.1, lambda: show(lasx_log, ANSWERED)).start()
        assert lasx_dialogs.answer_warnings() is None

    def test_ok_pressed_but_never_recorded_is_refused(self, lasx_log, monkeypatch):
        show(lasx_log)
        dialog = StandInDialog(lasx_log)
        dialog.press = lambda button: dialog.pressed.append(button)  # the click goes nowhere
        use(monkeypatch, StandInWindows([dialog]))
        monkeypatch.setattr(lasx_dialogs, "ANSWER_WAIT_S", 0.2)
        with pytest.raises(lasx_dialogs.DialogOpen, match="did not close"):
            lasx_dialogs.answer_warnings()

    def test_without_windows_automation_the_dialog_is_refused_not_guessed(self, lasx_log, monkeypatch):
        show(lasx_log)

        def unavailable():
            raise lasx_dialogs.WindowsUnavailable("no UI Automation here")

        monkeypatch.setattr(lasx_dialogs, "lasx_windows", unavailable)
        with pytest.raises(lasx_dialogs.DialogOpen, match="no UI Automation here"):
            lasx_dialogs.answer_warnings()


class TestWhileASendWaits:
    """A command sent directly to the hardware server does not return while
    the dialog it caused is open; a watcher answers it meanwhile."""

    def test_a_warning_that_opens_during_the_send_is_answered(self, lasx_log, monkeypatch):
        dialog = StandInDialog(lasx_log)
        use(monkeypatch, StandInWindows([dialog]))
        monkeypatch.setattr(lasx_dialogs, "WATCH_INTERVAL_S", 0.01)
        released = threading.Event()

        def blocked_send():
            show(lasx_log)
            for _ in range(200):
                if dialog.pressed:
                    released.set()
                    return
                time.sleep(0.01)

        with lasx_dialogs.watching() as notes:
            blocked_send()
        assert released.is_set()
        assert [n.dialog.text for n in notes] == ["Please keep minimum distance of 8nm."]

    def test_the_watcher_stops_with_the_send(self, lasx_log, monkeypatch):
        use(monkeypatch, StandInWindows())
        before = threading.active_count()
        with lasx_dialogs.watching():
            pass
        time.sleep(0.05)
        assert threading.active_count() == before


class TestCommands:
    """Every write, on either route, first answers an OK-only warning and is
    refused while another dialog is open; what was answered is in its result."""

    @pytest.fixture
    def client(self):
        from limits_fixtures import install_permissive_limits
        from mock_lasx_api import MockLasxClient

        return install_permissive_limits(MockLasxClient(), wide_stage=True)

    def test_a_setting_waits_for_no_open_dialog_and_says_why(self, client, lasx_log, monkeypatch):
        from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.actions import set as cam

        show(lasx_log)
        use(monkeypatch, StandInWindows([StandInDialog(lasx_log, buttons=("ButtonMessageYes", "ButtonMessageNo"))]))
        before = client._jobs["HiRes"]["zoom"]["current"]
        result = cam.set_zoom(client, "HiRes", 5.0)
        assert result["success"] is False
        assert "Please keep minimum distance of 8nm." in result["message"]
        assert client._jobs["HiRes"]["zoom"]["current"] == before

    def test_a_setting_answers_an_ok_only_warning_first_and_reports_it(self, client, lasx_log, monkeypatch):
        from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.actions import set as cam

        show(lasx_log)
        dialog = StandInDialog(lasx_log)
        use(monkeypatch, StandInWindows([dialog]))
        result = cam.set_zoom(client, "HiRes", 5.0)
        assert (result["success"], result["confirmed"]) == (True, True)
        assert dialog.pressed == ["ButtonMessageOk"]
        assert any("Please keep minimum distance of 8nm." in entry["msg"] for entry in result["logs"])

    def test_an_acquire_is_not_started_into_an_open_dialog(self, client, lasx_log, monkeypatch):
        from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.actions import set as cam

        show(lasx_log)
        use(monkeypatch, StandInWindows([StandInDialog(lasx_log, buttons=("ButtonMessageOk", "ButtonMessageCancel"))]))
        result = cam.acquire(client, "HiRes")
        assert result["success"] is False
        assert "8nm" in result["message"]

    def test_an_experiment_save_is_not_sent_into_an_open_dialog(self, client, lasx_log, monkeypatch, tmp_path):
        from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.scanfields import files

        show(lasx_log)
        use(monkeypatch, StandInWindows([StandInDialog(lasx_log, buttons=("ButtonMessageOk", "ButtonMessageCancel"))]))
        sent = []
        client.PyApiSaveExperiment.UpdateAwaitReceipt = lambda timeout: sent.append(1) or True
        assert files.save_experiment(client, "x.xml", tmp_path, timeout=0.2) is None
        assert sent == []


class TestComInThisThread:
    """The driver's thread has COM set up already, by pywin32 and the CAM API,
    in a mode of their own; UI Automation works in it as it is (simulator,
    2026-10-14: insisting on its own mode failed with "Cannot change thread
    mode after it is set", and the warning stayed open)."""

    def test_com_set_up_in_another_mode_is_used_as_it_is(self):
        def already_set_up():
            raise OSError(0, "Cannot change thread mode after it is set", None, lasx_dialogs.RPC_E_CHANGED_MODE)

        lasx_dialogs.initialise_com(already_set_up)

    def test_another_com_failure_is_not_hidden(self):
        def broken():
            raise OSError(0, "Class not registered", None, -2147221164)

        with pytest.raises(OSError, match="Class not registered"):
            lasx_dialogs.initialise_com(broken)

    def test_comtypes_is_imported_in_the_mode_the_thread_has(self, monkeypatch):
        import sys

        seen = {}

        def fake_import():
            seen["flags"] = getattr(sys, "coinit_flags", None)

        monkeypatch.setattr(lasx_dialogs, "apartment", lambda: lasx_dialogs.APTTYPE_MTA)
        monkeypatch.delattr(sys, "coinit_flags", raising=False)
        lasx_dialogs.import_in_this_threads_mode(fake_import)
        assert seen["flags"] == lasx_dialogs.COINIT_MULTITHREADED
        assert not hasattr(sys, "coinit_flags")  # set only for the import

    def test_a_thread_without_com_lets_comtypes_choose(self, monkeypatch):
        import sys

        seen = {}
        monkeypatch.setattr(lasx_dialogs, "apartment", lambda: None)
        monkeypatch.delattr(sys, "coinit_flags", raising=False)
        lasx_dialogs.import_in_this_threads_mode(lambda: seen.setdefault("flags", getattr(sys, "coinit_flags", None)))
        assert seen["flags"] is None


class TestNavigatorExpertsOwnWarnings:
    """Navigator Expert's warnings go through LAS X's window too, logged over
    several lines, the caption with an exclamation mark (simulator,
    2026-10-14, when a template run refused to start)."""

    MULTILINE = (
        "2026-10-14 18:20:47.050 0007  Info DbTracer ''\n"
        "2026-10-14 18:20:47.050 0007  Info DbTracer 'MessageBox Warning! I Cannot Start The Scan: \n"
        "       Problem:\t Some of the measured focus points do not seem to be correct.\n"
        "    \n"
        "       Solution:\t Please select the AF job and re-scan the Focus measuring points again\n"
        "    '\n"
        "2026-10-14 18:20:47.050 0007  Info DbTracer 'MessageConfirmationAction: OK'\n"
    )

    def test_a_warning_over_several_lines_is_noticed_with_all_its_text(self, lasx_log):
        show(lasx_log, self.MULTILINE)
        dialog = lasx_dialogs.pending()
        assert dialog.caption == "Warning"
        assert dialog.text.startswith("I Cannot Start The Scan:")
        assert "re-scan the Focus measuring points again" in dialog.text
        assert dialog.at == "2026-10-14 18:20:47.050"

    def test_it_is_answered_like_any_ok_only_warning(self, lasx_log, monkeypatch):
        show(lasx_log, self.MULTILINE)
        dialog = StandInDialog(lasx_log)
        use(monkeypatch, StandInWindows([dialog]))
        answered = lasx_dialogs.answer_warnings()
        assert dialog.pressed == ["ButtonMessageOk"]
        assert "focus points" in answered.message

    def test_a_warning_whose_end_is_not_yet_written_waits_for_it(self, lasx_log):
        first, rest = self.MULTILINE.split("    \n", 1)
        show(lasx_log, first)
        assert lasx_dialogs.pending() is None
        show(lasx_log, "    \n" + rest)
        assert "re-scan" in lasx_dialogs.pending().text

    def test_a_long_log_is_read_in_one_pass(self, lasx_log):
        # LAS X.log grows to 20 MB before LAS X moves it aside; the first look reads all of it.
        show(lasx_log, QUIET * 200_000 + self.MULTILINE)
        started = time.perf_counter()
        assert "focus points" in lasx_dialogs.pending().text
        assert time.perf_counter() - started < 2


class TestEveryFormAndWindow:
    """LAS X logs a dialog by text ("MessageBox Warning : ...", the hardware
    server's), without the space before the colon ("MessageBox Warning: ..."), or as
    Navigator Expert's ("MessageBox Warning! ..."); and a dialog in the log
    is open until its window says otherwise."""

    def test_a_dialog_logged_without_the_space_is_noticed(self, lasx_log):
        show(lasx_log, "2026-10-14 19:00:00.000 0007  Info DbTracer 'MessageBox Warning: The stage is not initialized.'\n")
        assert (lasx_lasx := lasx_dialogs.pending()) is not None
        assert (lasx_lasx.caption, lasx_lasx.text) == ("Warning", "The stage is not initialized.")

    def test_a_dialog_in_the_log_whose_window_appears_late_is_answered(self, lasx_log, monkeypatch):
        show(lasx_log)
        dialog = StandInDialog(lasx_log)
        windows = StandInWindows([])
        monkeypatch.setattr(lasx_dialogs, "ANSWER_WAIT_S", 1.0)
        use(monkeypatch, windows)
        threading.Timer(0.2, lambda: windows.open.append(dialog)).start()
        assert lasx_dialogs.answer_warnings() is not None
        assert dialog.pressed == ["ButtonMessageOk"]

    def test_a_dialog_in_the_log_that_never_shows_is_left_open_and_refused(self, lasx_log, monkeypatch):
        show(lasx_log)
        monkeypatch.setattr(lasx_dialogs, "ANSWER_WAIT_S", 0.2)
        use(monkeypatch, StandInWindows([]))
        with pytest.raises(lasx_dialogs.DialogOpen, match="not found on screen"):
            lasx_dialogs.answer_warnings()


class TestWatchingAllTheTime:
    """While the driver is connected, a watcher answers OK-only warnings
    whatever the driver does: writing, only reading, or waiting."""

    def test_a_warning_while_the_driver_only_waits_is_answered(self, lasx_log, monkeypatch):
        dialog = StandInDialog(lasx_log)
        use(monkeypatch, StandInWindows([dialog]))
        monkeypatch.setattr(lasx_dialogs, "WATCH_INTERVAL_S", 0.01)
        lasx_dialogs.keep_watching()
        try:
            show(lasx_log)
            for _ in range(200):
                if dialog.pressed:
                    break
                time.sleep(0.01)
            assert dialog.pressed == ["ButtonMessageOk"]
        finally:
            lasx_dialogs.stop_watching()

    def test_watching_starts_once(self, lasx_log, monkeypatch):
        use(monkeypatch, StandInWindows())
        before = threading.active_count()
        lasx_dialogs.keep_watching()
        lasx_dialogs.keep_watching()
        try:
            assert threading.active_count() == before + 1
        finally:
            lasx_dialogs.stop_watching()
        time.sleep(0.3)
        assert threading.active_count() == before

    def test_a_dialog_left_for_the_operator_is_logged_once(self, lasx_log, monkeypatch, caplog):
        show(lasx_log, SHOWN.replace("MessageBox Warning", "MessageBox Question"))
        use(monkeypatch, StandInWindows([StandInDialog(lasx_log)]))
        monkeypatch.setattr(lasx_dialogs, "WATCH_INTERVAL_S", 0.01)
        with caplog.at_level(logging.WARNING):
            lasx_dialogs.keep_watching()
            time.sleep(0.2)
            lasx_dialogs.stop_watching()
        assert caplog.text.count("not answered") == 1


def test_presses_set_no_focus_so_las_x_stays_behind():
    """By default UI Automation sets focus before a press, which brings LAS X to the front (simulator,
    2026-10-15); the newer client with AutoSetFocus off presses without it (Microsoft, IUIAutomation2)."""
    made = {}

    def create(cls, interface):
        made.update(cls=cls, interface=interface)
        return SimpleNamespace(AutoSetFocus=True)

    client = SimpleNamespace(CUIAutomation8="CUIAutomation8", IUIAutomation2="IUIAutomation2")
    uia = lasx_dialogs.quiet_automation(client, create)
    assert made == {"cls": "CUIAutomation8", "interface": "IUIAutomation2"}
    assert uia.AutoSetFocus is False
