"""Parser facade. Preserve versioned implementations when adding new engines.

Do not edit parser_engine_v1.py or http_parser.py to implement a new version:
add a new bundled engine and dispatch its explicit ID here instead.
"""
from .parser_engine_v1 import (
    CAPTURE_ENGINE, DEFAULT_OPTIONS, LEGACY_ENGINE, MAX_CAPTURE_CHARS,
    valid_source_spans,
)
from . import parser_engine_v1 as legacy
from . import parser_engine_v2 as current

CAPTURE_ENGINE_V2 = current.CAPTURE_ENGINE_V2


def validate_options(engine, options):
    return current.validate_options(options) if engine == CAPTURE_ENGINE_V2 else legacy.validate_options(engine, options)


def parse_capture_payload(raw, engine, options, *, waf_vendor=None):
    if engine == CAPTURE_ENGINE_V2:
        return current.parse_capture_payload(raw, options, waf_vendor=waf_vendor)
    return legacy.parse_capture_payload(raw, engine, options, waf_vendor=waf_vendor)
