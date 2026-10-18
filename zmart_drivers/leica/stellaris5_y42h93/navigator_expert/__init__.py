# ruff: noqa: E402,I001,F401
"""Navigator Expert driver for Leica LAS X.

Package layout, the anatomy of a ZMART driver (``docs/driver-anatomy.md`` in
this repository)::

    navigator_expert/
    - zmart_controller_plugin.py  the module handed to the ZMART Controller (to become zmart_driver.py)
    - zmart_adapter/      the functions behind it, one per controller command
    - connect.py          the connect flow: open LAS X, load this microscope's configuration
    - vendor_interface/   the only part that knows LAS X: the CAM API runtime, the API and
                          log readers, the parsers, and the sorting of LAS X's errors
    - dispatcher/         the engines that run an action safely: read.py, change.py, the
                          limits gate and rulebook, pre-checks, tuning
    - actions/            the readings (get.py) and the changes (set.py) with their
                          confirmations and profiles
    - procedures/         recipes built from actions: backlash, the setup measurements,
                          and the pure algorithms they use
    - output/             what comes out of one acquisition: LAS X export, OME, naming, save
    - configuration/      what is measured once per microscope: limits,
                          image_stage_registration, optical_calibration (each with its
                          defaults and notebook), the store, the per-connection state
    - testing/            the mock LAS X API, the offline suite, the hardware validators
"""

__version__ = "7.0.0rc1"

__all__ = [
    # logging
    "log",
    # parsing
    "parse_tile_geometry",
    # limits
    "set_stage_limits",
    "get_stage_limits",
    "apply_stage_limits_from_config",
    # limits handshake and command safety gate
    "connect_limits_handshake",
    # readers
    "Reading",
    "get_scan_status",
    "ping",
    "get_job_settings",
    "get_hardware_info",
    "get_xy",
    "read_zwide_um",
    "get_jobs",
    "get_job_by_name",
    "get_selected_job",
    "get_fov",
    "get_base_fov",
    "get_lasx_settings",
    "get_pending_dialog",
    # settings
    "make_changeable_copy",
    # commands
    "set_zoom",
    "set_scan_speed",
    "set_scan_resonant",
    "set_scan_mode",
    "set_sequential_mode",
    "set_scan_field_rotation",
    "set_image_format",
    "set_objective",
    "set_z_stack_definition",
    "set_z_stack_step_size",
    "set_z_stack_size",
    "set_frame_accumulation",
    "set_frame_average",
    "set_line_accumulation",
    "set_line_average",
    "set_pinhole_airy",
    "set_detector_gain",
    "set_laser_intensity",
    "set_laser_shutter",
    "set_filter_wheel_slot",
    "set_filter_wheel_spectrum",
    "move_xy",
    "move_z",
    # acquisition, not a command: acquire RAISES on failure and returns an
    # AcquisitionResult, never a result dict (see acquisition.capture)
    "acquire",
    "select_job",
    # session helpers
    "connect_python_client",
    "connect_microscope",
    # acquisition workflow
    "AcquisitionResult",
    "PlaneIndex",
    "PositionIndex",
    "SavedAcquisition",
    "save",
]

# -- parsing + command mechanics
from .vendor_interface.parsing import (
    _safe_float,
    make_changeable_copy,
    parse_format,
    parse_tile_geometry,
)
from .dispatcher.envelope import _make_log_entry
from .vendor_interface.error_classification import (
    _is_transient_error,
    _check_api_error,
    _default_error_check,
)
from .dispatcher.read import (
    Reading,
    get_scan_status,
    ping,
    get_job_settings,
    get_hardware_info,
    get_xy,
    read_zwide_um,
    get_jobs,
    get_job_by_name,
    get_selected_job,
    get_fov,
    get_base_fov,
    get_lasx_settings,
    get_pending_dialog,
)
from .actions.confirmations import _readback
from .actions.set import (
    set_zoom,
    set_scan_speed,
    set_scan_resonant,
    set_scan_mode,
    set_sequential_mode,
    set_scan_field_rotation,
    set_image_format,
    set_objective,
    set_z_stack_definition,
    set_z_stack_step_size,
    set_z_stack_size,
    set_frame_accumulation,
    set_frame_average,
    set_line_accumulation,
    set_line_average,
    set_pinhole_airy,
    set_detector_gain,
    set_laser_intensity,
    set_laser_shutter,
    set_filter_wheel_slot,
    set_filter_wheel_spectrum,
    move_xy,
    move_z,
    select_job,
)
from .connect import connect_python_client, connect_microscope

# -- commands/gate - command safety gate + connect handshake
from .dispatcher.gate import connect_handshake as connect_limits_handshake

# -- limits/checks.py - the rulebook: stage checks + the compiled limits document
from .dispatcher.checks import (
    _stage_limits,
    set_stage_limits,
    get_stage_limits,
    apply_stage_limits_from_config,
    check_xy,
    check_z,
)

# -- acquisition/ - capture, file arrival, and save handling
from .actions.acquire import AcquisitionResult, acquire
from .output.product import (
    AcquisitionMetadata,
    ChannelMetadata,
    PlaneIndex,
    PlaneSource,
    PositionIndex,
    SavedAcquisition,
    VendorMetadataSource,
)
from .output.save import save

# -- logging
import logging

log = logging.getLogger(__name__)
