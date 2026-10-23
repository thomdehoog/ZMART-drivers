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

from zmart_drivers.leica.stellaris.navigator_expert import __version__
from zmart_drivers.leica.stellaris.navigator_expert.dispatcher.dispatch import make_log_entry as _make_log_entry
from zmart_drivers.leica.stellaris.navigator_expert.driver_setup.limits.handshake import (
    connect_handshake as connect_limits_handshake,
)

from zmart_drivers.leica.stellaris.navigator_expert.procedures.keep_in_view import (
    select_job,
    set_objective,
)
from zmart_drivers.leica.stellaris.navigator_expert.procedures.save_data.save import save
from zmart_drivers.leica.stellaris.navigator_expert.procedures.save_data.product import SavedAcquisition
from zmart_drivers.leica.stellaris.navigator_expert.primitives.cam_api.client import connect_python_client

from zmart_drivers.leica.stellaris.navigator_expert.primitives.cam_api.error_classification import (
    _check_api_error,
    _default_error_check,
    _is_transient_error,
)
from zmart_drivers.leica.stellaris.navigator_expert.primitives.cam_api.parsing import (
    _safe_float,
    make_changeable_copy,
    parse_format,
    parse_tile_geometry,
)
from zmart_drivers.leica.stellaris.navigator_expert.procedures.readings import Reading
from zmart_drivers.leica.stellaris.navigator_expert.procedures.readings import get_base_fov
from zmart_drivers.leica.stellaris.navigator_expert.procedures.readings import get_fov
from zmart_drivers.leica.stellaris.navigator_expert.procedures.readings import get_hardware_info
from zmart_drivers.leica.stellaris.navigator_expert.procedures.readings import get_job_by_name
from zmart_drivers.leica.stellaris.navigator_expert.procedures.readings import get_job_settings
from zmart_drivers.leica.stellaris.navigator_expert.procedures.readings import get_jobs
from zmart_drivers.leica.stellaris.navigator_expert.procedures.readings import get_lasx_settings
from zmart_drivers.leica.stellaris.navigator_expert.procedures.readings import get_pending_dialog
from zmart_drivers.leica.stellaris.navigator_expert.procedures.readings import get_scan_status
from zmart_drivers.leica.stellaris.navigator_expert.procedures.readings import get_selected_job
from zmart_drivers.leica.stellaris.navigator_expert.procedures.readings import get_xy
from zmart_drivers.leica.stellaris.navigator_expert.procedures.readings import ping
from zmart_drivers.leica.stellaris.navigator_expert.procedures.readings import read_zwide_um


from zmart_drivers.leica.stellaris.navigator_expert.procedures.changes import take_picture
from zmart_drivers.leica.stellaris.navigator_expert.procedures.changes import change as change_setting
from zmart_drivers.leica.stellaris.navigator_expert.procedures.changes import move_xy, move_z
from zmart_drivers.leica.stellaris.navigator_expert.driver_setup.limits.limit_rules import (
    _stage_limits,
    apply_stage_limits_from_config,
    check_xy,
    check_z,
    get_stage_limits,
    set_stage_limits,
)
from zmart_drivers.leica.stellaris.navigator_expert.procedures.save_data.product import AcquisitionResult
from zmart_drivers.leica.stellaris.navigator_expert.procedures.save_data.product import (
    AcquisitionMetadata,
    ChannelMetadata,
    PlaneIndex,
    PlaneSource,
    PositionIndex,
    VendorMetadataSource,
)
