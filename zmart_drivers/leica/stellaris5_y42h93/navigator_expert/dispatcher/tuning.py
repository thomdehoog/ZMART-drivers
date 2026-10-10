"""How the driver waits on LAS X: the one rule and its four numbers.

Every wait on LAS X follows the same rule, whether it is a reading, the
confirmation of a command, or the delivery of a message:

- **Four windows of three seconds** (``WINDOWS`` x ``WINDOW_S``). Within a
  window the driver watches both sources, the CAM API and the LAS X log, and
  the first answer that counts as a success wins, from whichever source gives
  it. Between windows something is sent again: a command is fired again, a
  read that got no answer is requested again, a message is delivered again.
  After four windows a reading is unknown, a change is unconfirmed, a
  delivery has failed.
- **Look again every** ``POLL_S``: a new look at the source that shows the
  state, a new read request to the API or a new read of the LAS X log. One
  read of the log took 64-71 ms (median 66 ms over 30 reads) on the LAS X
  simulator PC on 2026-10-10, and one API read 60-80 ms, so looking faster
  gains little and keeps a CPU core busy re-reading the log. To be confirmed
  on the microscope PC.
- **Check for the answer every** ``ANSWER_POLL_S``: whether a request already
  sent has been answered, a field on the local API object. At ``POLL_S`` a
  65 ms answer would only be noticed at 100 ms.

Acquisition is the one command that is never sent again (a second acquire is
a second acquisition); it keeps its own watch in ``ACQUIRE``. Waits on a
physical process, such as a file being written, are not waits on LAS X and
keep their own numbers.

Runtime consumers read these at call time (``tuning.WINDOW_S``), so a test
can shrink them with monkeypatch. ``actions.profiles`` captures them into
dataclass field defaults at import.
"""

WINDOWS = 4
WINDOW_S = 3
POLL_S = 0.1
ANSWER_POLL_S = 0.01
