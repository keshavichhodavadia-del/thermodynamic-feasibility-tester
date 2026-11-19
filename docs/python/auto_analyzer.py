#!/usr/bin/env python3
"""AUTOMATIC SCENARIO ANALYZER — plain-English problem to feasibility verdict.

Pipeline
--------
1. Scan    : find every number with a unit and convert it to engine units
             (K, kW, kJ, kg/s, kJ/(kg K), ...). The original phrase is kept.
2. Roles   : read the words around each quantity and decide what it means
             (heat input, rejected heat, claimed work, hot/cold temperature,
             stream inlet/outlet, wind speed, head, ...).
3. Map     : identify the energy SOURCE and SINK. Hot/cold streams are
             grouped, reservoirs are separated from streams, and the domain
             (thermal, mechanical, wind, hydro, solar, chemical) is chosen.
4. Infer   : derive uniquely determined inputs from conservation equations.
             Physical properties and boundary conditions must be supplied.
5. Solve   : build the standard engine case and run the ORIGINAL engine
             (thermodynamic_feasibility_tester_MASTER.run_case).

Every extracted value, inferred value and assumption is reported, so the
mapping can be checked and defended. The analyzer never invents a claim:
if the claimed output or a required quantity is missing or ambiguous, it
returns "needs_values" with a specific list of what to add.

Pure standard-library Python (runs locally and in the browser via Pyodide).

Usage
-----
    python auto_analyzer.py "An engine receives 100 kW from a source at 600 K, ..."
    python auto_analyzer.py --json "..."
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import thermodynamic_feasibility_tester_MASTER as thermo

# ============================================================================
# 0. Constants and labelled defaults
# ============================================================================

SOLAR_IRRADIANCE_W_M2 = 1000.0  # explicit normalization only for efficiency-only claims
GRAVITY_M_S2 = 9.81
BETZ = 16.0 / 27.0

# Standard Gibbs energies at 298.15 K, 1 bar, per mole of fuel, liquid water
# product (from standard Gibbs energies of formation). Used only when the
# text names the fuel but gives no Delta G; always reported as an assumption.
FUEL_GIBBS = {
    "hydrogen": (-237.1, 2.016, "H2 + 1/2 O2 -> H2O(l)", r"\b(?:hydrogen|H2)\b"),
    "methane": (-818.0, 16.04, "CH4 + 2 O2 -> CO2 + 2 H2O(l)", r"\b(?:methane|CH4|natural gas)\b"),
    "methanol": (-702.4, 32.04, "CH3OH(l) + 3/2 O2 -> CO2 + 2 H2O(l)", r"\b(?:methanol|CH3OH)\b"),
    "ethanol": (-1325.4, 46.07, "C2H5OH(l) + 3 O2 -> 2 CO2 + 3 H2O(l)", r"\b(?:ethanol|C2H5OH)\b"),
}

# ============================================================================
# 1. Quantity scanner
# ============================================================================

NUM = (r"(?<![\w.])(?P<sign>[-−–])?(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d*)?|\.\d+)"
       r"(?P<exp>\s*(?:[eE][-+−]?\d+|[×x*]\s*10\s*\^?\s*\(?[-+−]?\d+\)?))?")
NUM_RE = re.compile(NUM)
NUM_PLAIN = r"[-−]?(?:\d+(?:\.\d*)?|\.\d+)(?:\s*(?:[eE][-+−]?\d+|[×x*]\s*10\s*\^?\s*\(?[-+−]?\d+\)?))?"
SUP2 = r"(?:2|²|\^2)"
SUP3 = r"(?:3|³|\^3)"
KGK = r"\(?\s*kg\s*(?:[·.\-*/ ]|\s)?\s*(?:K|°\s*C|ºC|C)\s*\)?"
PER = r"\s*(?:/|per|each|a)\s*"


def _c_to_k(value: float) -> float:
    return value + 273.15


def _f_to_k(value: float) -> float:
    return (value - 32.0) * 5.0 / 9.0 + 273.15


# (regex, kind, conversion factor or function, case-sensitive?, unit label)
# Ordered so that longer / compound units are tried first.
UNIT_SPECS: List[Tuple[str, str, Any, bool, str]] = [
    # specific heat capacity / specific entropy  kJ/(kg K)
    (rf"kJ\s*/\s*{KGK}", "cp", 1.0, False, "kJ/(kg K)"),
    (rf"kJ\s*/\s*kg\s*/\s*(?:K|°C)", "cp", 1.0, False, "kJ/(kg K)"),
    (rf"kJ\s+per\s+kg\s+(?:per\s+)?(?:K|kelvin|°C|degree)", "cp", 1.0, False, "kJ/(kg K)"),
    (rf"J\s*/\s*{KGK}", "cp", 0.001, False, "kJ/(kg K)"),
    (rf"J\s*/\s*kg\s*/\s*(?:K|°C)", "cp", 0.001, False, "kJ/(kg K)"),
    (r"kJ\s*/\s*K\b", "entropy", 1.0, True, "kJ/K"),
    (r"kW\s*/\s*K\b", "entropy_rate", 1.0, True, "kW/K"),
    # molar
    (r"kJ\s*/\s*mol(?:e)?(?:\s+(?:of\s+)?reaction)?\b", "molar_energy", 1.0, False, "kJ/mol"),
    (r"kJ\s+per\s+mol(?:e)?\b", "molar_energy", 1.0, False, "kJ/mol"),
    (r"J\s*/\s*mol(?:e)?\b", "molar_energy", 0.001, False, "kJ/mol"),
    (r"mmol\s*/\s*s\b", "molar_rate", 0.001, False, "mol/s"),
    (r"mol(?:e?s)?\s*(?:/|per)\s*s(?:ec(?:ond)?)?\b", "molar_rate", 1.0, False, "mol/s"),
    (r"mol(?:e?s)?\s*(?:/|per)\s*min(?:ute)?\b", "molar_rate", 1 / 60, False, "mol/s"),
    (r"mol(?:e?s)?\s*(?:/|per)\s*h(?:r|our)?\b", "molar_rate", 1 / 3600, False, "mol/s"),
    # specific energy kJ/kg (after cp so that kJ/kg K is not split)
    (r"MJ\s*/\s*kg\b", "specific_energy", 1000.0, False, "kJ/kg"),
    (r"kJ\s*/\s*kg\b", "specific_energy", 1.0, False, "kJ/kg"),
    (r"kJ\s+per\s+kg\b", "specific_energy", 1.0, False, "kJ/kg"),
    (r"J\s*/\s*kg\b", "specific_energy", 0.001, False, "kJ/kg"),
    # irradiance
    (rf"kW\s*/\s*m{SUP2}", "irradiance", 1000.0, False, "W/m²"),
    (rf"W\s*/\s*m{SUP2}", "irradiance", 1.0, False, "W/m²"),
    (r"W\s+per\s+square\s+met(?:er|re)", "irradiance", 1.0, False, "W/m²"),
    # density
    (rf"kg\s*/\s*m{SUP3}", "density", 1.0, False, "kg/m³"),
    # mass flow
    (r"kg\s*(?:/|per)\s*s(?:ec(?:ond)?s?)?\b", "mass_flow", 1.0, False, "kg/s"),
    (r"kg\s*(?:/|per)\s*min(?:ute)?\b", "mass_flow", 1 / 60, False, "kg/s"),
    (r"kg\s*(?:/|per)\s*h(?:r|our)?\b", "mass_flow", 1 / 3600, False, "kg/s"),
    (r"g\s*(?:/|per)\s*s(?:ec(?:ond)?)?\b", "mass_flow", 0.001, False, "kg/s"),
    (r"g\s*(?:/|per)\s*min(?:ute)?\b", "mass_flow", 0.001 / 60, False, "kg/s"),
    (r"(?:t|tonnes?|tons?)\s*(?:/|per)\s*h(?:r|our)?\b", "mass_flow", 1000 / 3600, False, "kg/s"),
    # volumetric flow
    (rf"m{SUP3}\s*(?:/|per)\s*s(?:ec(?:ond)?)?\b", "vol_flow", 1.0, False, "m³/s"),
    (rf"m{SUP3}\s*(?:/|per)\s*min(?:ute)?\b", "vol_flow", 1 / 60, False, "m³/s"),
    (rf"m{SUP3}\s*(?:/|per)\s*h(?:r|our)?\b", "vol_flow", 1 / 3600, False, "m³/s"),
    (r"(?:L|l|litres?|liters?)\s*(?:/|per)\s*s(?:ec(?:ond)?)?\b", "vol_flow", 0.001, False, "m³/s"),
    (r"(?:L|l|litres?|liters?)\s*(?:/|per)\s*min(?:ute)?\b", "vol_flow", 0.001 / 60, False, "m³/s"),
    (r"(?:L|l|litres?|liters?)\s*(?:/|per)\s*h(?:r|our)?\b", "vol_flow", 0.001 / 3600, False, "m³/s"),
    # velocity
    (r"m\s*/\s*s\b", "velocity", 1.0, True, "m/s"),
    (r"met(?:er|re)s?\s+per\s+second\b", "velocity", 1.0, False, "m/s"),
    (r"km\s*/\s*h(?:r)?\b|kmph\b|km\s+per\s+hour\b", "velocity", 1 / 3.6, False, "m/s"),
    # per-step energy and step rate (mechanical harvesting)
    (rf"(?:kJ|J|joules?)(?:\s+of\s+energy)?{PER}(?:foot)?(?:step|stride|footfall|press|stomp|event)\b",
     "event_energy", None, False, "J/step"),
    (rf"(?:foot)?(?:steps?|strides?|footfalls?|presses|events?){PER}min(?:ute)?\b", "event_rate", 1 / 60, False, "1/s"),
    (rf"(?:foot)?(?:steps?|strides?|footfalls?|presses|events?){PER}s(?:ec(?:ond)?)?\b", "event_rate", 1.0, False, "1/s"),
    (rf"(?:foot)?(?:steps?|strides?|footfalls?|presses|events?){PER}h(?:our)?\b", "event_rate", 1 / 3600, False, "1/s"),
    # power, including energy-per-time forms
    (r"kJ\s*(?:/|per)\s*s(?:ec(?:ond)?)?\b", "power", 1.0, False, "kW"),
    (r"kJ\s*(?:/|per)\s*min(?:ute)?\b", "power", 1 / 60, False, "kW"),
    (r"kJ\s*(?:/|per)\s*h(?:r|our)?\b", "power", 1 / 3600, False, "kW"),
    (r"MJ\s*(?:/|per)\s*s(?:ec(?:ond)?)?\b", "power", 1000.0, False, "kW"),
    (r"MJ\s*(?:/|per)\s*h(?:r|our)?\b", "power", 1000 / 3600, False, "kW"),
    (r"MJ\s*(?:/|per)\s*min(?:ute)?\b", "power", 1000 / 60, False, "kW"),
    (r"J\s*(?:/|per)\s*s(?:ec(?:ond)?)?\b", "power", 0.001, False, "kW"),
    (r"(?:Btu|BTU)\s*(?:/|per)\s*h(?:r|our)?\b", "power", 0.000293071, False, "kW"),
    (r"GW(?![A-Za-z])", "power", 1.0e6, True, "kW"),
    (r"MW(?![A-Za-z])", "power", 1000.0, True, "kW"),
    (r"kW(?![A-Za-z])", "power", 1.0, True, "kW"),
    (r"KW(?![A-Za-z])", "power", 1.0, True, "kW"),
    (r"mW(?![A-Za-z])", "power", 1.0e-6, True, "kW"),
    (r"W(?![A-Za-z])", "power", 0.001, True, "kW"),
    (r"gigawatts?\b", "power", 1.0e6, False, "kW"),
    (r"megawatts?\b", "power", 1000.0, False, "kW"),
    (r"kilowatts?\b", "power", 1.0, False, "kW"),
    (r"milliwatts?\b", "power", 1.0e-6, False, "kW"),
    (r"watts?\b", "power", 0.001, False, "kW"),
    (r"(?:hp|horsepower)\b", "power", 0.7457, False, "kW"),
    # energy (amount): per-cycle or with a duration
    (r"kWh\b|kW\s*h\b|kilowatt[- ]hours?\b", "energy", 3600.0, False, "kJ"),
    (r"Wh\b", "energy", 3.6, True, "kJ"),
    (r"GJ(?![A-Za-z/])", "energy", 1.0e6, True, "kJ"),
    (r"MJ(?![A-Za-z/])", "energy", 1000.0, True, "kJ"),
    (r"kJ(?![A-Za-z/])", "energy", 1.0, True, "kJ"),
    (r"J(?![A-Za-z/])", "energy", 0.001, True, "kJ"),
    (r"(?:kilo)?joules?\b", "energy", None, False, "kJ"),
    (r"(?:kcal|kilocalories?)\b", "energy", 4.184, False, "kJ"),
    # temperature
    (r"°\s*F\b|ºF\b|deg(?:rees?)?\s*F(?:ahrenheit)?\b|Fahrenheit\b", "temperature", _f_to_k, False, "K"),
    (r"°\s*C\b|ºC\b|℃|deg(?:rees?)?\s*C(?:elsius)?\b|Celsius\b|centigrade\b", "temperature", _c_to_k, False, "K"),
    (r"C(?![A-Za-z0-9])", "temperature", _c_to_k, True, "K"),
    (r"K(?![A-Za-z0-9])|kelvins?\b|°\s*K\b", "temperature", 1.0, True, "K"),
    (r"kelvins?\b", "temperature", 1.0, False, "K"),
    # geometry
    (rf"cm{SUP2}", "area", 1.0e-4, False, "m²"),
    (rf"m{SUP2}(?![A-Za-z0-9])", "area", 1.0, False, "m²"),
    (r"square\s+met(?:er|re)s?\b|sq\.?\s*m\b", "area", 1.0, False, "m²"),
    (r"km(?![A-Za-z0-9/])", "length", 1000.0, True, "m"),
    (r"cm(?![A-Za-z0-9/])", "length", 0.01, True, "m"),
    (r"mm(?![A-Za-z0-9/])", "length", 0.001, True, "m"),
    (r"m(?![A-Za-z0-9/²³^])", "length", 1.0, True, "m"),
    (r"met(?:er|re)s?\b", "length", 1.0, False, "m"),
    (r"(?:ft|feet|foot)\b", "length", 0.3048, False, "m"),
    # electrical, time, fraction
    (r"mA(?![A-Za-z])", "current", 0.001, True, "A"),
    (r"A(?![A-Za-z0-9])|amps?\b|amperes?\b", "current", 1.0, True, "A"),
    (r"amps?\b|amperes?\b", "current", 1.0, False, "A"),
    (r"V(?![A-Za-z0-9])|volts?\b", "voltage", 1.0, True, "V"),
    (r"%|percent\b|per\s*cent\b", "fraction", 0.01, False, ""),
    (r"Hz\b", "event_rate", 1.0, True, "1/s"),
]
UNIT_RES = [(re.compile(r"\s*(?:" + pattern + r")", 0 if case else re.IGNORECASE), kind, conv, label)
            for pattern, kind, conv, case, label in UNIT_SPECS]


@dataclass
class Quantity:
    value: float              # converted to engine units
    kind: str                 # dimension, e.g. "temperature", "power"
    unit: str                 # engine unit label
    quote: str                # text as written, e.g. "57 °C"
    start: int
    end: int
    raw: float
    sentence: int = 0
    role: Optional[str] = None
    owner: Optional[str] = None       # stream key
    pair: Optional[str] = None        # "from"/"between"/"range" partner info
    partner: Optional[int] = None
    note: str = ""


def _parse_number(match: re.Match) -> float:
    value = float(match["num"].replace(",", ""))
    exp = match["exp"]
    if exp:
        digits = re.search(r"[-+−]?\d+\)?\s*$", exp.strip())
        power = int(digits.group(0).replace("−", "-").rstrip(")").strip()) if digits else 0
        value *= 10.0 ** power
    if match["sign"]:
        value = -value
    return value


def _convert(raw: float, conv: Any, kind: str, unit_text: str) -> float:
    if callable(conv):
        return conv(raw)
    if conv is None:
        if kind in ("event_energy",):
            return raw * (1000.0 if re.match(r"\s*k", unit_text, re.IGNORECASE) else 1.0)
        if kind == "energy":
            return raw * (1.0 if re.match(r"\s*kilo", unit_text, re.IGNORECASE) else 0.001)
    return raw * conv


def sentence_spans(text: str) -> List[Tuple[int, int]]:
    spans, start = [], 0
    for match in re.finditer(r"(?<=[.!?;])\s+(?=\S)|\n+", text):
        spans.append((start, match.start()))
        start = match.end()
    spans.append((start, len(text)))
    return spans


def scan_quantities(text: str) -> List[Quantity]:
    """Find every number+unit, including 'from 600 to 400 K' style pairs."""
    sentences = sentence_spans(text)
    found: List[Quantity] = []
    pending: List[Tuple[re.Match, float]] = []   # numbers without their own unit
    for match in NUM_RE.finditer(text):
        raw = _parse_number(match)
        unit_match, spec = None, None
        for regex, kind, conv, label in UNIT_RES:
            unit_match = regex.match(text, match.end())
            if unit_match:
                spec = (kind, conv, label)
                break
        if not unit_match:
            pending.append((match, raw))
            continue
        kind, conv, label = spec
        unit_text = unit_match.group(0)
        if kind == "temperature" and conv == _c_to_k and match["sign"] is None:
            pass
        value = _convert(raw, conv, kind, unit_text)
        quote = text[match.start():unit_match.end()].strip()
        q = Quantity(value, kind, label, quote, match.start(), unit_match.end(), raw)
        # A bare number directly before this one ("from 600 to 400 K",
        # "between 600 and 300 K", "50 to 60 kW") inherits the same unit.
        if pending:
            prev_match, prev_raw = pending[-1]
            gap = text[prev_match.end():match.start()]
            joiner = re.fullmatch(r"\s*(to|and|-|–|—|or)\s*", gap, re.IGNORECASE)
            if joiner:
                before = text[max(0, prev_match.start() - 40):prev_match.start()].lower()
                if re.search(r"\bfrom\s+(?:an?\s+|the\s+)?(?:initial\s+)?(?:temperature\s+of\s+)?$", before) \
                        or re.search(r"\bfrom\b[^.;]*$", before[-25:]):
                    pair = "from"
                elif re.search(r"\bbetween\b[^.;]*$", before[-30:]):
                    pair = "between"
                else:
                    pair = "range"
                first = Quantity(_convert(prev_raw, conv, kind, unit_text), kind, label,
                                 text[prev_match.start():prev_match.end()] + " " + label.strip(),
                                 prev_match.start(), prev_match.end(), prev_raw)
                first.pair, q.pair = pair, pair
                found.append(first)
                first.partner = len(found)      # index of q after append
                q.partner = len(found) - 1
                pending.clear()
        found.append(q)
    # explicit with-units pairs: "from 500 °C to 150 °C", "between 600 K and 300 K"
    for i in range(len(found) - 1):
        a, b = found[i], found[i + 1]
        if a.pair or b.pair or a.kind != b.kind:
            continue
        gap = text[a.end:b.start]
        if re.fullmatch(r"\s*(?:to|down to|up to|and|-|–)\s*", gap, re.IGNORECASE):
            before = text[max(0, a.start - 30):a.start].lower()
            if re.search(r"\bfrom\s*$|\bfrom\s+\w+\s*$", before):
                a.pair = b.pair = "from"
            elif re.search(r"\bbetween\s*$|\bbetween\s+\w+\s*$", before):
                a.pair = b.pair = "between"
            elif re.fullmatch(r"\s*(?:to|-|–)\s*", gap) and a.kind != "temperature":
                a.pair = b.pair = "range"
            else:
                continue
            a.partner, b.partner = i + 1, i
    for q in found:
        for index, (s, e) in enumerate(sentences):
            if s <= q.start <= e:
                q.sentence = index
                break
    return found


# ============================================================================
# 2. Context-based role scoring
# ============================================================================

def _left_context(text: str, quantities: List[Quantity], index: int, sentences) -> str:
    q = quantities[index]
    s_start = sentences[q.sentence][0]
    prev_end = s_start
    for other in quantities[:index]:
        if other.sentence == q.sentence and other.end <= q.start and other.partner != index:
            prev_end = max(prev_end, other.end)
    if q.pair == "from" and q.partner is not None and q.partner < index:
        # second member of a pair: use the context before the pair
        prev_end = s_start
        for other in quantities[:q.partner]:
            if other.sentence == q.sentence:
                prev_end = max(prev_end, other.end)
        return text[prev_end:quantities[q.partner].start]
    return text[prev_end:q.start]


def _right_context(text: str, quantities: List[Quantity], index: int, sentences) -> str:
    q = quantities[index]
    s_end = sentences[q.sentence][1]
    nxt = s_end
    for other in quantities[index + 1:]:
        if other.sentence == q.sentence and other.start >= q.end:
            nxt = min(nxt, other.start)
            break
    right = text[q.end:nxt]
    # A qualifier that follows a number belongs to the same clause only.
    cut = re.search(r"[,;:()]|\b(?:and|while|but|which|whereas|then|so|if)\b", right, re.IGNORECASE)
    if cut:
        right = right[:cut.start()]
    words = re.findall(r"\S+", right)
    return " " + " ".join(words[:6])


def _score(left: str, right: str, rules: Dict[str, List[Tuple[str, float]]]) -> Dict[str, float]:
    scores = {role: 0.0 for role in rules}
    left_words = len(re.findall(r"\w+", left))
    for role, patterns in rules.items():
        for pattern, weight in patterns:
            for m in re.finditer(pattern, left, re.IGNORECASE):
                distance = left_words - len(re.findall(r"\w+", left[:m.end()]))
                scores[role] += weight / (1.0 + 0.35 * max(distance, 0))
            for m in re.finditer(pattern, right, re.IGNORECASE):
                distance = len(re.findall(r"\w+", right[:m.start()]))
                scores[role] += 0.85 * weight / (1.0 + 0.35 * distance)
    return scores


ENERGY_RULES = {
    "work_out": [(r"\bproduc\w*", 3), (r"\bgenerat\w*", 3), (r"\bdeliver\w*", 3), (r"\boutputs?\b", 2.5), (r"\bsupplies\b", 1.5),
                 (r"\byield\w*", 2.5), (r"\bdevelop\w*", 2.5), (r"\belectric\w*", 2.5), (r"\bwork\b", 2),
                 (r"\bpower\b", 1.0), (r"\bclaim\w*", 1.0), (r"\bnet\b", 1.5), (r"\bconvert\w*", 1.5),
                 (r"\bsupplies\s+(?:a\s+)?(?:load|grid)", 2.5), (r"\bdrives?\b", 1.5),
                 (r"\bdoes\b|\bperforms\b", 2.0)],
    "heat_in": [(r"\breceiv\w*", 3), (r"\babsorb\w*", 3), (r"\bsuppl(?:y|ied|ies)\b", 2.5), (r"\binput\b", 2.5),
                (r"\btak(?:es|ing|en)?\b", 2), (r"\bextract\w*", 2), (r"\bconsum\w*", 2), (r"\bheat\b", 1.2),
                (r"\bfrom\b", 0.8), (r"\bburn\w*", 2), (r"\bfuel\b", 1.5), (r"\bfed\b", 2), (r"\bdraws?\b", 2),
                (r"\bacquir\w*", 2), (r"\bgets?\b", 1.5)],
    "heat_out": [(r"\breject\w*", 3.5), (r"\bdischarg\w*", 3), (r"\bdump\w*", 3), (r"\brelease\w*", 2.5),
                 (r"\blos(?:e|es|t|s|ing)\b", 2.5), (r"\bto\s+(?:the|a)\b", 0.8), (r"\bwaste\b", 1.2),
                 (r"\bcondenser\b", 2), (r"\bsink\b", 1.5),
                 (r"\b(?:surroundings|environment|atmosphere|river|lake|sea|ocean)\b", 1.2),
                 (r"\bexhaust(?:s|ed)\b", 2.5), (r"\bgives?\s+(?:out|off|up)\b", 2.5)],
    "mech_in": [(r"\bmechanical\b", 3), (r"\bshaft\s+(?:power|work)\s+(?:input|supplied)", 3),
                (r"\binput\s+power\b|\bpower\s+input\b", 2), (r"\bpedal\w*", 2.5), (r"\bhuman\b", 1.5),
                (r"\bsupplied\s+to\s+(?:it|the\s+generator|the\s+device)", 2.5), (r"\bkinetic\b", 1.5),
                (r"\bavailable\b", 1.2), (r"\brecoverable\b", 2.5)],
}

TEMP_RULES = {
    "hot": [(r"\bhot\w*", 2.5), (r"\bsource\b", 3), (r"\bfurnace\b", 3), (r"\bboiler\b", 2.5),
            (r"\bcombustion\b", 2), (r"\bflame\b", 2), (r"\bgeothermal\b", 2), (r"\bhigh[- ]temperature\b", 3),
            (r"\bhigher\b", 1.5), (r"\bsuppl\w*\s+at\b", 2), (r"\breceiv\w*", 1.5), (r"\bfrom\b", 0.9),
            (r"\bupper\b", 1.5), (r"\bsun\b", 2), (r"\bcondens(?:ing|es)\b", 2), (r"\bsolidif\w*|\bfreez\w*", 1.5),
            (r"\bheat\s+(?:is\s+)?(?:taken|extracted|added)\b", 1.5), (r"\bwaste\s+heat\b", 1.5),
            (r"\bexhaust\b|\bflue\b", 2.5), (r"\bbrine\b|\bgeothermal\s+(?:fluid|water)\b", 1.5),
            (r"\bheater\b|\bburner\b|\bengine\s+block\b|\bcylinder\b", 2.0)],
    "cold": [(r"\bcold\w*", 2.5), (r"\bsink\b", 3), (r"\bsurroundings\b", 3), (r"\bambient\b", 3),
             (r"\benvironment\b", 3), (r"\batmosphere\b", 2.5), (r"\briver\b|\blake\b|\bsea\b|\bocean\b", 2.5),
             (r"\bcooling\b", 1.5), (r"\breject\w*", 2), (r"\blow[- ]temperature\b", 3), (r"\blower\b", 1.5),
             (r"\bdump\w*|\bdischarg\w*", 1.5), (r"\boutside\b|\broom\b", 1.5), (r"\bcooler\b|\bradiator\b|\bheat\s+sink\b", 2.5), (r"\bmelt\w*", 2),
             (r"\bboil\w*|\bevaporat\w*|\bvaporiz\w*", 1.0), (r"\bto\b", 0.6), (r"\bdead\s+state\b", 2.5)],
    "in": [(r"\benter\w*", 3), (r"\binlet\b", 3), (r"\binitial\w*", 2.5), (r"\bincoming\b", 2), (r"\bfrom\b", 1.5),
           (r"\bsupplied\s+at\b", 1.5), (r"\bstarts?\b", 1.5), (r"\barriv\w*", 2)],
    "out": [(r"\bleav\w*", 3), (r"\bexit\w*", 3), (r"\boutlet\b", 3), (r"\bfinal\w*", 2.5),
            (r"\b(?:cooled|heated|warmed|raised|reduced|brought)\s+(?:down\s+|up\s+)?to\b", 3),
            (r"\b(?:down|up)\s+to\b", 2), (r"\bdischarged\s+at\b", 2.5), (r"\breturn\w*", 2), (r"\bto\b", 1.0)],
}

RESERVOIR_WORDS = (r"\b(?:reservoir|surroundings|ambient|environment|atmosphere|sink|source|furnace|river|lake|sea|ocean|"
                   r"sun|flame|boiler|room|outside|air\s+outside|dead\s+state|ground|earth|wax|ice|condenser)\b")
PHASE_WORDS = r"\b(?:melt\w*|boil\w*|condens\w*|evaporat\w*|vaporiz\w*|freez\w*|solidif\w*|phase\s+change)\b"

STREAM_ADJ = r"(?:hot|cold|cool|cooling|warm|waste|exhaust|flue|chilled|heated|incoming|process|combustion|geothermal)"
STREAM_SUBST = (r"(?:thermal\s+oil|hot\s+oil|oil|sea\s*water|water|brine|air|exhaust\s+gas(?:es)?|flue\s+gas(?:es)?|"
                r"combustion\s+gas(?:es)?|gas(?:es)?|nitrogen|helium|carbon\s+dioxide|CO2|glycol|coolant|"
                r"fluid|stream|steam|exhaust)")
# The adjective nearest the substance decides the side: "cool hot oil" is the HOT oil, "cooling water" is cold.
STREAM_RE = re.compile(rf"\b(?:{STREAM_ADJ}\s+(?={STREAM_ADJ}\s))?(?:(?P<adj>{STREAM_ADJ})\s+)?(?P<subst>{STREAM_SUBST})\b", re.IGNORECASE)
EXCHANGER_RE = (r"\bheat\s+exchangers?\b|\brecuperat\w*|\beconomi[sz]er\b|\bpreheat\w*|\bheat\s+recovery\b|"
                r"\bno\s+(?:shaft\s+)?work\b|\bwithout\s+(?:any\s+)?(?:shaft\s+)?work\b|\bzero\s+(?:shaft\s+)?work\b|"
                r"\ball\s+(?:the\s+)?heat\s+(?:released|lost|given\s+up|rejected)\b[^.]*\babsorbed\b")
EXCHANGER_DATA_TOLERANCE = 1.0e-3   # 0.1 % of the heat duty, for rounded textbook data


def _subst_key(subst: str) -> str:
    s = re.sub(r"\s+", " ", subst.lower())
    if "exhaust" in s or "flue" in s or "combustion" in s:
        return "exhaust gas"
    if "oil" in s:
        return "oil"
    if "sea" in s:
        return "seawater"
    if s in ("gases",):
        return "gas"
    if s in ("co2", "carbon dioxide"):
        return "carbon dioxide"
    if s in ("fluid", "stream", "coolant"):
        return "fluid"
    return s


@dataclass
class Stream:
    key: str
    substance: str
    adjective: str
    first_pos: int
    side: Optional[str] = None
    quantities: List[int] = field(default_factory=list)
    cp_linear: Optional[Tuple[float, float, str]] = None


# ============================================================================
# 3. The analysis object
# ============================================================================

@dataclass
class Analysis:
    text: str
    domain: str = "unknown"
    model: Optional[str] = None
    title: str = ""
    extracted: List[Dict[str, Any]] = field(default_factory=list)
    mapping: List[str] = field(default_factory=list)
    assumptions: List[str] = field(default_factory=list)
    inferred: List[str] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)
    issues: List[str] = field(default_factory=list)
    cases: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    quotes: Dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {"domain": self.domain, "model": self.model, "title": self.title,
                "extracted": self.extracted, "mapping": self.mapping,
                "assumptions": self.assumptions, "inferred": self.inferred,
                "missing": self.missing, "issues": self.issues}


DOMAIN_LABELS = {
    "thermal": "Thermal (heat engine / source–sink)",
    "mechanical": "Mechanical (recoverable work)",
    "wind": "Wind (kinetic energy, Betz limit)",
    "hydro": "Hydro (potential energy of water)",
    "solar": "Solar / radiative (Petela–Landsberg limit)",
    "chemical": "Chemical / electrochemical (Gibbs energy)",
    "work_consuming": "Refrigerator / heat pump (consumes work)",
    "unsupported": "Outside the implemented models",
    "no_input": "No energy input (perpetual-motion claim)",
}

DOMAIN_RULES = {
    "thermal": [(r"\bheat\s+engines?\b", 4), (r"\bengine\b", 1.5), (r"\breservoirs?\b", 2.5), (r"\bcarnot\b", 3),
                (r"\bfurnace\b|\bboiler\b", 2), (r"\bwaste\s+heat\b", 3), (r"\bexhaust\b|\bflue\b", 1.5),
                (r"\bgeothermal\b|\bORC\b|\brankine\b|\bbrayton\b|\botto\b|\bstirling\b|\bthermoelectric\b|\bTEG\b|\bOTEC\b", 3),
                (r"\bsource\b|\bsink\b", 1.5), (r"\bpower\s+plant\b", 1.5), (r"\bsteam\b", 1.0),
                (r"\bheat\s+exchanger\b|\bstreams?\b", 1.5), (r"\bhot\b|\bcold\b", 1.0), (r"\bheat\b", 1.0)],
    "mechanical": [(r"\bwalk\w*\b|\bfoot\w*\b|\bsteps?\b|\bshoes?\b|\bstride\w*\b", 3), (r"\bpedal\w*|\bcycling\b|\bbicycle\b", 3),
                   (r"\bpiezo\w*", 3), (r"\bmechanical\b", 2), (r"\bflywheel\b|\bspring\b|\bvibration\w*|\btreadmill\b|\bhand[- ]crank\w*", 2.5),
                   (r"\bhuman\b", 1.5), (r"\bkinetic\s+energy\s+harvest\w*", 2)],
    "wind": [(r"\bwind\b", 4), (r"\bwindmill\b", 4), (r"\brotor\b|\bblades?\b", 2), (r"\bswept\s+area\b", 3)],
    "hydro": [(r"\bhydro\w*\b", 4), (r"\bdam\b", 3), (r"\bhead\b", 2), (r"\bwaterfall\b|\bpenstock\b|\bfalls?\b", 2.5),
              (r"\bmicro[- ]?hydro\b", 3), (r"\bwater\s+wheel\b", 3), (r"\bheight\b|\belevation\b", 1.0)],
    "solar": [(r"\bsolar\b", 4), (r"\bsun\w*\b", 3), (r"\bphotovoltaic\w*|\bPV\b", 4), (r"\birradian\w*|\binsolation\b", 3),
              (r"\bradiation\b|\bblack\s*body\b", 2)],
    "chemical": [(r"\bfuel\s+cells?\b", 5), (r"\bgibbs\b|ΔG|\bdelta\s*G\b", 4), (r"\breaction\b", 2), (r"\bbattery\b|\bbatteries\b", 2),
                 (r"\belectrochemical\b|\belectrolyte\b", 2), (r"\bhydrogen\b|\bmethanol\b|\bethanol\b", 1.5)],
    "work_consuming": [(r"\brefrigerat\w*|\bfreezer\b|\bheat\s+pumps?\b|\bair[- ]condition\w*|\bCOP\b|\bcoefficient\s+of\s+performance\b|\bchiller\b", 5)],
}
KIND_DOMAIN = {"velocity": ("wind", 2.5), "event_energy": ("mechanical", 4), "event_rate": ("mechanical", 2),
               "molar_energy": ("chemical", 4), "molar_rate": ("chemical", 2), "irradiance": ("solar", 4),
               "vol_flow": ("hydro", 1.5), "cp": ("thermal", 3), "specific_energy": ("thermal", 1),
               "temperature": ("thermal", 1.2)}


class ScenarioAnalyzer:
    def __init__(self, text: str):
        self.text = text.strip()
        self.sentences = sentence_spans(self.text)
        self.q = scan_quantities(self.text)
        self.a = Analysis(self.text)
        self.streams: Dict[str, Stream] = {}

    # ------------------------------------------------------------------ helpers
    def ctx(self, index: int) -> Tuple[str, str]:
        return (_left_context(self.text, self.q, index, self.sentences),
                _right_context(self.text, self.q, index, self.sentences))

    def wide_left(self, index: int, chars: int = 120) -> str:
        q = self.q[index]
        s_start = self.sentences[q.sentence][0]
        return self.text[max(s_start, q.start - chars):q.start]

    def record(self, index: int, label: str, value: Optional[float] = None, unit: Optional[str] = None,
               field_name: Optional[str] = None) -> None:
        q = self.q[index]
        entry = {"quantity": label, "value": q.value if value is None else value,
                 "unit": q.unit if unit is None else unit, "quote": q.quote}
        if entry not in self.a.extracted:
            self.a.extracted.append(entry)
        if field_name:
            self.a.quotes[field_name] = q.quote

    def of_kind(self, *kinds: str) -> List[int]:
        return [i for i, q in enumerate(self.q) if q.kind in kinds]

    def has(self, pattern: str) -> bool:
        return re.search(pattern, self.text, re.IGNORECASE) is not None

    # ------------------------------------------------------------------ domain
    def detect_domain(self) -> str:
        scores = {d: 0.0 for d in DOMAIN_RULES}
        for domain, rules in DOMAIN_RULES.items():
            for pattern, weight in rules:
                scores[domain] += weight * len(re.findall(pattern, self.text, re.IGNORECASE))
        for q in self.q:
            if q.kind in KIND_DOMAIN:
                domain, weight = KIND_DOMAIN[q.kind]
                scores[domain] += weight
        if any(q.kind == "length" for q in self.q) and any(q.kind in ("vol_flow", "mass_flow") for q in self.q) \
                and self.has(r"\bhead\b|\bheight\b|\bfalls?\b|\bdam\b|\bdrop\b"):
            scores["hydro"] += 4
        if self.has(r"\bwind\b") and any(q.kind == "velocity" for q in self.q):
            scores["wind"] += 3
        if scores["work_consuming"] > 0 and not self.has(r"\bheat\s+engine\b"):
            return "work_consuming"
        best = max(scores, key=scores.get)
        if scores[best] <= 0:
            return "unknown"
        return best

    # ------------------------------------------------------------------ roles
    def assign_energy_roles(self) -> None:
        for i, q in enumerate(self.q):
            if q.kind not in ("power", "energy"):
                continue
            left, right = self.ctx(i)
            scores = _score(left, right, ENERGY_RULES)
            # "of heat" right after the number is a strong heat marker
            if re.match(r"\s*(?:of\s+)?heat\b", right, re.IGNORECASE) and scores["heat_out"] < 2.5:
                scores["heat_in"] += 2.0
            if re.match(r"\s*(?:of\s+)?(?:useful\s+|net\s+|shaft\s+|mechanical\s+)?(?:work|power|electric\w*)\b",
                        right, re.IGNORECASE) and not re.search(r"\bmechanical\b", right[:25], re.IGNORECASE):
                scores["work_out"] += 2.0
            best = max(scores, key=scores.get)
            q.role = best if scores[best] > 0.6 else None

    def assign_temperature_roles(self) -> None:
        for i, q in enumerate(self.q):
            if q.kind != "temperature":
                continue
            left, right = self.ctx(i)
            q.note = ""
            phase_ctx = left + right
            # "600 K reservoir" qualifies; "600 K with ambient air" / "800 K rejects" do not.
            cut = re.search(r"\b(?:with|from|to|at|in|on|by|into|of|for|is|are|was|rejects?|receives?|"
                            r"produces?|and|while|whereas)\b", right, re.IGNORECASE)
            right = right[:cut.start()] if cut else right
            scores = _score(left, right, TEMP_RULES)
            if q.pair == "from" and q.partner is not None:
                q.role = "in" if q.partner > i else "out"
                continue
            q.role = max(scores, key=scores.get) if max(scores.values()) > 0.5 else None
            q.scores = scores  # type: ignore[attr-defined]
            if re.search(PHASE_WORDS, phase_ctx, re.IGNORECASE):
                q.note = "phase"

    # ------------------------------------------------------------------ streams
    def build_streams(self) -> None:
        mentions = []
        for m in STREAM_RE.finditer(self.text):
            subst = _subst_key(m.group(0) if (m["adj"] or "").lower() in ("exhaust", "flue", "combustion")
                               and re.search(r"gas", m["subst"], re.I) else m["subst"])
            adj = (m["adj"] or "").lower()
            # "heat engine", "water" in "water wheel" etc. are not streams
            after = self.text[m.end():m.end() + 12].lower()
            if subst == "steam" and re.match(r"\s*(?:engine|turbine|power)", after):
                continue
            mentions.append((m.start(), m.end(), subst, adj))
        if not mentions:
            return
        # Merge mentions into streams.
        order: List[str] = []
        mention_keys = []
        for start, end, subst, adj in mentions:
            side_hint = "hot" if subst in ("exhaust gas",) or adj in ("hot", "warm", "waste", "exhaust", "flue", "heated", "combustion", "geothermal") \
                else "cold" if adj in ("cold", "cool", "cooling", "chilled") else ""
            key = None
            if side_hint:
                key = f"{side_hint} {subst}"
            else:
                same = [k for k in order if self.streams[k].substance == subst]
                if subst == "fluid" and not same:
                    same = [k for k in order if self.streams[k].substance != "fluid"]
                if len(same) == 1:
                    key = same[0]
                elif len(same) > 1:
                    key = None  # resolved by proximity below
                else:
                    key = subst
            if key is not None and key not in self.streams:
                self.streams[key] = Stream(key, subst, side_hint, start, side=side_hint or None)
                order.append(key)
            mention_keys.append((start, end, key, subst))
        # Resolve ambiguous mentions to the most recently named stream of that substance.
        resolved = []
        last_by_subst: Dict[str, str] = {}
        for start, end, key, subst in mention_keys:
            if key is None:
                key = last_by_subst.get(subst) or next((k for k in order if self.streams[k].substance == subst), None)
            if key is None:
                continue
            last_by_subst[subst] = key
            resolved.append((start, end, key))
        self._mentions = resolved

        # Attach stream-type quantities to the nearest stream mention.
        for i, q in enumerate(self.q):
            if q.kind not in ("temperature", "mass_flow", "vol_flow", "cp"):
                continue
            owner = self._owner_for(i)
            if owner is None:
                continue
            if q.kind == "temperature":
                left, right = self.ctx(i)
                clause = left[-60:]
                res = [m.end() for m in re.finditer(RESERVOIR_WORDS, clause, re.IGNORECASE)]
                strm = [m.end() for m in STREAM_RE.finditer(clause)]
                if res and (not strm or max(res) > max(strm)) and q.pair != "from":
                    continue  # reservoir temperature, not a stream state
                if q.note == "phase":
                    continue
            q.owner = owner
            self.streams[owner].quantities.append(i)
        # Linear Cp expressions "Cp = a + bT" in each stream's scope.
        for m in re.finditer(r"\bc_?p\s*(?:\(\s*T\s*\))?\s*=\s*(" + NUM_PLAIN + r")\s*([+\-−])\s*(" + NUM_PLAIN
                             + r")\s*[*×·]?\s*T\b", self.text, re.IGNORECASE):
            a_val = _parse_number(NUM_RE.search(m.group(1)))
            b_val = abs(_parse_number(NUM_RE.search(m.group(3))))
            if m.group(2) in "-−":
                b_val = -b_val
            owner = self._owner_at(m.start())
            if owner is None and len(self.streams) == 1:
                owner = next(iter(self.streams))
            if owner is not None:
                self.streams[owner].cp_linear = (a_val, b_val, m.group(0))
                self.a.extracted.append({"quantity": f"{owner} Cp(T)", "value": f"{a_val:g} + {b_val:g}·T",
                                         "unit": "kJ/(kg K)", "quote": m.group(0)})
            # cp-expression numbers must not be read as other quantities
            self.q = [q for q in self.q if not (m.start() <= q.start < m.end())]

    def _owner_at(self, pos: int) -> Optional[str]:
        sentence = next((k for k, (s, e) in enumerate(self.sentences) if s <= pos <= e), 0)
        s_start, s_end = self.sentences[sentence]
        before = [(st, key) for st, en, key in self._mentions if s_start <= st < pos]
        if before:
            return max(before)[1]
        after = [(st, key) for st, en, key in self._mentions if pos < st <= s_end]
        if after:
            return min(after)[1]
        prev = [(st, key) for st, en, key in self._mentions if st < s_start]
        if prev and re.match(r"\s*(?:it|its|this|the\s+stream|the\s+same|which|they|their)\b",
                             self.text[s_start:s_end], re.IGNORECASE):
            return max(prev)[1]
        return None

    def _owner_for(self, index: int) -> Optional[str]:
        q = self.q[index]
        follow = re.match(r"\s*of\s+(?:the\s+)?", self.text[q.end:])
        if follow:
            pos = q.end + follow.end()
            direct = [key for st, en, key in self._mentions if st == pos]
            if direct:
                return direct[0]
        return self._owner_at(q.start)

    # ------------------------------------------------------------------ claim
    def claim(self, domain_inputs: Tuple[str, ...] = ("heat_in",)) -> Tuple[Optional[float], Optional[int], Optional[float], Optional[int]]:
        """Return (claimed work kW, quantity index, claimed efficiency fraction, index)."""
        work = [i for i, q in enumerate(self.q) if q.kind in ("power", "energy") and q.role == "work_out"]
        if any(self.q[i].pair == "range" for i in work):
            self.a.issues.append("The claimed output is given as a range. Give a single claimed value "
                                 "(or test the upper value) to obtain a verdict.")
            return None, None, None, None
        values = {round(self.q[i].value, 12) for i in work}
        if len(values) > 1:
            self.a.issues.append("Several different output values were found: "
                                 + ", ".join(self.q[i].quote for i in work) + ". State which one is claimed.")
            return None, None, None, None
        eff = None
        eff_index = None
        for i, q in enumerate(self.q):
            left = self.wide_left(i, 60)
            if q.kind == "fraction" and re.search(r"\befficien\w*|\bconvert\w*|\bconversion\b", left + self.ctx(i)[1][:30],
                                                  re.IGNORECASE):
                eff, eff_index = q.value, i
        # efficiency written as a bare decimal: "efficiency of 0.42"
        if eff is None:
            m = re.search(r"\befficiency\s+(?:of|is|=|:)?\s*(0?\.\d+|1\.0+)\b(?!\s*[%A-Za-z])", self.text, re.IGNORECASE)
            if m:
                eff = float(m.group(1))
                self.a.extracted.append({"quantity": "Claimed efficiency", "value": eff, "unit": "fraction",
                                         "quote": m.group(0)})
        if eff_index is not None:
            self.record(eff_index, "Claimed efficiency", eff * 100, "%", "claimed_efficiency")
        if work:
            return self.q[work[0]].value, work[0], eff, eff_index
        return None, None, eff, eff_index

    def single_power(self, exclude_roles=("heat_in", "heat_out", "mech_in")) -> Optional[int]:
        cands = [i for i, q in enumerate(self.q) if q.kind == "power" and q.role not in exclude_roles]
        if len(cands) == 1:
            return cands[0]
        return None

    # ================================================================== routes
    def analyse(self) -> Analysis:
        a = self.a
        if not self.q and not self.text:
            a.missing.append("A description of the process with its numbers.")
            return a
        domain = self.detect_domain()
        if re.search(r"\bperpetual\s+motion\b|\bwithout\s+(?:any\s+)?(?:energy|fuel|power|heat)(?:\s+input)?\b|"
                     r"\bno\s+(?:energy|fuel|power|heat)\s+input\b|\bfrom\s+nothing\b|\bfree\s+energy\b|"
                     r"\bwith\s+zero\s+(?:energy|fuel|power)\s+input\b", self.text, re.IGNORECASE):
            domain = "no_input"
        a.domain = domain
        self.assign_energy_roles()
        self.assign_temperature_roles()
        if domain in ("thermal", "unknown"):
            self.build_streams()
        handler = {
            "thermal": self.route_thermal, "mechanical": self.route_mechanical, "wind": self.route_wind,
            "hydro": self.route_hydro, "solar": self.route_solar, "chemical": self.route_chemical,
            "work_consuming": self.route_work_consuming, "no_input": self.route_no_input,
        }.get(domain, self.route_unknown)
        handler()
        return a

    # ---------------------------------------------------------------- thermal
    def route_thermal(self) -> None:
        a = self.a
        # Models 3, 4 and 5 are chosen only on strong cues; otherwise the source/sink routes below apply.
        if self.try_steady_cv() or self.try_closed_system() or self.try_property_request():
            return
        hot_streams = [s for s in self.streams.values() if s.quantities or s.cp_linear]
        # Decide stream sides from their temperatures and adjectives.
        complete_streams = []
        for s in hot_streams:
            temps = [i for i in s.quantities if self.q[i].kind == "temperature"]
            flows = [i for i in s.quantities if self.q[i].kind in ("mass_flow", "vol_flow")]
            if not temps:
                continue
            if len(temps) == 1 and not flows and s.cp_linear is None and \
                    not any(self.q[i].kind == "cp" for i in s.quantities):
                # a single temperature with no flow is a reservoir-like source/sink
                q = self.q[temps[0]]
                q.owner = None
                if s.side and q.role not in ("hot", "cold"):
                    q.role = s.side
                if s.side == "hot" or q.role == "hot":
                    self.a.assumptions.append(
                        f"Only one temperature is given for the {s.key} ({q.quote}); it is treated as an isothermal "
                        "source at that temperature. A stream that cools while giving up heat has a lower effective "
                        "temperature, so this gives the most generous (upper-bound) ceiling.")
                continue
            complete_streams.append(s)
        stream_info = {s.key: self.stream_state(s) for s in complete_streams}
        reservoirs = [i for i, q in enumerate(self.q) if q.kind == "temperature" and q.owner is None]
        hot_res, cold_res = self.classify_reservoirs(reservoirs)

        hot_s = [k for k, st in stream_info.items() if st["side"] == "hot"]
        cold_s = [k for k, st in stream_info.items() if st["side"] == "cold"]
        if len(hot_s) > 1 or len(cold_s) > 1:
            a.issues.append("More than one hot or cold stream was found; describe one source stream and one sink stream.")
            a.missing.append("A single source (hot) stream and a single sink (cold) stream.")
            return
        latent = self.latent_reservoir()
        if latent is not None:
            cold_res = [latent]
        if hot_s and cold_s:
            return self.thermal_two_streams(stream_info[hot_s[0]], stream_info[cold_s[0]])
        if hot_s:
            return self.thermal_stream_reservoir(stream_info[hot_s[0]], None, cold_res, "hot")
        if cold_s:
            return self.thermal_stream_reservoir(stream_info[cold_s[0]], hot_res, None, "cold")
        return self.thermal_reservoirs(hot_res, cold_res)

    # ---------------------------------------------------------------- Models 3, 4, 5 from text
    IN_WORDS = r"\benter\w*|\binlet\b|\binitial\w*|\bstate\s*1\b|\bsupplied\b|\bat\s+the\s+inlet\b"
    OUT_WORDS = r"\bleav\w*|\bexit\w*|\boutlet\b|\bdischarg\w*|\bstate\s*2\b|\bfinal\w*"

    def _in_or_out(self, index: int, order_position: int) -> str:
        left = self.wide_left(index, 140)
        last_in = max((m.end() for m in re.finditer(self.IN_WORDS, left, re.IGNORECASE)), default=-1)
        last_out = max((m.end() for m in re.finditer(self.OUT_WORDS, left, re.IGNORECASE)), default=-1)
        if last_in < 0 and last_out < 0:
            return "in" if order_position == 0 else "out"
        return "in" if last_in > last_out else "out"

    def try_steady_cv(self) -> bool:
        """Model 3: one inlet and one outlet with h and s from tables (turbine, compressor, nozzle, pump...)."""
        if not self.has(r"\bturbines?\b|\bcompressors?\b|\bnozzles?\b|\bdiffusers?\b|\bpumps?\b|\bthrottl\w*|"
                        r"\bcontrol\s+volume\b|\bsteady[- ]flow\b"):
            return False
        hs = [i for i, q in enumerate(self.q) if q.kind == "specific_energy"
              and not re.search(r"latent|fusion|vapori|heating\s+value|LHV|HHV", self.wide_left(i, 40), re.I)]
        ss = [i for i, q in enumerate(self.q) if q.kind == "cp"
              and re.search(r"\bs\s*\d?\s*[=:]|\bentropy\b", self.wide_left(i, 40), re.I)]
        if len(hs) != 2 or len(ss) != 2:
            return False
        a = self.a
        a.model, a.title = "steady_control_volume", "Steady control volume (one inlet, one outlet)"
        h = {self._in_or_out(i, n): i for n, i in enumerate(hs)}
        sv = {self._in_or_out(i, n): i for n, i in enumerate(ss)}
        if set(h) != {"in", "out"} or set(sv) != {"in", "out"}:
            h, sv = {"in": hs[0], "out": hs[1]}, {"in": ss[0], "out": ss[1]}
            a.assumptions.append("Inlet/outlet wording was unclear: the first h and s are taken as the inlet state.")
        for side, label in (("in", "Inlet"), ("out", "Outlet")):
            self.record(h[side], f"{label} specific enthalpy h", field_name=f"h_{side}")
            self.record(sv[side], f"{label} specific entropy s", field_name=f"s_{side}")
        flows = self.of_kind("mass_flow")
        if flows:
            mdot = self.q[flows[0]].value
            self.record(flows[0], "Mass flow rate", field_name="mass_flow_kg_s")
        else:
            a.missing.append("Mass flow rate through the device (kg/s).")
            return True
        heat = []
        heats = [i for i, q in enumerate(self.q) if q.kind == "power" and q.role in ("heat_in", "heat_out")]
        if heats and not self.has(r"\badiabatic\b|\binsulated\b"):
            i = heats[0]
            temps = [j for j, q in enumerate(self.q) if q.kind == "temperature" and q.sentence == self.q[i].sentence and j > i]
            if not temps:
                a.missing.append("Boundary temperature at which the heat crosses the boundary (K).")
                return True
            sign = -1.0 if self.q[i].role == "heat_out" else 1.0
            heat = [{"heat_into_cv_kw": sign * self.q[i].value, "boundary_temperature_K": self.q[temps[0]].value}]
            self.record(i, "Heat transfer (+ into the device)", sign * self.q[i].value, "kW", "heat_into_cv_kw")
            self.record(temps[0], "Boundary temperature for that heat", field_name="boundary_temperature_K")
        if not heats and not self.has(r"\badiabatic\b|\binsulated\b|\bno heat\b"):
            a.missing.append("Heat interaction, or explicitly state that the device is adiabatic.")
            return True
        w, wi, _, _ = self.claim()
        case = {"model": "steady_control_volume", "title": a.title, "description": self.text,
                "inlets": [{"mass_flow_kg_s": mdot, "specific_enthalpy_kj_kg": self.q[h["in"]].value,
                            "specific_entropy_kj_kg_k": self.q[sv["in"]].value}],
                "outlets": [{"mass_flow_kg_s": mdot, "specific_enthalpy_kj_kg": self.q[h["out"]].value,
                             "specific_entropy_kj_kg_k": self.q[sv["out"]].value}],
                "heat": heat}
        if w is not None:
            case["W_out_kw"] = w
            self.record(wi, "Claimed shaft work output", field_name="W_out_kw")
        else:
            a.issues.append("No claimed work was found: the work is derived from the First Law and only the Second "
                            "Law (S_gen ≥ 0) is tested.")
        a.assumptions.append("Kinetic and potential energy changes are neglected; h and s are taken as given (from tables).")
        a.mapping += ["Boundary: steady open device with one inlet and one outlet (Model 3).",
                      "Tests: mass in = out; Q̇ − Ẇ + ṁ(h_in − h_out) = 0; S_gen = ṁ(s_out − s_in) − ΣQ̇/T_b ≥ 0."]
        a.cases["steady_control_volume"] = case
        return True

    def try_closed_system(self) -> bool:
        """Model 4: fixed mass with stated energy and entropy changes and heat at known temperatures."""
        entropy = [i for i, q in enumerate(self.q) if q.kind == "entropy"]
        if not entropy or not self.has(r"\bpiston\b|\bcylinder\b|\brigid\s+tank\b|\bclosed\s+system\b|\bsealed\b|"
                                       r"\binternal\s+energy\b|\bΔU\b|\bdelta\s*U\b"):
            return False
        a = self.a
        de = [i for i, q in enumerate(self.q) if q.kind == "energy"
              and re.search(r"internal\s+energy|energy\s+of\s+the\s+(?:system|gas|fluid)|ΔU|ΔE|delta\s*[UE]",
                            self.wide_left(i, 60), re.I)]
        if not de:
            return False
        a.model, a.title = "closed_system", "Closed system (energy and entropy balance)"
        sign = lambda i: -1.0 if re.search(r"decreas|fall|drop|reduc|lower|loses", self.wide_left(i, 50), re.I) else 1.0
        delta_e = sign(de[0]) * self.q[de[0]].value
        delta_s = sign(entropy[0]) * self.q[entropy[0]].value
        self.record(de[0], "Change in system energy ΔE", delta_e, "kJ", "delta_E_kj")
        self.record(entropy[0], "Change in system entropy ΔS", delta_s, "kJ/K", "delta_S_system_kj_K")
        heat = []
        for i, q in enumerate(self.q):
            if q.kind != "energy" or i in de or q.role not in ("heat_in", "heat_out"):
                continue
            temps = [j for j, t in enumerate(self.q) if t.kind == "temperature" and t.sentence == q.sentence and j > i] or \
                    [j for j, t in enumerate(self.q) if t.kind == "temperature" and t.sentence == q.sentence and j < i]
            if not temps:
                a.missing.append(f"Boundary temperature for the heat transfer “{q.quote}” (K).")
                continue
            value = (-1.0 if q.role == "heat_out" else 1.0) * q.value
            heat.append({"heat_into_system_kj": value, "boundary_temperature_K": self.q[temps[0]].value})
            self.record(i, "Heat transfer (+ into the system)", value, "kJ")
            self.record(temps[0], "Boundary temperature for that heat")
        if not heat and not self.has(r"\badiabatic\b|\binsulated\b|\bno heat\b"):
            a.missing.append("Heat interaction, or explicitly state that the closed system is adiabatic.")
        if a.missing:
            return True
        work = [i for i, q in enumerate(self.q) if q.kind == "energy" and q.role == "work_out" and i not in de]
        case = {"model": "closed_system", "title": a.title, "description": self.text,
                "delta_E_kj": delta_e, "delta_S_system_kj_K": delta_s, "heat": heat}
        if work:
            case["W_out_kj"] = self.q[work[0]].value
            self.record(work[0], "Claimed work output", field_name="W_out_kj")
        a.mapping += ["Boundary: closed system — no mass crosses it (Model 4).",
                      "Tests: ΣQ − W − ΔE = 0 and S_gen = ΔS_system − ΣQ/T_b ≥ 0."]
        a.cases["closed_system"] = case
        return True

    def try_property_request(self) -> bool:
        """Model 5: the question asks only for property changes (Δh, Δs, T_eff), with no device claim."""
        if not self.has(r"\b(?:calculate|find|determine|compute|what\s+(?:is|are))\b[^.?]*\b(?:enthalpy|entropy)\s+change|"
                        r"\b(?:calculate|find|determine|compute)\b[^.?]*\b(?:effective|log[- ]mean)\s+temperature"):
            return False
        if any(q.role == "work_out" for q in self.q if q.kind in ("power", "energy")):
            return False
        temps = [i for i, q in enumerate(self.q) if q.kind == "temperature"]
        if len(temps) < 2:
            return False
        a = self.a
        a.model, a.title = "property_change", "Property change of a material"
        t1, t2 = temps[0], temps[1]
        self.record(t1, "Initial temperature T1", field_name="T1_K")
        self.record(t2, "Final temperature T2", field_name="T2_K")
        case = {"model": "property_change", "title": a.title, "description": self.text,
                "T1_K": self.q[t1].value, "T2_K": self.q[t2].value}
        linear = next((st.cp_linear for st in self.streams.values() if st.cp_linear), None)
        cps = [i for i, q in enumerate(self.q) if q.kind == "cp"
               and not re.search(r"entropy", self.wide_left(i, 40), re.I)]
        if linear:
            case.update(cp_model="linear", a=linear[0], b=linear[1])
        elif cps:
            case.update(cp_model="constant", cp_kj_kg_k=self.q[cps[0]].value)
            self.record(cps[0], "Heat capacity Cp", field_name="cp_kj_kg_k")
        else:
            a.missing.append("Heat capacity Cp of the material at the stated conditions (kJ/kg K).")
            return True
        a.mapping += ["Property calculation only (Model 5): Δh = ∫Cp dT, Δs = ∫Cp/T dT, T_eff = |Δh|/|Δs|.",
                      "No device is assessed, so no feasibility verdict is given."]
        a.cases["property_change"] = case
        return True

    def latent_reservoir(self) -> Optional[Any]:
        """Sink defined by phase-change enthalpy and entropy: T = Δh/Δs."""
        dh = [i for i, q in enumerate(self.q) if q.kind == "specific_energy"
              and re.search(r"latent|melt|fusion|vapori|phase|evapor", self.wide_left(i, 80), re.IGNORECASE)]
        ds = [i for i, q in enumerate(self.q) if q.kind == "cp"
              and re.search(r"entropy", self.wide_left(i, 60), re.IGNORECASE) and q.owner is None]
        if len(dh) == 1 and len(ds) == 1:
            t = thermo.phase_change_temperature(self.q[dh[0]].value, self.q[ds[0]].value)
            self.record(dh[0], "Phase-change enthalpy Δh", field_name="phase_delta_h_kj_kg")
            self.record(ds[0], "Phase-change entropy Δs", field_name="phase_delta_s_kj_kg_k")
            self.a.mapping.append(f"Phase-change sink: T = Δh/Δs = {self.q[dh[0]].value:g}/{self.q[ds[0]].value:g} "
                                  f"= {t:.2f} K (isothermal).")
            return ("latent", t)
        return None

    def classify_reservoirs(self, idx: List[int]) -> Tuple[List[Any], List[Any]]:
        hot, cold = [], []
        unlabeled = []
        for i in idx:
            q = self.q[i]
            if q.pair == "between" and q.partner is not None:
                unlabeled.append(i)
                continue
            if q.role == "hot":
                hot.append(i)
            elif q.role == "cold":
                cold.append(i)
            else:
                unlabeled.append(i)
        if (len(hot) >= 2 and not cold) or (len(cold) >= 2 and not hot):
            both = sorted(hot + cold, key=lambda i: self.q[i].value, reverse=True)
            hot, cold = [both[0]], [both[-1]]
            self.a.assumptions.append("The source/sink wording was ambiguous: the higher temperature is taken as the "
                                      "source and the lower as the sink.")
        if unlabeled:
            if len(unlabeled) == 2 and not hot and not cold:
                hi, lo = sorted(unlabeled, key=lambda i: self.q[i].value, reverse=True)
                hot.append(hi)
                cold.append(lo)
                self.a.assumptions.append("Two reservoir temperatures were given without labels: the higher is "
                                          "taken as the source and the lower as the sink.")
            elif len(unlabeled) == 1:
                i = unlabeled[0]
                if hot and not cold:
                    cold.append(i)
                elif cold and not hot:
                    hot.append(i)
                else:
                    hot.append(i)
            else:
                for i in unlabeled:
                    (hot if not hot else cold).append(i)
        return hot, cold

    def stream_state(self, s: Stream) -> Dict[str, Any]:
        temps = [i for i in s.quantities if self.q[i].kind == "temperature"]
        tin = tout = None
        for i in temps:
            if self.q[i].role == "in" and tin is None:
                tin = i
            elif self.q[i].role == "out" and tout is None:
                tout = i
        rest = [i for i in temps if i not in (tin, tout)]
        for i in rest:
            if tin is None:
                tin = i
            elif tout is None:
                tout = i
        side = s.side
        if tin is not None and tout is not None:
            side = "hot" if self.q[tin].value > self.q[tout].value else "cold"
            if s.side and s.side != side:
                self.a.issues.append(f"The {s.key} is called {s.side} but its temperatures show it "
                                     f"{'cooling' if side == 'hot' else 'warming'}; it is treated as the {side} side.")
        if side is None:
            ctx = self.text[max(0, s.first_pos - 10):s.first_pos + 160].lower()
            side = "hot" if re.search(r"cool(?:s|ed|ing)?\b|releas|gives\s+up", ctx) else \
                "cold" if re.search(r"heat(?:s|ed)\b|warm|absorb", ctx) else None
        flows = [i for i in s.quantities if self.q[i].kind in ("mass_flow", "vol_flow")]
        cps = [i for i in s.quantities if self.q[i].kind == "cp"]
        mflow = None
        if flows:
            q = self.q[flows[0]]
            if q.kind == "mass_flow":
                mflow = q.value
            else:
                densities = [i for i in s.quantities if self.q[i].kind == "density"]
                if densities:
                    mflow = q.value * self.q[densities[0]].value
                    self.a.inferred.append(f"Mass flow = supplied density × volumetric flow = {mflow:g} kg/s.")
                else:
                    self.a.missing.append(f"Density or mass flow of {s.key}; volumetric flow alone does not determine mass flow.")
        cp = None
        cp_assumed = False
        if cps:
            cp = ("constant", self.q[cps[0]].value)
        elif s.cp_linear:
            cp = ("linear", s.cp_linear[0], s.cp_linear[1])
        return {"key": s.key, "name": s.key, "side": side, "tin": tin, "tout": tout, "flow": flows[0] if flows else None,
                "m": mflow, "cp": cp, "cp_index": cps[0] if cps else None, "substance": s.substance,
                "cp_assumed": cp_assumed}

    def _dh_ds(self, cp, t1, t2) -> Tuple[float, float]:
        if cp[0] == "constant":
            return thermo.properties_constant_cp(t1, t2, cp[1])
        return thermo.properties_linear_cp(t1, t2, cp[1], cp[2])

    def _solve_outlet(self, cp, t_in, dh_target) -> float:
        if cp[0] == "constant":
            return t_in + dh_target / cp[1]
        a_, b_ = cp[1], cp[2]
        lo, hi = (1.0, t_in) if dh_target < 0 else (t_in, t_in + 5000.0)
        return thermo.solve_temperature_bisection(dh_target, t_in, lo, hi, lambda T: a_ + b_ * T)

    def _stream_case(self, st) -> Dict[str, Any]:
        out = {"name": st["name"], "mass_flow_kg_s": st["m"], "T_in_K": st["Tin"], "T_out_K": st["Tout"]}
        if st["cp"][0] == "constant":
            out.update(cp_model="constant", cp_kj_kg_k=st["cp"][1])
        else:
            out.update(cp_model="linear", a=st["cp"][1], b=st["cp"][2])
        return out

    def _record_stream(self, st, label) -> None:
        for key, name, field_ in (("tin", "inlet temperature", "T_in_K"), ("tout", "outlet temperature", "T_out_K"),
                                  ("flow", "flow rate", "mass_flow_kg_s"), ("cp_index", "Cp", "cp_kj_kg_k")):
            if st.get(key) is not None:
                self.record(st[key], f"{label} {name}", field_name=f"{st['side']}_stream.{field_}")

    def thermal_two_streams(self, hot, cold) -> None:
        a = self.a
        a.model = "finite_stream_engine"
        a.title = "Finite hot stream → device → finite cold stream"
        w, wi, eff, _ = self.claim()
        exchanger = w is None and eff is None and self.has(EXCHANGER_RE)
        if exchanger:
            a.model = "finite_stream_heat_recovery"
            a.title = "Insulated heat exchanger (no shaft work)"
            w = 0.0
        for st in (hot, cold):
            st["Tin"] = self.q[st["tin"]].value if st["tin"] is not None else None
            st["Tout"] = self.q[st["tout"]].value if st["tout"] is not None else None
            self._record_stream(st, f"{st['key'].capitalize()}")
            if st["cp"] is None:
                a.missing.append(f"Heat capacity Cp of the {st['key']}.")
        if wi is not None:
            self.record(wi, "Claimed work/power output", field_name="W_out_kw")
        if a.missing:
            return
        unknowns = [(st, f) for st in (hot, cold) for f in ("Tin", "Tout", "m") if st[f] is None]
        if w is None and eff is None:
            pass  # engine will infer work; verdict rests on S_gen only
        if eff is not None and w is None and hot["Tin"] and hot["Tout"] and hot["m"]:
            q_h = -hot["m"] * self._dh_ds(hot["cp"], hot["Tin"], hot["Tout"])[0]
            w = eff * q_h
            a.inferred.append(f"Claimed work = efficiency × heat released by hot stream = {eff:.4g} × {q_h:.4g} kW = {w:.4g} kW.")
        if len(unknowns) > 1 or (unknowns and w is None):
            for st, f in unknowns:
                a.missing.append({"Tin": f"Inlet temperature of the {st['key']}.", "Tout": f"Outlet temperature of the {st['key']}.",
                                  "m": f"Mass flow rate of the {st['key']}."}[f])
            if w is None and unknowns:
                a.missing.append("The claimed work or power output.")
            return
        if unknowns:
            st, f = unknowns[0]
            other = cold if st is hot else hot
            od = other["m"] * self._dh_ds(other["cp"], other["Tin"], other["Tout"])[0]  # signed
            # energy balance: -dH_hot = dH_cold + W
            if st is hot:
                need = -(od + w)                     # signed ΔḢ of hot stream
            else:
                need = -od - w                       # signed ΔḢ of cold stream
            if f == "m":
                dh = self._dh_ds(st["cp"], st["Tin"], st["Tout"])[0]
                st["m"] = need / dh
                if st["m"] <= 0:
                    raise thermo.ThermoInputError("The stated temperatures and claimed work leave no positive flow "
                                                  f"for the {st['key']}: the claim exceeds the heat available.")
                a.inferred.append(f"{st['key'].capitalize()} mass flow from the First Law: {st['m']:.5g} kg/s.")
            elif f == "Tout":
                st["Tout"] = self._solve_outlet(st["cp"], st["Tin"], need / st["m"])
                a.inferred.append(f"{st['key'].capitalize()} outlet temperature from the First Law "
                                  f"(Q̇_hot = Ẇ + Q̇_cold): {st['Tout']:.5g} K ({st['Tout'] - 273.15:.4g} °C).")
            else:
                raise thermo.ThermoInputError(f"Give the inlet temperature of the {st['key']}.")
            a.assumptions.append("One missing stream quantity was calculated from the energy balance, so the "
                                 "First Law closes by construction; the verdict rests on the Second Law.")
        if exchanger:
            return self._exchanger_case(hot, cold)
        case = {"model": "finite_stream_engine", "title": a.title, "description": self.text,
                "hot_stream": self._stream_case(hot), "cold_stream": self._stream_case(cold),
                "apply_effective_temperature_limit": True, "apply_strict_reversible_limit": True}
        if w is not None:
            case["W_out_kw"] = w
        a.mapping += [f"SOURCE: {hot['key']} cooling {hot['Tin']:.2f} → {hot['Tout']:.2f} K.",
                      f"SINK: {cold['key']} warming {cold['Tin']:.2f} → {cold['Tout']:.2f} K.",
                      "Boundary: both streams + device, steady and adiabatic to the surroundings.",
                      "Tests: First Law, S_gen = ṁ_hΔs_h + ṁ_cΔs_c ≥ 0, and the exact reversible limit W_max = Q̇_hot − "
                      "Q̇_cold,rev (cold outlet chosen so that S_gen = 0). The T_eff ratio is shown for information only."]
        if w is None:
            a.issues.append("No claimed output was found; work is inferred from the stream energy balance. "
                            "The Second Law and exact reversible limit are still checked.")
        a.cases["finite_stream_engine"] = case

    def _exchanger_case(self, hot, cold) -> None:
        """Two streams exchanging heat with no work: energy balance, S_gen and exergy destruction."""
        a = self.a
        t0 = None
        for i, q in enumerate(self.q):
            if q.kind == "temperature" and q.owner is None and \
                    re.search(r"surroundings|ambient|environment|atmosphere|dead\s+state|T0|T_0",
                              self.wide_left(i, 60), re.IGNORECASE):
                t0 = q.value
                self.record(i, "Environment temperature T0", field_name="T0_K")
                break
        q_hot = -hot["m"] * self._dh_ds(hot["cp"], hot["Tin"], hot["Tout"])[0]
        q_cold = cold["m"] * self._dh_ds(cold["cp"], cold["Tin"], cold["Tout"])[0]
        a.inferred += [f"Heat released by the {hot['key']}: Q̇ = ṁ·Δh = {q_hot:.6g} kW.",
                       f"Heat absorbed by the {cold['key']}: Q̇ = ṁ·Δh = {q_cold:.6g} kW.",
                       f"Energy mismatch = {q_hot - q_cold:.4g} kW ({100 * abs(q_hot - q_cold) / max(q_hot, q_cold):.4g} %)."]
        a.mapping += [f"SOURCE: {hot['key']} cooling {hot['Tin']:.2f} → {hot['Tout']:.2f} K.",
                      f"SINK: {cold['key']} warming {cold['Tin']:.2f} → {cold['Tout']:.2f} K.",
                      "Device: insulated heat exchanger, no shaft work (W = 0). Boundary encloses both streams.",
                      "Tests: Q̇_released = Q̇_absorbed, S_gen = ṁ_hΔs_h + ṁ_cΔs_c ≥ 0, no temperature cross; "
                      "exergy destroyed = T0·S_gen."]
        a.cases["finite_stream_heat_recovery"] = {
            "model": "finite_stream_heat_recovery", "title": a.title, "description": self.text,
            "hot_stream": self._stream_case(hot), "cold_stream": self._stream_case(cold),
            "T0_K": t0}

    def thermal_stream_reservoir(self, st, hot_res, cold_res, side) -> None:
        """Finite stream on one side, isothermal reservoir on the other → T_eff mapping."""
        a = self.a
        st["Tin"] = self.q[st["tin"]].value if st["tin"] is not None else None
        st["Tout"] = self.q[st["tout"]].value if st["tout"] is not None else None
        self._record_stream(st, st["key"].capitalize())
        w, wi, eff, _ = self.claim()
        if wi is not None:
            self.record(wi, "Claimed work/power output", field_name="W_out_kw")
        if st["cp"] is None:
            a.missing.append(f"Heat capacity Cp of the {st['key']}.")
        # reservoir temperature on the other side
        res_list = cold_res if side == "hot" else hot_res
        t_res = None
        if res_list:
            r = res_list[0]
            if isinstance(r, tuple):
                t_res = r[1]
            else:
                t_res = self.q[r].value
                self.record(r, "Sink temperature" if side == "hot" else "Source temperature",
                            field_name="T_cold_K" if side == "hot" else "T_hot_K")
        else:
            a.missing.append("Temperature of the heat sink." if side == "hot" else "Temperature of the heat source.")
        if st["Tin"] is None:
            a.missing.append(f"Inlet temperature of the {st['key']}.")
        q_heat = None
        if st["m"] is None:
            powers = [i for i, q in enumerate(self.q) if q.kind == "power" and q.role in ("heat_in", "heat_out")]
            if powers and st["Tin"] and st["Tout"]:
                q_heat = self.q[powers[0]].value
                self.record(powers[0], "Heat exchanged by the stream", field_name="Q_kw")
            elif eff is not None and w is None:
                st["m"] = 1.0
                a.assumptions.append("No flow rate given; a 1 kg/s basis is used because only an efficiency is claimed.")
            else:
                a.missing.append(f"Mass flow rate of the {st['key']}.")
        if w is None and eff is None:
            a.missing.append("The claimed work/power output or efficiency.")
        if st["Tout"] is None:
            if side == "cold" and st["m"] is not None and w is not None:
                pass  # solved below from the energy balance
            else:
                a.missing.append(f"Outlet temperature of the {st['key']}.")
        if a.missing:
            return

        if side == "hot":
            dh, ds = self._dh_ds(st["cp"], st["Tin"], st["Tout"])
            m = st["m"] if st["m"] is not None else -q_heat / dh
            q_h = -m * dh
            t_eff = thermo.effective_temperature(dh, ds)
            if eff is not None and w is None:
                w = eff * q_h
                a.inferred.append(f"Claimed work = {eff:.4g} × Q̇_H = {w:.5g} kW.")
            a.inferred.append(f"Hot stream: Δh = {dh:.5g} kJ/kg, Δs = {ds:.5g} kJ/(kg K), Q̇_H = ṁ|Δh| = {q_h:.5g} kW.")
            a.inferred.append(f"Effective source temperature T_H,eff = |Δh|/|Δs| = {t_eff:.5g} K.")
            t_hot, t_cold = t_eff, t_res
            q_c_checked = None
            a.mapping += [f"SOURCE: {st['key']} cooling {st['Tin']:.2f} → {st['Tout']:.2f} K, represented by "
                          f"T_H,eff = {t_eff:.2f} K.",
                          f"SINK: isothermal reservoir at {t_res:.2f} K."]
        else:
            # hot reservoir supplies Q_H; cold stream absorbs Q_C = Q_H - W
            heat_in = [i for i, q in enumerate(self.q) if q.kind == "power" and q.role == "heat_in"]
            t_hot = t_res
            if st["Tout"] is None:
                if not heat_in:
                    a.missing.append("Heat input rate from the source (or the sink outlet temperature).")
                    return
                q_h = self.q[heat_in[0]].value
                self.record(heat_in[0], "Heat input", field_name="Q_hot_kw")
                if eff is not None and w is None:
                    w = eff * q_h
                st["Tout"] = self._solve_outlet(st["cp"], st["Tin"], (q_h - w) / st["m"])
                a.inferred.append(f"Sink outlet temperature from the First Law Q̇_C = Q̇_H − Ẇ: {st['Tout']:.5g} K.")
            dh, ds = self._dh_ds(st["cp"], st["Tin"], st["Tout"])
            q_c = st["m"] * dh
            t_eff = thermo.effective_temperature(dh, ds)
            if heat_in and "Heat input" not in [e["quantity"] for e in a.extracted]:
                # Heat input was stated independently: keep it, so the engine checks the First Law.
                q_h = self.q[heat_in[0]].value
                self.record(heat_in[0], "Heat input", field_name="Q_hot_kw")
                if eff is not None and w is None:
                    w = eff * q_h
                if st.get("cp_assumed"):
                    q_c_checked = None
                    a.inferred.append(f"Stated Q̇_H = {q_h:.5g} kW. The sink stream's Cp was assumed, so its heat "
                                      f"({q_c:.5g} kW) is not used for a strict First Law check; Q̇_C = Q̇_H − Ẇ.")
                else:
                    q_c_checked = q_c
                    a.inferred.append(f"Cold stream absorbs Q̇_C = ṁΔh = {q_c:.5g} kW; stated Q̇_H = {q_h:.5g} kW "
                                      "(the engine checks Q̇_H − Ẇ − Q̇_C = 0).")
            else:
                q_c_checked = None
                if w is not None:
                    q_h = q_c + w
                elif eff is not None and eff != 1:
                    q_h = q_c / (1 - eff)
                    w = eff * q_h
                else:
                    a.missing.append("Heat input or work; the stated efficiency does not determine a finite heat input.")
                    return
                a.inferred.append(f"Cold stream: Q̇_C = ṁΔh = {q_c:.5g} kW; Q̇_H = Q̇_C + Ẇ = {q_h:.5g} kW.")
            a.inferred.append(f"Effective sink temperature T_C,eff = Δh/Δs = {t_eff:.5g} K.")
            t_cold = t_eff
            a.mapping += [f"SOURCE: isothermal reservoir at {t_res:.2f} K.",
                          f"SINK: {st['key']} warming {st['Tin']:.2f} → {st['Tout']:.2f} K, represented by "
                          f"T_C,eff = {t_eff:.2f} K."]
        a.mapping.append("For a finite stream exchanging heat with a reservoir, η_rev = 1 − T_C/T_H with the "
                         "effective temperature is exact: Ẇ_max = ΔḢ − T_res ΔṠ.")
        self._fixed_engine(t_hot, t_cold, q_h, w, q_c_checked,
                           "Finite stream ↔ reservoir engine (effective-temperature mapping)")

    def thermal_reservoirs(self, hot_res, cold_res) -> None:
        a = self.a
        heat_in = [i for i, q in enumerate(self.q) if q.kind in ("power", "energy") and q.role == "heat_in"]
        heat_out = [i for i, q in enumerate(self.q) if q.kind in ("power", "energy") and q.role == "heat_out"]
        w, wi, eff, _ = self.claim()
        if a.issues and w is None and eff is None:
            a.missing.append("A single claimed work/power output.")
            return
        t_hot = t_cold = None
        if hot_res:
            r = hot_res[0]
            t_hot = self.q[r].value
            self.record(r, "Source (hot) temperature", field_name="T_hot_K")
        if cold_res:
            r = cold_res[0]
            if isinstance(r, tuple):
                t_cold = r[1]
            else:
                t_cold = self.q[r].value
                self.record(r, "Sink (cold) temperature", field_name="T_cold_K")
        if len(hot_res) > 1 or len(cold_res) > 1:
            a.missing.append("Identify one source and one sink, or use the multiple-boundary model.")
            return
        q_h = self.q[heat_in[0]].value if heat_in else None
        q_c = self.q[heat_out[0]].value if heat_out else None
        if heat_in:
            self.record(heat_in[0], "Heat input Q_H", field_name="Q_hot_kw")
        if heat_out:
            self.record(heat_out[0], "Rejected heat Q_C", field_name="Q_cold_rejected_kw")
        if wi is not None:
            self.record(wi, "Claimed work output W", field_name="W_out_kw")
        energy_units = {self.q[i].kind for i in heat_in + heat_out + ([wi] if wi is not None else [])}
        if energy_units == {"energy"}:
            a.assumptions.append("Quantities are energies (kJ) rather than rates; they are treated as amounts per "
                                 "cycle. Feasibility depends only on their ratios, so 'kW' in the output reads as 'kJ per cycle'.")
        # unlabeled powers: if only one power and no claim, decide by context
        if w is None and eff is None and q_h is not None and q_c is not None:
            w = q_h - q_c
            a.inferred.append(f"Work output not stated: from the First Law W = Q_H − Q_C = {w:.5g}.")
            a.assumptions.append("Rejected heat was used to infer work, so the First Law closes by construction.")
        if q_h is None and w is not None and q_c is not None:
            q_h = w + q_c
            a.inferred.append(f"Heat input not stated: Q_H = W + Q_C = {q_h:.5g}.")
        if eff is not None:
            if q_h is None and w is None:
                q_h = 100.0
                w = eff * q_h
                a.assumptions.append("Only an efficiency is claimed: a 100 kW heat-input basis is used "
                                     f"(W = {w:.4g} kW); the verdict does not depend on this basis.")
            elif q_h is None and eff == 0:
                a.missing.append("Heat input: zero efficiency does not determine it from work.")
            elif q_h is None:
                q_h = w / eff
                a.inferred.append(f"Heat input from the claimed efficiency: Q_H = W/η = {q_h:.5g}.")
            elif w is None:
                w = eff * q_h
                a.inferred.append(f"Claimed work from efficiency: W = η·Q_H = {w:.5g}.")
            elif abs(w / q_h - eff) > 0.01:
                a.issues.append(f"Stated efficiency {eff:.1%} differs from W/Q_H = {w / q_h:.1%}; W and Q_H are used.")
        # single reservoir (Kelvin–Planck) or missing sink
        single = self.has(r"\bsingle\s+(?:heat\s+)?reservoir\b|\bone\s+(?:heat\s+)?reservoir\b|\bonly\s+one\s+reservoir\b|"
                          r"\bentirely\s+into\s+work\b|\bcompletely\s+into\s+work\b|\bwithout\s+rejecting\b|"
                          r"\bno\s+heat\s+(?:is\s+)?rejected\b|\brejects\s+no\s+heat\b")
        if single and w is None and eff is None and q_h is not None:
            w = q_h
            a.inferred.append("'Converts it entirely into work' means W = Q_H (100 % conversion).")
        if t_hot is None and t_cold is not None and single:
            t_hot, t_cold = t_cold, None
        if t_hot is not None and t_cold is None and (single or (q_h is not None and w is not None and w >= q_h)):
            return self._kelvin_planck(t_hot, q_h, w)
        if t_hot is not None and t_cold is None:
            a.missing.append("Temperature of the sink (where heat is rejected).")
        if t_hot is None:
            a.missing.append("Temperature of the heat source.")
        if q_h is None:
            a.missing.append("Heat input rate Q_H (or the efficiency claimed).")
        if w is None:
            a.missing.append("The claimed work/power output (or efficiency).")
        if a.missing:
            return
        a.mapping += [f"SOURCE: isothermal reservoir at {t_hot:.2f} K supplying Q_H = {q_h:.5g}.",
                      f"SINK: isothermal reservoir at {t_cold:.2f} K.",
                      "Device: cyclic heat engine. Tests: Carnot limit η ≤ 1 − T_C/T_H, First Law, "
                      "S_gen = Q_C/T_C − Q_H/T_H ≥ 0."]
        if t_hot <= t_cold and w > 0:
            return self._cycle_entropy(t_hot, t_cold, q_h, w, q_c)
        self._fixed_engine(t_hot, t_cold, q_h, w, q_c, "Cyclic heat engine between two reservoirs")

    def _fixed_engine(self, t_hot, t_cold, q_h, w, q_c, title) -> None:
        a = self.a
        if t_hot <= t_cold and w and w > 0:
            return self._cycle_entropy(t_hot, t_cold, q_h, w, q_c)
        a.model = "fixed_reservoir_engine"
        a.title = title
        case = {"model": "fixed_reservoir_engine", "title": title, "description": self.text,
                "T_hot_K": t_hot, "T_cold_K": t_cold, "Q_hot_kw": q_h, "W_out_kw": w}
        if q_c is not None:
            case["Q_cold_rejected_kw"] = q_c
        a.cases["fixed_reservoir_engine"] = case

    def _kelvin_planck(self, t_hot, q_h, w) -> None:
        a = self.a
        if q_h is None and w is not None:
            q_h = w
            a.inferred.append("Heat drawn from the single reservoir equals the work produced (Q = W).")
        if q_h is None or w is None:
            a.missing.append("Heat drawn and work produced.")
            return
        a.model = "closed_system"
        a.title = "Cyclic device exchanging heat with a single reservoir (Kelvin–Planck test)"
        a.mapping += [f"SOURCE: single reservoir at {t_hot:.2f} K supplying Q = {q_h:.5g}.",
                      "SINK: none — no heat is rejected to a colder body.",
                      "Over one cycle ΔE = 0 and ΔS = 0, so S_gen = −Q/T_H and the First Law requires W = Q."]
        a.cases["closed_system"] = {
            "model": "closed_system", "title": a.title, "description": self.text,
            "delta_E_kj": 0.0, "delta_S_system_kj_K": 0.0, "W_out_kj": w,
            "heat": [{"heat_into_system_kj": q_h, "boundary_temperature_K": t_hot}]}

    def _cycle_entropy(self, t_hot, t_cold, q_h, w, q_c) -> None:
        a = self.a
        if q_c is None:
            q_c = q_h - w
        a.model = "closed_system"
        a.title = "Cyclic device: heat drawn at a temperature not above the rejection temperature"
        a.issues.append("The stated source is not hotter than the sink, so a Carnot efficiency cannot be defined; "
                        "the cycle entropy balance is used instead.")
        a.cases["closed_system"] = {
            "model": "closed_system", "title": a.title, "description": self.text,
            "delta_E_kj": 0.0, "delta_S_system_kj_K": 0.0, "W_out_kj": w,
            "heat": [{"heat_into_system_kj": q_h, "boundary_temperature_K": t_hot},
                     {"heat_into_system_kj": -q_c, "boundary_temperature_K": t_cold}]}

    # ---------------------------------------------------------------- mechanical
    def route_mechanical(self) -> None:
        a = self.a
        a.model = "mechanical_power"
        a.title = "Mechanical-to-electrical converter"
        case: Dict[str, Any] = {"model": "mechanical_power", "title": a.title, "description": self.text}
        ev = self.of_kind("event_energy")
        rate = self.of_kind("event_rate")
        energies = [i for i in self.of_kind("energy")
                    if re.search(r"\bper\s+(?:foot)?step|\beach\s+step|\bevery\s+step|\bper\s+stride", self.ctx(i)[1][:40], re.I)]
        ev = ev or energies
        mech = [i for i, q in enumerate(self.q) if q.kind == "power" and q.role == "mech_in"]
        heat = [i for i, q in enumerate(self.q) if q.kind == "power" and q.role == "heat_out"]
        w, wi, eff, _ = self.claim()
        if w is None:
            outs = [i for i, q in enumerate(self.q) if q.kind == "power" and q.role not in ("mech_in", "heat_out", "heat_in")]
            if len(outs) == 1:
                w, wi = self.q[outs[0]].value, outs[0]
        if ev and rate:
            case["recoverable_energy_per_event_j"] = self.q[ev[0]].value if self.q[ev[0]].kind == "event_energy" \
                else self.q[ev[0]].value * 1000.0
            case["events_per_s"] = self.q[rate[0]].value
            self.record(ev[0], "Recoverable energy per step", case["recoverable_energy_per_event_j"], "J",
                        "recoverable_energy_per_event_j")
            self.record(rate[0], "Step rate", field_name="events_per_s")
            src = (f"{case['recoverable_energy_per_event_j']:g} J/step × {case['events_per_s']:.4g} steps/s = "
                   f"{case['recoverable_energy_per_event_j'] * case['events_per_s']:.4g} W")
        elif mech:
            case["recoverable_mechanical_power_kw"] = self.q[mech[0]].value
            self.record(mech[0], "Mechanical input power", field_name="recoverable_mechanical_power_kw")
            src = f"{self.q[mech[0]].quote} of mechanical power"
        else:
            src = None
            if ev and not rate:
                a.missing.append("How often the energy is recovered (e.g. steps per minute).")
            elif rate and not ev:
                a.missing.append("Recoverable mechanical energy per step (J).")
            else:
                a.missing.append("Recoverable mechanical input: power (W) or energy per step (J) and step rate.")
        if w is None and eff is not None and src is not None:
            p_in = (case["recoverable_mechanical_power_kw"] if case.get("recoverable_mechanical_power_kw") is not None else
                    case["recoverable_energy_per_event_j"] * case["events_per_s"] / 1000)
            w = eff * p_in
            a.inferred.append(f"Claimed output = {eff:.3g} × {p_in * 1000:.4g} W = {w * 1000:.4g} W.")
        if w is None and not heat:
            a.missing.append("The claimed electrical output (W), or measured heat rejection to derive it.")
        else:
            case["W_out_kw"] = w
            if wi is not None:
                self.record(wi, "Claimed electrical output", field_name="W_out_kw")
        if heat:
            case["heat_rejected_kw"] = self.q[heat[0]].value
            self.record(heat[0], "Measured heat loss", field_name="heat_rejected_kw")
        temps = [i for i, q in enumerate(self.q) if q.kind == "temperature" and
                 re.search(r"\b(?:ambient|heat|rejection|loss)\b", self.wide_left(i, 80), re.I)]
        if temps:
            case["heat_rejection_temperature_K"] = self.q[temps[0]].value
            self.record(temps[0], "Heat-rejection / ambient boundary temperature", field_name="heat_rejection_temperature_K")
        if a.missing:
            return
        a.mapping += [f"SOURCE: recoverable mechanical work, {src}.",
                      "SINK: losses leave as heat (no Carnot temperature is invented for a mechanical source).",
                      "Limit: electrical output ≤ mechanical input (100 % ideal conversion)."]
        a.cases["mechanical_power"] = case

    # ---------------------------------------------------------------- wind
    def route_wind(self) -> None:
        a = self.a
        vel = self.of_kind("velocity")
        lengths = self.of_kind("length")
        areas = self.of_kind("area")
        dens = self.of_kind("density")
        w, wi, eff, _ = self.claim()
        if w is None:
            i = self.single_power()
            if i is not None:
                w, wi = self.q[i].value, i
        if not vel:
            a.missing.append("Wind speed (m/s).")
        area = None
        if areas:
            area = self.q[areas[0]].value
            self.record(areas[0], "Swept area", field_name="swept_area_m2")
        elif lengths:
            i = lengths[0]
            left = self.wide_left(i, 50).lower()
            d = self.q[i].value
            if re.search(r"radius|blade\s+length|blades?\s+(?:of|are|is)|long\s+blades?", left + self.ctx(i)[1][:20].lower()):
                area = math.pi * d ** 2
                self.record(i, "Rotor radius / blade length", field_name="rotor_radius_m")
                a.inferred.append(f"Swept area A = πR² = {area:.5g} m².")
            else:
                area = math.pi * d ** 2 / 4
                self.record(i, "Rotor diameter", field_name="rotor_diameter_m")
                a.inferred.append(f"Swept area A = πD²/4 = {area:.5g} m².")
        else:
            a.missing.append("Rotor diameter, blade length or swept area.")
        rho = None
        if dens:
            rho = self.q[dens[0]].value
            self.record(dens[0], "Air density", field_name="air_density_kg_m3")
        else:
            a.missing.append("Air density at the rotor (kg/m³).")
        if a.missing:
            if w is None and eff is None:
                a.missing.append("The claimed power output.")
            return
        v = self.q[vel[0]].value
        self.record(vel[0], "Wind speed", field_name="wind_speed_m_s")
        p_wind = 0.5 * rho * area * v ** 3 / 1000.0
        p_betz = BETZ * p_wind
        if w is None and eff is not None:
            w = eff * p_wind
            a.inferred.append(f"Claimed output = {eff:.3g} × kinetic power {p_wind:.5g} kW = {w:.5g} kW "
                              "(efficiency taken relative to the wind's kinetic power).")
        if w is None:
            a.missing.append("The claimed power output.")
            return
        if wi is not None:
            self.record(wi, "Claimed power output", field_name="W_out_kw")
        a.inferred += [f"Kinetic power of the wind ½ρAv³ = {p_wind:.5g} kW.",
                       f"Betz limit (16/27 of it) = {p_betz:.5g} kW — the maximum extractable mechanical power."]
        a.mapping += ["SOURCE: kinetic energy of the air stream through the rotor area.",
                      "SINK: slowed air leaving the rotor. No Carnot temperature applies.",
                      "Limit: Betz, P_max = (16/27)·½ρAv³, passed to the engine as recoverable mechanical power."]
        a.model = "mechanical_power"
        a.title = "Wind turbine (Betz limit)"
        a.cases["mechanical_power"] = {"model": "mechanical_power", "title": a.title, "description": self.text,
                                       "recoverable_mechanical_power_kw": p_betz, "W_out_kw": w}

    # ---------------------------------------------------------------- hydro
    def route_hydro(self) -> None:
        a = self.a
        lengths = self.of_kind("length")
        flows = self.of_kind("vol_flow", "mass_flow")
        w, wi, eff, _ = self.claim()
        if w is None:
            i = self.single_power()
            if i is not None:
                w, wi = self.q[i].value, i
        head = None
        for i in lengths:
            head = self.q[i].value
            self.record(i, "Head (height of fall)", field_name="head_m")
            break
        if head is None:
            a.missing.append("Head / height the water falls (m).")
        if not flows:
            a.missing.append("Water flow rate (m³/s, L/s or kg/s).")
        if a.missing:
            if w is None and eff is None:
                a.missing.append("The claimed power output.")
            return
        f = self.q[flows[0]]
        densities = self.of_kind("density")
        if f.kind == "vol_flow" and not densities:
            a.missing.append("Water density (kg/m³), or supply mass flow directly.")
            return
        mdot = f.value * self.q[densities[0]].value if f.kind == "vol_flow" else f.value
        self.record(flows[0], "Water flow rate", field_name="flow")
        if f.kind == "vol_flow":
            self.record(densities[0], "Water density", field_name="density_kg_m3")
        p_max = mdot * GRAVITY_M_S2 * head / 1000.0
        if w is None and eff is not None:
            w = eff * p_max
            a.inferred.append(f"Claimed output = {eff:.3g} × {p_max:.5g} kW = {w:.5g} kW.")
        if w is None:
            a.missing.append("The claimed power output.")
            return
        if wi is not None:
            self.record(wi, "Claimed power output", field_name="W_out_kw")
        a.inferred.append(f"Ideal hydraulic power ṁgH = {mdot:.5g} × {GRAVITY_M_S2} × {head:.5g} = {p_max:.5g} kW.")
        a.assumptions.append(f"g = {GRAVITY_M_S2} m/s²; head is the gross head with no friction losses (ideal limit).")
        a.mapping += ["SOURCE: gravitational potential energy of water falling through the head.",
                      "SINK: water leaving at the lower level. No Carnot temperature applies.",
                      "Limit: P_max = ρgQH, passed to the engine as recoverable mechanical power."]
        a.model = "mechanical_power"
        a.title = "Hydroelectric generator (ρgQH limit)"
        a.cases["mechanical_power"] = {"model": "mechanical_power", "title": a.title, "description": self.text,
                                       "recoverable_mechanical_power_kw": p_max, "W_out_kw": w}

    # ---------------------------------------------------------------- solar
    def route_solar(self) -> None:
        a = self.a
        temps = self.of_kind("temperature")
        t_src = t_amb = None
        for i in temps:
            left = self.wide_left(i, 60).lower()
            if re.search(r"\bsun\b|source|surface|black\s*body|radiat", left) and t_src is None and self.q[i].value > 1000:
                t_src = self.q[i].value
                self.record(i, "Radiation source (sun) temperature", field_name="T_source_K")
            elif t_amb is None:
                t_amb = self.q[i].value
                self.record(i, "Ambient temperature", field_name="T_ambient_K")
        if t_src is None:
            a.missing.append("Radiation source temperature (K) for the blackbody model.")
        if t_amb is None:
            a.missing.append("Ambient temperature (K).")
        irr = self.of_kind("irradiance")
        areas = self.of_kind("area")
        w, wi, eff, _ = self.claim()
        if w is None:
            i = self.single_power()
            if i is not None:
                w, wi = self.q[i].value, i
        g = None
        if irr:
            g = self.q[irr[0]].value
            self.record(irr[0], "Irradiance", field_name="irradiance_W_m2")
        area = None
        if areas:
            area = self.q[areas[0]].value
            self.record(areas[0], "Collection area", field_name="collection_area_m2")
        if area is None and w is None and eff is not None:
            area, g = 1.0, g or SOLAR_IRRADIANCE_W_M2
            a.assumptions.append("Only an efficiency is claimed: a 1 m² basis is used; the verdict does not depend on it.")
        if g is None:
            a.missing.append("Irradiance at the collection surface (W/m²).")
        if area is None:
            a.missing.append("Collection area (m²).")
        if w is None and eff is not None and area is not None and g is not None:
            w = eff * g * area / 1000.0
            a.inferred.append(f"Claimed output = {eff:.3g} × {g:g} W/m² × {area:g} m² = {w:.5g} kW.")
        if w is None:
            a.missing.append("The claimed electrical output or efficiency.")
        if a.missing:
            return
        if wi is not None:
            self.record(wi, "Claimed electrical output", field_name="W_out_kw")
        a.assumptions.append("The Petela–Landsberg bound for undiluted blackbody radiation is used. Real sunlight at "
                             "Earth is diluted and has less exergy, so this is a generous (conservative) upper bound.")
        a.mapping += [f"SOURCE: blackbody radiation from the sun at {t_src:g} K.",
                      f"SINK: environment at {t_amb:g} K.",
                      "Limit: η_L = 1 − (4/3)(T₀/T_s) + (1/3)(T₀/T_s)⁴ applied to the incident radiation."]
        a.model = "solar_blackbody"
        a.title = "Solar converter (Petela–Landsberg limit)"
        a.cases["solar_blackbody"] = {"model": "solar_blackbody", "title": a.title, "description": self.text,
                                      "T_source_K": t_src, "T_ambient_K": t_amb, "irradiance_W_m2": g,
                                      "collection_area_m2": area, "W_out_kw": w}

    # ---------------------------------------------------------------- chemical
    def route_chemical(self) -> None:
        a = self.a
        dg = self.of_kind("molar_energy")
        rates = self.of_kind("molar_rate")
        w, wi, eff, _ = self.claim()
        if w is None:
            i = self.single_power()
            if i is not None:
                w, wi = self.q[i].value, i
        fuel = None
        for name, (g0, mm, eqn, pattern) in FUEL_GIBBS.items():
            if re.search(pattern, self.text, re.IGNORECASE):
                fuel = name
                break
        delta_g = None
        direction = None
        m_dir = re.search(r"reaction(?:\s+direction)?\s*[:=]\s*([^;\n]+?)(?:;|\n|$|\.\s)", self.text, re.IGNORECASE)
        if m_dir:
            direction = m_dir.group(1).strip()
        if dg:
            # prefer the value labelled Gibbs / ΔG over enthalpy
            pick = next((i for i in dg if re.search(r"gibbs|ΔG|delta\s*G|free\s+energy", self.wide_left(i, 40), re.I)), None)
            if pick is None and len(dg) == 1 and not re.search(r"enthalpy|ΔH|delta\s*H|heating\s+value",
                                                               self.wide_left(dg[0], 40), re.I):
                pick = dg[0]
            if pick is not None:
                delta_g = self.q[pick].value
                if delta_g > 0 and not re.search(r"[-−+]\s*$", self.text[:self.q[pick].start]) and \
                        re.search(r"releas|liberat|availab|produc", self.wide_left(pick, 60), re.I):
                    delta_g = -delta_g
                    a.inferred.append("Gibbs energy described as released: sign taken as negative (ΔG < 0).")
                self.record(pick, "Reaction Gibbs energy ΔG", delta_g, "kJ/mol", "delta_G_kj_per_mol_rxn")
        if direction is None and fuel is not None:
            direction = FUEL_GIBBS[fuel][2]
        rate = None
        if rates:
            rate = self.q[rates[0]].value
            self.record(rates[0], "Reaction (fuel) rate", field_name="reaction_rate_mol_rxn_s")
        elif fuel is not None:
            mflows = self.of_kind("mass_flow")
            if mflows:
                rate = self.q[mflows[0]].value * 1000.0 / FUEL_GIBBS[fuel][1]
                self.record(mflows[0], f"{fuel.capitalize()} mass flow", field_name="fuel_mass_flow")
                a.inferred.append(f"Molar rate = mass flow / M = {self.q[mflows[0]].value * 1000:.5g} g/s ÷ "
                                  f"{FUEL_GIBBS[fuel][1]} g/mol = {rate:.5g} mol/s.")
        if delta_g is None:
            a.missing.append("Gibbs energy of reaction ΔG (kJ/mol) at the stated operating conditions.")
        if rate is None:
            a.missing.append("Reaction or fuel consumption rate (mol/s or g/s).")
        if direction is None:
            a.missing.append("The balanced reaction direction corresponding to the signed Gibbs energy.")
        if w is None and eff is not None and delta_g is not None and rate is not None:
            w = eff * (-delta_g) * rate
            a.inferred.append("Efficiency taken relative to −ΔG: W = η(−ΔG)ṅ.")
        extras = {}
        for i in dg:
            if re.search(r"enthalpy|ΔH|delta\s*H", self.ctx(i)[0][-45:], re.I):
                extras["delta_H_kj_per_mol_rxn"] = self.q[i].value
                self.record(i, "Reaction enthalpy DeltaH", field_name="delta_H_kj_per_mol_rxn")
        for i in self.of_kind("temperature"):
            left = self.ctx(i)[0][-55:]
            key = ("heat_boundary_temperature_K" if re.search(r"boundary|heat rejection", left, re.I)
                   else "reaction_temperature_K" if re.search(r"reaction|cell|reactor|operat", left, re.I) else None)
            if key:
                extras[key] = self.q[i].value
                self.record(i, key, field_name=key)
        heats = [i for i,q in enumerate(self.q) if q.kind == "power" and q.role in ("heat_in", "heat_out")]
        if len(heats) == 1:
            i = heats[0]
            extras["heat_into_reactor_kw"] = (-1 if self.q[i].role == "heat_out" else 1)*self.q[i].value
            self.record(i, "Signed heat into reactor", extras["heat_into_reactor_kw"], "kW", "heat_into_reactor_kw")
        if w is None and not all(k in extras for k in ("delta_H_kj_per_mol_rxn", "heat_into_reactor_kw")):
            a.missing.append("Electrical output, or reaction enthalpy and signed heat transfer to calculate it.")
        if a.missing:
            return
        if wi is not None:
            self.record(wi, "Claimed electrical output", field_name="W_out_kw")
        a.mapping += [f"SOURCE: chemical reaction {direction}.",
                      "Limit: maximum non-expansion work = −ΔG × reaction rate (isothermal, isobaric). "
                      "No Carnot temperature is used for an electrochemical device."]
        a.model = "reaction_gibbs"
        a.title = "Electrochemical converter (Gibbs energy limit)"
        a.cases["reaction_gibbs"] = {"model": "reaction_gibbs", "title": a.title, "description": self.text,
                                     "reaction_direction": direction, "delta_G_kj_per_mol_rxn": delta_g,
                                     "reaction_rate_mol_rxn_s": rate, "W_out_kw": w, **extras}

    # ---------------------------------------------------------------- other
    def route_no_input(self) -> None:
        """Claimed output with zero energy input: a First Law (perpetual-motion) test."""
        a = self.a
        self.assign_energy_roles()
        w, wi, eff, _ = self.claim()
        if w is None:
            i = self.single_power(exclude_roles=())
            if i is not None:
                w, wi = self.q[i].value, i
        if w is None:
            a.missing.append("The claimed output power.")
            return
        if wi is not None:
            self.record(wi, "Claimed output", field_name="W_out_kw")
        a.model = "mechanical_power"
        a.title = "Claimed output with no energy input (perpetual-motion test)"
        a.mapping += ["SOURCE: none — the description states that no energy enters the device.",
                      "Test: First Law. With zero input, the maximum steady output is zero."]
        a.cases["mechanical_power"] = {"model": "mechanical_power", "title": a.title, "description": self.text,
                                       "recoverable_mechanical_power_kw": 0.0, "W_out_kw": w}

    def route_work_consuming(self) -> None:
        a = self.a
        a.model = "unsupported_domain"
        a.title = "Work-consuming device"
        a.mapping.append("This device consumes work to move heat (refrigerator / heat pump). The tester evaluates "
                         "claims of producing useful work, so no feasibility verdict is given.")
        a.cases["unsupported_domain"] = {"model": "unsupported_domain", "title": a.title, "domain": "work-consuming",
                                         "description": self.text,
                                         "reason": "Refrigerators and heat pumps consume work; they are outside the "
                                                   "useful-work scope of this tester."}

    def route_unknown(self) -> None:
        a = self.a
        if self.of_kind("temperature"):
            a.domain = "thermal"
            self.build_streams()
            return self.route_thermal()
        a.missing.append("Describe the energy source (heat at a temperature, mechanical motion, wind, falling water, "
                         "sunlight or a chemical reaction) and the claimed output with units.")


# ============================================================================
# 4. Public entry point
# ============================================================================

def analyze_scenario(text: str) -> Dict[str, Any]:
    """Return a JSON-ready dictionary with the interpretation and engine result."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Enter a scenario description.")
    analyzer = ScenarioAnalyzer(text)
    try:
        analysis = analyzer.analyse()
    except thermo.ThermoInputError as error:
        analysis = analyzer.a
        analysis.issues.append(str(error))
        return {"status": "error", "route": analysis.model, "analysis": analysis.as_dict(),
                "source_quotes": analysis.quotes, "cases": {}, "result": None, "error": str(error)}
    if not analysis.model:
        analysis.model = _domain_route(analysis.domain)
    if analysis.missing or not analysis.cases:
        if not analysis.missing:
            analysis.missing.append("A complete description of the source, sink and claimed output.")
        return {"status": "needs_values", "route": analysis.model or _domain_route(analysis.domain),
                "analysis": analysis.as_dict(), "source_quotes": analysis.quotes, "cases": {}, "result": None,
                "issues": analysis.issues}
    case = list(analysis.cases.values())[-1]
    try:
        result = thermo.run_case(case)
    except thermo.ThermoInputError as error:
        return {"status": "error", "route": analysis.model, "analysis": analysis.as_dict(),
                "source_quotes": analysis.quotes, "cases": analysis.cases, "result": None,
                "error": f"The engine rejected the mapped inputs: {error}"}
    result_dict = result.as_dict()
    for row in result_dict.get("derivation", []):
        if row["source"] == "provided":
            row["source"] = "mapped"
            row["equation"] = "Mapped from the description; see extracted quotations and inference steps."
    result_dict["input_status"] = {row["field"]: row["source"].upper() for row in result_dict.get("derivation", [])}
    if analysis.assumptions:
        result_dict["verdict"]["reasons"].append(
            "This verdict uses the labelled assumptions listed under 'How the scenario was interpreted'.")
    return {"status": "evaluated", "route": result.model, "analysis": analysis.as_dict(),
            "source_quotes": analysis.quotes, "cases": analysis.cases, "result": result_dict, "questions": []}


