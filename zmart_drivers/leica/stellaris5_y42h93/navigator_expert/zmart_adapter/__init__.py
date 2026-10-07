"""ZMART controller adapter package.

The implementation lives in :mod:`.zmart_adapter`; this file only re-exports
the public surface. The controller is handed the driver module,
``driver.py`` in the driver folder (see the module docstring for the usage
example).

Tests and validators that patch the adapter's driver seams (``_session``,
``_readers``, ...) import the implementation module directly::

    from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.zmart_adapter import zmart_adapter as adapter
"""

from .zmart_adapter import (  # noqa: F401 -- re-exported public surface
    CONNECTION,
    ZmartHandle,
    acquire,
    connect,
    disconnect,
    get_acquisition_settings,
    get_actuators,
    get_info,
    get_procedures,
    get_state,
    get_xyz,
    run_procedure,
    set_origin,
    set_state,
    set_xyz,
)
