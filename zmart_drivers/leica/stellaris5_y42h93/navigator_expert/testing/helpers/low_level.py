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
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.procedures.acquisition.run_job import (
    AcquisitionResult,
    acquire,
)
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
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_specific.primitive_handling.confirmations.confirmations import (
    _readback,
)
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_specific.primitive_handling.profiles.error_classification import (
    _check_api_error,
    _default_error_check,
    _is_transient_error,
)
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_specific.primitive_handling.readings.parsing import (
    _safe_float,
    make_changeable_copy,
    parse_format,
    parse_tile_geometry,
)
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_specific.primitive_handling.readings.state import (
    Reading,
    get_base_fov,
    get_fov,
    get_hardware_info,
    get_job_by_name,
    get_job_settings,
    get_jobs,
    get_lasx_settings,
    get_pending_dialog,
    get_scan_status,
    get_selected_job,
    get_xy,
    ping,
    read_zwide_um,
)
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_specific.primitive_handling.settings.changes import (
    move_xy,
    move_z,
    set_detector_gain,
    set_filter_wheel_slot,
    set_filter_wheel_spectrum,
    set_frame_accumulation,
    set_frame_average,
    set_image_format,
    set_laser_intensity,
    set_laser_shutter,
    set_line_accumulation,
    set_line_average,
    set_pinhole_airy,
    set_scan_field_rotation,
    set_scan_mode,
    set_scan_resonant,
    set_scan_speed,
    set_sequential_mode,
    set_z_stack_definition,
    set_z_stack_size,
    set_z_stack_step_size,
    set_zoom,
)
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_specific.primitive_handling.settings.limit_rules import (
    _stage_limits,
    apply_stage_limits_from_config,
    check_xy,
    check_z,
    get_stage_limits,
    set_stage_limits,
)
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_specific.primitives.capture.product import (
    AcquisitionMetadata,
    ChannelMetadata,
    PlaneIndex,
    PlaneSource,
    PositionIndex,
    VendorMetadataSource,
)
