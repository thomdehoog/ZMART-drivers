"""The driver's low-level reads and commands, gathered for the hardware checks.

The hardware scripts (``validate_hardware.py`` and the ones built on it) test
the driver's reads and commands one by one, below the ``Microscope``. They hand
these around as one object, ``drv``, so that a test can hand in a stand-in
instead. This module is that object. It is for the checks only: a person or a
notebook uses ``navigator_expert.connect()``, which goes through the session,
the frame and the record of unconfirmed changes.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

# ruff: noqa: F401

from zmart_drivers.leica.stellaris5_y42h93.navigator_expert import __version__
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.dispatcher.door import (
    make_log_entry as _make_log_entry,
)
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.driver_setup.limits.handshake import (
    connect_handshake as connect_limits_handshake,
)
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_specific.primitive_handling.commands.run_job import AcquisitionResult
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_specific.primitive_handling.commands.run_job import acquire
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.procedures.objective_change_with_compensation.compensated_changes import (
    select_job,
    set_objective,
)
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.procedures.save_data.save import save
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.procedures.save_data.saved_acquisition import (
    SavedAcquisition,
)
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_specific.connection.lasx_client import (
    connect_python_client,
)
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_specific.primitive_handling.confirmations.confirm_changes import _readback
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_specific.primitive_handling.profiles.error_classification import (
    _check_api_error,
    _default_error_check,
    _is_transient_error,
)
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_specific.primitives.values.parsing import (
    _safe_float,
    make_changeable_copy,
    parse_format,
    parse_tile_geometry,
)
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_specific.primitive_handling.readings.named_readings import Reading
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_specific.primitive_handling.readings.named_readings import get_base_fov
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_specific.primitive_handling.readings.named_readings import get_fov
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_specific.primitive_handling.readings.named_readings import get_hardware_info
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_specific.primitive_handling.readings.named_readings import get_job_by_name
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_specific.primitive_handling.readings.named_readings import get_job_settings
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_specific.primitive_handling.readings.named_readings import get_jobs
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_specific.primitive_handling.readings.named_readings import get_lasx_settings
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_specific.primitive_handling.readings.named_readings import get_pending_dialog
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_specific.primitive_handling.readings.named_readings import get_scan_status
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_specific.primitive_handling.readings.named_readings import get_selected_job
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_specific.primitive_handling.readings.named_readings import get_xy
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_specific.primitive_handling.readings.named_readings import ping
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_specific.primitive_handling.readings.named_readings import read_zwide_um
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_specific.primitive_handling.commands.changes import move_xy
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_specific.primitive_handling.commands.changes import move_z
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_specific.primitive_handling.commands.changes import set_detector_gain
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_specific.primitive_handling.commands.changes import set_filter_wheel_slot
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_specific.primitive_handling.commands.changes import set_filter_wheel_spectrum
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_specific.primitive_handling.commands.changes import set_frame_accumulation
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_specific.primitive_handling.commands.changes import set_frame_average
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_specific.primitive_handling.commands.changes import set_image_format
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_specific.primitive_handling.commands.changes import set_laser_intensity
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_specific.primitive_handling.commands.changes import set_laser_shutter
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_specific.primitive_handling.commands.changes import set_line_accumulation
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_specific.primitive_handling.commands.changes import set_line_average
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_specific.primitive_handling.commands.changes import set_pinhole_airy
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_specific.primitive_handling.commands.changes import set_scan_field_rotation
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_specific.primitive_handling.commands.changes import set_scan_mode
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_specific.primitive_handling.commands.changes import set_scan_resonant
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_specific.primitive_handling.commands.changes import set_scan_speed
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_specific.primitive_handling.commands.changes import set_sequential_mode
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_specific.primitive_handling.commands.changes import set_z_stack_definition
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_specific.primitive_handling.commands.changes import set_z_stack_size
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_specific.primitive_handling.commands.changes import set_z_stack_step_size
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_specific.primitive_handling.commands.changes import set_zoom
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_specific.primitive_handling.commands.limit_rules import _stage_limits
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_specific.primitive_handling.commands.limit_rules import apply_stage_limits_from_config
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_specific.primitive_handling.commands.limit_rules import check_xy
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_specific.primitive_handling.commands.limit_rules import check_z
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_specific.primitive_handling.commands.limit_rules import get_stage_limits
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_specific.primitive_handling.commands.limit_rules import set_stage_limits
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_specific.primitives.capture.product import (
    AcquisitionMetadata,
    ChannelMetadata,
    PlaneIndex,
    PlaneSource,
    PositionIndex,
    VendorMetadataSource,
)
