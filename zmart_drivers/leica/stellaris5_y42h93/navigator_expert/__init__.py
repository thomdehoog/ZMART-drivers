# ruff: noqa: E402,I001,F401
"""Navigator Expert driver for Leica LAS X.

Package layout, the anatomy of a ZMART driver (``docs/driver-anatomy.md`` in
this repository)::

    navigator_expert/
    - for_zmart_controller/  the ZmartDriver class the controller makes (zmart_driver.py), one
                          method per controller command
    - vendor_specific/    everything that is specific to LAS X: the CAM API runtime, the
                          readers, the parsers, the sorting of LAS X's errors, the single
                          commands (changes.py) and primitive_handling/, which says how each
                          command is treated
    - dispatcher/         the machinery that runs a command safely, and names no LAS X
                          object: the one door (read and write), the reader, the send and
                          confirm loops and the limits gate
    - procedures/         the recipes built on the commands: the session, the frame and its
                          coordinates (absolute_coordinate_system/), changing the objective
                          (objective_change_with_compensation/), taking a picture (acquisition/), saving it
                          (save_data/) and letting LAS X's cache go (clear_cache/)
    - driver_setup/       what is set up once per microscope, one folder per item: limits,
                          origin, orientation, optical_calibration and
                          data_location, each with its defaults, loader, measuring code
                          and notebook; plus the store that finds the saved values
    - running_log.py      the driver's rolling log file
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
    # acquisition workflow
    "AcquisitionResult",
    "PlaneIndex",
    "PositionIndex",
    "SavedAcquisition",
    "save",
]

# -- parsing + command mechanics
from .vendor_specific.primitive_handling.readings.parsing import (
    _safe_float,
    make_changeable_copy,
    parse_format,
    parse_tile_geometry,
)
from .dispatcher.door import make_log_entry as _make_log_entry
from .vendor_specific.primitive_handling.profiles.error_classification import (
    _is_transient_error,
    _check_api_error,
    _default_error_check,
)
from .vendor_specific.primitive_handling.readings.state import Reading, get_scan_status, ping, get_job_settings, get_hardware_info, get_xy, read_zwide_um, get_jobs, get_job_by_name, get_selected_job, get_fov, get_base_fov, get_lasx_settings, get_pending_dialog
from .vendor_specific.primitive_handling.confirmations.confirmations import _readback
from .vendor_specific.primitive_handling.settings.changes import set_zoom, set_scan_speed, set_scan_resonant, set_scan_mode, set_sequential_mode, set_scan_field_rotation, set_image_format, set_z_stack_definition, set_z_stack_step_size, set_z_stack_size, set_frame_accumulation, set_frame_average, set_line_accumulation, set_line_average, set_pinhole_airy, set_detector_gain, set_laser_intensity, set_laser_shutter, set_filter_wheel_slot, set_filter_wheel_spectrum, move_xy, move_z
from .procedures.objective_change_with_compensation.compensated_changes import set_objective, select_job
from .vendor_specific.connection.lasx_client import connect_python_client

# -- driver_setup/limits/handshake.py - the connect-time limits handshake
from .driver_setup.limits.handshake import connect_handshake as connect_limits_handshake

# -- vendor_specific/primitive_handling/settings/limit_rules.py - the rulebook: stage checks + the compiled limits document
from .vendor_specific.primitive_handling.settings.limit_rules import _stage_limits, set_stage_limits, get_stage_limits, apply_stage_limits_from_config, check_xy, check_z

# -- acquisition/ - capture, file arrival, and save handling
from .procedures.acquisition.run_job import AcquisitionResult, acquire
from .vendor_specific.primitives.capture.product import (
    AcquisitionMetadata,
    ChannelMetadata,
    PlaneIndex,
    PlaneSource,
    PositionIndex,
    VendorMetadataSource,
)
from .procedures.save_data.saved_acquisition import SavedAcquisition
from .procedures.save_data.save import save

# -- logging
import logging

log = logging.getLogger(__name__)
