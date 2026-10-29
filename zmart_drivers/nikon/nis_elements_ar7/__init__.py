"""The ZMART driver for a Nikon microscope on NIS-Elements AR 7.

Use the driver by itself with ``connect()``, which reaches NIS-Elements
through the bridge (a small program that runs inside NIS) and hands back a
``Microscope``; the README says what it can do. The ZMART Controller plugs
the driver in through ``zmart_controller_plugin/``, which is built on the same
``Microscope``.

The package is laid out as the anatomy of a ZMART driver describes
(``docs/driver-anatomy.md`` in this repository)::

    nis_elements_ar7/
    - __init__.py         connect() and Microscope, nothing else
    - methods.py          what the user calls: connect(), the Microscope, its Session, the running log
    - procedures/         chains of steps: the readings, the changes, the coordinates, acquire,
                          the state, and save_data/ (the staged TIFF into OME-Zarr)
    - dispatcher/         the same in every driver: run(), the read and write loops, the limits gate
    - primitives/         one folder for each way of reaching NIS: nis_python/, the bridge
    - driver_setup/       what is set up once per microscope: limits, origin, orientation,
                          data location, the pixel-size record, and the bridge's macro
    - zmart_controller_plugin/  what the controller sees: one method per controller command
    - testing/            the pretend NIS, the offline suite, the first checks at a microscope

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

__version__ = "0.1.0a1"

__all__ = ["Microscope", "__version__", "connect"]


def __getattr__(name):
    """``connect`` and ``Microscope``, loaded when first asked for.

    They need numpy and the OME-Zarr packages, which NIS-Elements' own Python
    does not have. Loading them only when asked lets that Python use the
    parts that need nothing installed, such as writing the bridge's macro
    (``python -m zmart_drivers.nikon.nis_elements_ar7.driver_setup.nis_bridge``).
    """
    if name in ("connect", "Microscope"):
        from . import methods

        return getattr(methods, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
