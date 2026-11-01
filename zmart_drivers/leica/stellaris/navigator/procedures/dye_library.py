"""LAS X's dye library: the dyes, their peaks and their spectra, read from LAS X's dye-database service.

LAS X keeps its dyes in a service on this computer and asks it the way a web
page would (``http://localhost:8897``, written in the service's own log,
``C:\\ProgramData\\Leica Microsystems\\DyeDataBase\\log\\DyeDataBase.log``):
``GET /dyes/<namespace>?api-version=1`` lists a namespace's dyes (``Leica``,
482 on the simulator, or a user's, ``User:<name>``), and
``GET /dyes/<namespace>/<name>?api-version=1`` gives one dye as XML: its full
name, its excitation, emission and multiphoton peaks, and its spectra as
pairs of a wavelength and an intensity (simulator, 2026-11-01).

This only reads, with the same requests LAS X makes. The library is not
changed here. A dye is named in a setting as ``<namespace>/<name>``
(``Leica/ALEXA 488``); :func:`dye` takes either form.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

#: Where LAS X's dye-database service answers, on this computer.
SERVICE = "http://localhost:8897"
API_VERSION = 1
#: How long to wait for the service, in seconds.
TIMEOUT_S = 10


def fetch(path: str) -> str:
    """The service's answer to ``GET <path>``, as text; OSError when it does not answer."""
    with urllib.request.urlopen(f"{SERVICE}{path}", timeout=TIMEOUT_S) as answer:
        return answer.read().decode("utf-8")


def dye_names(namespace: str = "Leica") -> list[str]:
    """The names of the dyes in *namespace* (``Leica``, or a user's ``User:<name>``), as LAS X lists them."""
    listed = ET.fromstring(fetch(f"/dyes/{urllib.parse.quote(namespace, safe=':')}?api-version={API_VERSION}"))
    return [dye.get("Name") for dye in listed.iter("Dye")]


def dye(name: str, namespace: str = "Leica") -> dict:
    """One dye: its names, its peaks (nm) and its spectra as ``[(nm, intensity), ...]``; LookupError when there is none.

    *name* may carry its namespace as a setting names it (``Leica/ALEXA 488``).
    """
    if "/" in name:
        namespace, name = name.split("/", 1)
    path = f"/dyes/{urllib.parse.quote(namespace, safe=':')}/{urllib.parse.quote(name)}?api-version={API_VERSION}"
    try:
        found = ET.fromstring(fetch(path))
    except OSError as why:
        raise LookupError(f"LAS X's dye library has no dye {namespace}/{name}: {why}") from why
    data = found.find("Data")

    def text(tag):
        element = found.find(tag)
        return (element.text or "").strip() if element is not None else ""

    def number(tag):
        value = text(tag)
        return float(value) if value else None

    def spectrum(tag):
        graph = data.find(tag) if data is not None else None
        return [(float(pair.get("W")), float(pair.get("I"))) for pair in graph.iter("Pair")] if graph is not None else []

    return {
        "name": text("Name"),
        "namespace": text("Namespace"),
        "setting_name": f"{text('Namespace')}/{text('Name')}",
        "full_name": text("FullName"),
        "creator": text("Creator"),
        "excitation_peak_nm": number("ExcitationPeak"),
        "emission_peak_nm": number("EmissionPeak"),
        "multiphoton_peak_nm": number("MPExcitationPeak"),
        "excitation_spectrum": spectrum("ExcitationGraph"),
        "emission_spectrum": spectrum("EmissionGraph"),
        "multiphoton_spectrum": spectrum("MPExcitationGraph"),
    }