def _domain_route(domain: str) -> Optional[str]:
    return {"mechanical": "mechanical_power", "wind": "mechanical_power", "hydro": "mechanical_power",
            "solar": "solar_blackbody", "chemical": "reaction_gibbs", "thermal": "fixed_reservoir_engine"}.get(domain)


def format_report(response: Dict[str, Any]) -> str:
    lines = ["=" * 78, "AUTOMATIC FEASIBILITY ANALYSIS", "=" * 78]
    an = response.get("analysis", {})
    lines.append(f"Domain : {DOMAIN_LABELS.get(an.get('domain'), an.get('domain'))}")
    lines.append(f"Model  : {an.get('title') or an.get('model')}")
    if an.get("extracted"):
        lines.append("\nEXTRACTED FROM THE TEXT")
        for e in an["extracted"]:
            value = e["value"]
            shown = f"{value:.6g}" if isinstance(value, float) else str(value)
            lines.append(f"  - {e['quantity']}: {shown} {e['unit']}   ← \"{e['quote']}\"")
    for title, key in (("SOURCE / SINK MAPPING", "mapping"), ("CALCULATED / INFERRED", "inferred"),
                       ("ASSUMPTIONS", "assumptions"), ("NOTES", "issues"), ("STILL NEEDED", "missing")):
        if an.get(key):
            lines.append(f"\n{title}")
            lines += [f"  - {item}" for item in an[key]]
    if response.get("error"):
        lines.append(f"\nERROR: {response['error']}")
    result = response.get("result")
    if result:
        calc = result.get("calculations", {})
        lines.append("\nKEY RESULTS")
        for key in ("carnot_efficiency_percent", "actual_efficiency_percent", "reversible_work_limit_kw",
                    "reversible_power_limit_kw", "effective_temperature_work_reference_kw",
                    "hot_effective_temperature_K", "cold_effective_temperature_K", "S_gen_dot_kw_per_k", "S_gen_kj_per_k"):
            if calc.get(key) is not None:
                lines.append(f"  {key}: {calc[key]:.6g}")
        lines.append("\nVERDICT: " + result["verdict"]["status"])
        lines += [f"  - {reason}" for reason in result["verdict"]["reasons"]]
    else:
        lines.append(f"\nSTATUS: {response['status']}")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Analyze a plain-English energy claim automatically.")
    parser.add_argument("scenario", nargs="*", help="Scenario text (reads stdin if omitted).")
    parser.add_argument("--json", action="store_true", help="Print the full JSON response.")
    args = parser.parse_args(argv)
    text = " ".join(args.scenario) if args.scenario else sys.stdin.read()
    response = analyze_scenario(text)
    print(json.dumps(response, indent=2, default=str) if args.json else format_report(response))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
