"""The direct route: a client the driver's commands run on unchanged.

``DirectRoute(client)`` stands in for the CAM client. Job settings,
hardware info and stage XY are read from the hardware server (``reads.py``);
the job list, the scan status and the ping pass through to the CAM client,
having no hardware-server form seen yet. Each CAM command
object is replaced by a :class:`DirectCommand` that delivers to LAS X's
hardware server; a command with no verified translation
is refused at delivery, so nothing silently falls back to the CAM API. The
route answers to the same limits gate and session config as the CAM client:
it is the same instrument, reached another way.
"""

from __future__ import annotations

from types import SimpleNamespace

from ..configuration import session_state
from ..dispatcher import gate
from ..vendor_interface.hardware_server import HardwareServerUnavailable
from ..vendor_interface.state_tree import Job, job_sequence
from .command import DirectCommand, accepted, answer_text
from .reads import read_objects
from .translations import ENUM_FIELDS, TRANSLATIONS, NoTranslation

#: CAM read objects the route passes through; they never change the instrument.
CAM_READS = frozenset(
    {"PyApiStatus", "PyApiPing", "PyApiImagePathItem", "PyApiGetJobsInformation"}
)


class DirectRoute:
    """A client whose commands go to LAS X's hardware server instead of the CAM API."""

    def __init__(self, client):
        self.cam_client = client
        self.PyApiCommandEcho = SimpleNamespace(
            Model=SimpleNamespace(HasError=False, Error="", Result=1)
        )
        self.sent = []
        self.enum_types = {}
        self.known_ids = {}
        for name, read in read_objects(self).items():
            setattr(self, name, read)
        gate.share(client, self)
        config = session_state.get(client)
        if config is not None:
            session_state.install(self, config)

    def __getattr__(self, name):
        attribute = getattr(self.cam_client, name)
        if not name.startswith("PyApi") or name in CAM_READS:
            return attribute
        return DirectCommand(
            name,
            attribute,
            TRANSLATIONS.get(name),
            self,
            self.enum_types_of(name, attribute.Model),
        )

    def enum_types_of(self, name, model) -> dict:
        """The enum types of a CAM model's fields, kept from the first look, before anything wrote them."""
        if name not in self.enum_types:
            self.enum_types[name] = {
                field: type(getattr(model, field))
                for field in ENUM_FIELDS
                if hasattr(model, field) and type(getattr(model, field)) is not int
            }
        return self.enum_types[name]

    def job(self, job_name) -> Job:
        """The job's live ids, from the hardware server's state tree, looked up now.

        From the tree, not the CAM API's job list: after an experiment load
        LAS X gives every job a new id, and the CAM API's list can keep the
        old ones (2026-10-13: CAM 145, LAS X 152 for HiRes). Every write asks
        afresh, because a write's answer does not name the job, so a
        remembered id could not be checked.
        """
        try:
            found = job_sequence().jobs.get(job_name)
        except HardwareServerUnavailable as why:
            raise NoTranslation(f"no job ids from LAS X's state tree: {why}") from why
        if found is None:
            raise NoTranslation(f"no job {job_name!r} in LAS X's job sequence")
        self.known_ids[job_name] = found.block_id
        return found

    def block_id(self, job_name) -> int:
        """The job's id in LAS X, the hardware server's ``BlockId``."""
        return self.job(job_name).block_id

    def element_id(self, job_name) -> int:
        """The job's element in LAS X's job sequence, by which a job is selected."""
        return self.job(job_name).element_id

    def sequence_id(self) -> int:
        """The job sequence's own block id."""
        try:
            return job_sequence().block_id
        except HardwareServerUnavailable as why:
            raise NoTranslation(
                f"no job sequence from LAS X's state tree: {why}"
            ) from why

    def read_ids(self, job_name):
        """Ids to try for a read: the one last seen for the job, then a fresh one.

        A read's answer names its job, so the reader can check the id it used
        and ask again with a fresh one when the answer is for another job.
        """
        known = self.known_ids.get(job_name)
        if known is not None:
            yield known
        fresh = self.block_id(job_name)
        if fresh != known:
            yield fresh

    def answered(self, xml, answer):
        """Record LAS X's answer in the echo, as the CAM API records its own."""
        self.sent.append((xml, answer))
        echo = self.PyApiCommandEcho.Model
        if accepted(answer):
            echo.HasError, echo.Error, echo.Result = False, answer_text(answer), 1
        else:
            echo.HasError, echo.Error, echo.Result = (
                True,
                answer_text(answer) or answer,
                2,
            )


def route(client) -> DirectRoute:
    """The direct route for *client*: the same commands, sent to the hardware server."""
    return DirectRoute(client)
