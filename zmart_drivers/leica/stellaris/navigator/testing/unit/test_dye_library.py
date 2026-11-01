"""LAS X's dye library, read from its local dye-database service the way LAS X reads it.

LAS X asks a service on this computer (``http://localhost:8897``) for its
dyes: ``GET /dyes/<namespace>`` lists them, ``GET /dyes/<namespace>/<name>``
gives one, both as XML (simulator, 2026-11-01). These tests hand the reader
answers in that form and check what it makes of them.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

import pytest

from zmart_drivers.leica.stellaris.navigator.procedures import dye_library

LIST = (
    '<?xml version="1.0" encoding="utf-8" standalone="yes"?><DyeSummaryList Version="1">'
    '<Dye Namespace="Leica" Name="7-AAD" Uuid="a" DatabaseChangeDate="2026-10-26T17:37:02Z" />'
    '<Dye Namespace="Leica" Name="ALEXA 488" Uuid="b" DatabaseChangeDate="2026-10-26T17:37:02Z" />'
    "</DyeSummaryList>"
)
DYE = (
    '<?xml version="1.0" encoding="utf-8" standalone="yes"?><Dye Version="1" Uuid="b">'
    "<Namespace>Leica</Namespace><Name>ALEXA 488</Name><FullName>Alexa Fluor 488</FullName>"
    "<EmissionPeak>519</EmissionPeak><ExcitationPeak>499</ExcitationPeak><MPExcitationPeak>720</MPExcitationPeak>"
    "<Creator>Leica</Creator><Data><DyeType IsProtein=\"Undefined\" IsAutofluorescence=\"Undefined\" /><Lifetime />"
    '<EmissionGraph><Pair W="475" I="0.87" /><Pair W="476" I="0.6" /></EmissionGraph>'
    '<ExcitationGraph><Pair W="250" I="100.94" /></ExcitationGraph>'
    '<MPExcitationGraph><Pair W="700" I="91.69" /></MPExcitationGraph></Data></Dye>'
)


@pytest.fixture
def service(monkeypatch):
    asked = []

    def answer(path):
        asked.append(path)
        if path == "/dyes/Leica?api-version=1":
            return LIST
        if path == "/dyes/Leica/ALEXA%20488?api-version=1":
            return DYE
        if path == "/dyes/User:someone?api-version=1":
            return '<DyeSummaryList Version="1" />'
        raise OSError("404")

    monkeypatch.setattr(dye_library, "fetch", answer)
    return asked


def test_the_dyes_of_a_namespace_are_listed_by_name(service):
    assert dye_library.dye_names() == ["7-AAD", "ALEXA 488"]
    assert dye_library.dye_names("User:someone") == []


def test_one_dye_is_read_with_its_peaks_and_spectra(service):
    dye = dye_library.dye("ALEXA 488")

    assert (dye["name"], dye["full_name"], dye["namespace"]) == ("ALEXA 488", "Alexa Fluor 488", "Leica")
    assert (dye["excitation_peak_nm"], dye["emission_peak_nm"], dye["multiphoton_peak_nm"]) == (499.0, 519.0, 720.0)
    assert dye["emission_spectrum"] == [(475.0, 0.87), (476.0, 0.6)]
    assert dye["excitation_spectrum"] == [(250.0, 100.94)]
    assert dye["multiphoton_spectrum"] == [(700.0, 91.69)]
    assert dye["setting_name"] == "Leica/ALEXA 488"
    assert service[-1] == "/dyes/Leica/ALEXA%20488?api-version=1"


def test_a_dye_the_library_does_not_have_says_so(service):
    with pytest.raises(LookupError, match="ALEXA 9999"):
        dye_library.dye("ALEXA 9999")


def test_a_dye_s_name_in_a_setting_is_found_in_the_library(service):
    assert dye_library.dye("Leica/ALEXA 488")["name"] == "ALEXA 488"
