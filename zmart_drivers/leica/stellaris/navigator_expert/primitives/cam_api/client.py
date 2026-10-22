"""Open the LAS X client: connect, pace the API, check that it answers, and load its runtime.

This is the one place that talks to LAS X to start a session. The runtime
loader is imported where it is used, so this file can be imported on a
computer without LAS X.

How the driver opens the LAS X CAM API: the pacing and where the API is installed.

The connection reads ``LASX_API`` each time it opens, so a test or a
validator can hand in other settings by replacing it.

Load the Leica LAS X CAM API runtime from the licensed LAS X install.

The CAM API assemblies ship with LAS X under the NavigatorExpert add-in
directory, so the repo carries no Leica binaries and no Leica files are copied
into the Python env. ``pythonnet`` is still required as the Python/.NET bridge.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import logging
from typing import Any
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace


_log = logging.getLogger(__name__)


def configure_lasx_api_delay(
    lasx_api_module: Any, delay_ms: int | None = None
) -> int | None:
    """Set Leica's ``PyApiClient.DelayInMilliseconds`` pacing knob.

    ``delay_ms=None`` means "use the current profile value." A profile value
    of ``None`` disables explicit configuration.

    Returns the applied value, or ``None`` when disabled.
    """
    if delay_ms is None:
        delay_ms = LASX_API.delay_ms
    if delay_ms is None:
        return None

    pyapi_client = getattr(lasx_api_module, "PyApiClient", None)
    if pyapi_client is None:
        client_model = getattr(lasx_api_module, "LasxApiClientPyModel", None)
        pyapi_client = getattr(client_model, "PyApiClient", None)
    if pyapi_client is None:
        raise RuntimeError(
            "LasxApi.PyApiClient is unavailable; cannot configure DelayInMilliseconds"
        )

    try:
        pyapi_client.DelayInMilliseconds = int(delay_ms)
    except Exception as exc:  # noqa: BLE001 - .NET interop exceptions vary
        raise RuntimeError(
            "Could not set LasxApi.PyApiClient.DelayInMilliseconds"
        ) from exc
    return int(delay_ms)


def connect_python_client(
    client_name: str = "PythonClient", api_delay_ms: int | None = None
) -> Any:
    """Open the LAS X API client.

    The runtime loader is imported at the call site so this module
    stays import-safe on machines without LAS X. Pass a custom ``client_name`` if
    multiple python clients need to coexist. ``api_delay_ms`` overrides the
    ``LASX_API.delay_ms`` for this connection attempt.
    Whether LAS X then answers is the caller's check (``ping``), a reading
    the connection does not make.

    Raises ``ConnectionError`` if the connect call returns False.
    """

    _lasx_api = load_lasx_api_runtime()
    client = _lasx_api.LasxApiClientPyModel
    if not client.Connect(client_name):
        raise ConnectionError("Cannot connect to LAS X. Is it running?")
    applied_delay = configure_lasx_api_delay(_lasx_api, api_delay_ms)
    _log.info(
        "LAS X runtime=%s version=%s api_delay_ms=%s",
        getattr(_lasx_api, "base_path", "unknown"),
        getattr(_lasx_api, "__version__", "unknown"),
        "profile-disabled" if applied_delay is None else applied_delay,
    )
    return client


@dataclass(frozen=True)
class LasxApiProfile:
    """Connection-level LAS X API settings.

    ``DelayInMilliseconds`` is Leica's client-side pacing knob. Keeping it in
    the profile makes the default explicit and keeps hardware-specific API
    timing out of scripts and workflows. ``runtime_root`` is the LAS X-installed
    NavigatorExpert add-in directory that contains the CAM API assemblies.
    """

    delay_ms: int | None = 250
    runtime_root: str = (
        r"C:\Program Files\Leica Microsystems CMS GmbH\LAS X\AddIns\NavigatorExpert"
    )


LASX_API = LasxApiProfile()


REQUIRED_DLLS = (
    "PYLICamApiConnector.dll",
    "LMS.CAM.CORE.dll",
    "LMS.CAM.SHARED.OBJECTS.dll",
    "Newtonsoft.Json.dll",
)


def _runtime_root() -> Path:
    """Return the configured LAS X CAM API runtime root."""

    return Path(LASX_API.runtime_root)


def _missing_dlls(root: Path) -> list[str]:
    """Return required DLL names missing from ``root``."""
    return [name for name in REQUIRED_DLLS if not (root / name).exists()]


def load_lasx_api_runtime() -> Any:
    """Load LAS X CAM API assemblies and return a module-like connector.

    The returned object intentionally mirrors the small connector surface the
    driver uses: ``LasxApiClientPyModel``, ``__version__``, ``__file__``, and
    ``base_path``.
    """
    root = _runtime_root()
    missing = _missing_dlls(root)
    if missing:
        raise RuntimeError(
            f"LAS X CAM API runtime not found at {root} "
            f"(missing {', '.join(missing)}). Is LAS X installed?"
        )

    # Keep this import after the filesystem check so the missing-runtime path
    # stays testable on machines without LAS X or pythonnet installed.
    import clr  # type: ignore[import-not-found]  # noqa: F401,PLC0415
    import System  # type: ignore[import-not-found]  # noqa: PLC0415

    dll_paths = [root / name for name in REQUIRED_DLLS]
    refs = [System.Reflection.Assembly.LoadFile(str(path)) for path in dll_paths]
    model_type = refs[0].GetType("PYLICamApiConnector.LasxApiClientPyModel")
    if model_type is None:
        raise RuntimeError(
            f"PYLICamApiConnector.LasxApiClientPyModel not found in {dll_paths[0]}"
        )
    version = refs[0].GetName().Version.ToString()
    model = System.Activator.CreateInstance(model_type)
    return SimpleNamespace(
        LasxApiClientPyModel=model,
        __version__=version,
        __file__=str(dll_paths[0]),
        base_path=str(root),
    )
