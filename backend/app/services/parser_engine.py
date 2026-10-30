"""Parser facade. Preserve versioned implementations when adding new engines.

Do not edit parser_engine_v1.py or http_parser.py to implement a new version:
add a new bundled engine and dispatch its explicit ID here instead.
"""
from .parser_engine_v1 import (
    CAPTURE_ENGINE, DEFAULT_OPTIONS, LEGACY_ENGINE, MAX_CAPTURE_CHARS,
    parse_capture_payload, valid_source_spans, validate_options,
)
