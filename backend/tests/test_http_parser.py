import pytest

from app.services.http_parser import parse_http_payload


def test_parser_keeps_cookie_query_and_body():
    raw = (
        "POST /login?next=%2Fadmin HTTP/1.1\r\n"
        "Host: example.internal\r\n"
        "Cookie: session=important\r\n"
        "Content-Type: application/json\r\n\r\n"
        '{"username":"admin"}'
    )
    parsed = parse_http_payload(raw)
    assert parsed["parse_status"] == "success"
    assert parsed["method"] == "POST"
    assert parsed["uri"] == "/login"
    assert parsed["query"] == [("next", "/admin")]
    assert parsed["headers"]["cookie"] == ["session=important"]
    assert parsed["body"] == '{"username":"admin"}'


@pytest.mark.parametrize("raw", [
    "",
    "not an HTTP request",
    '{"message": "synthetic data"}',
    "HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n\r\nhello",
    "GET ",
    "GET\t/search\tHTTP/1.1\r\n\r\n",
])
def test_unrecognized_payload_is_failed_not_a_successful_request(raw):
    parsed = parse_http_payload(raw)
    assert parsed["parse_status"] == "failed"
    assert parsed["method"] is None
    assert parsed["uri"] is None
    assert parsed["protocol"] is None
    assert parsed["headers"] == {}
    assert "request_line_not_recognized" in parsed["warnings"]


@pytest.mark.parametrize("request_line", [
    "GET /search?q=test",
    "GET /search?q=test not-http",
    "GET /search?q=test HTTP/1.1 trailing-text",
    "GET /search?q=test HTTP/1.1\t",
])
def test_recognizable_request_with_missing_or_invalid_protocol_is_partial(request_line):
    parsed = parse_http_payload(request_line + "\r\nHost: synthetic.test\r\n\r\n")
    assert parsed["parse_status"] == "partial"
    assert parsed["method"] == "GET"
    assert parsed["uri"] == "/search"
    assert parsed["query"] == [("q", "test")]
    assert parsed["protocol"] is None
    assert parsed["request_line"] == request_line
    assert parsed["warnings"] == ["request_protocol_not_recognized"]


@pytest.mark.parametrize("raw", [
    "GET / HTTP/1.1",
    "GET / HTTP/1.1\r\nHost: synthetic.test",
    "GET / HTTP/1.1\nHost: synthetic.test\n",
])
def test_missing_header_terminator_is_partial(raw):
    parsed = parse_http_payload(raw)
    assert parsed["parse_status"] == "partial"
    assert "headers_terminator_missing" in parsed["warnings"]


@pytest.mark.parametrize("target", [
    "http://[synthetic-invalid/",
    "http://[not-an-ip]/",
    "http://synthetic.test:invalid/",
    "http://synthetic.test:99999/",
    "http://synthetic\uff0ftest/",
    "http:///missing-authority",
    "/search?input=synthetic\x00data",
])
def test_malformed_request_target_does_not_raise_or_echo_error_details(target):
    parsed = parse_http_payload(f"GET {target} HTTP/1.1\r\nHost: synthetic.test\r\n\r\n")
    assert parsed["parse_status"] == "partial"
    assert parsed["warnings"] == ["request_target_not_recognized"]
    assert parsed["headers"]["host"] == ["synthetic.test"]


@pytest.mark.parametrize("method,target", [
    ("GET", "/search?q=value#fragment"),
    ("GET", "http://synthetic.test/search?q=value#fragment"),
    ("CONNECT", "synthetic.test:443#fragment"),
])
def test_literal_fragment_is_partial_and_kept_in_raw_target(method, target):
    request_line = f"{method} {target} HTTP/1.1"
    parsed = parse_http_payload(request_line + "\r\nHost: synthetic.test\r\n\r\n")
    assert parsed["parse_status"] == "partial"
    assert parsed["warnings"] == ["request_target_not_recognized"]
    assert parsed["request_line"] == request_line
    assert parsed["uri"] == target
    assert parsed["query"] == []


def test_encoded_fragment_is_query_data_and_is_not_rejected_as_a_literal_fragment():
    parsed = parse_http_payload("GET /search?q=value%23fragment HTTP/1.1\r\n\r\n")
    assert parsed["parse_status"] == "success"
    assert parsed["query"] == [("q", "value#fragment")]


@pytest.mark.parametrize("line", [
    "missing-colon",
    ": empty-name",
    "Host : whitespace-before-colon",
    " continuation-line: unsupported-fold",
    "X-Synthetic: invalid\x00value",
    "X-Synthetic: invalid\rvalue",
])
def test_malformed_header_is_partial_without_losing_other_headers_or_body(line):
    parsed = parse_http_payload(
        f"POST / HTTP/1.1\r\n{line}\r\nCookie: fixture=value\r\n\r\nraw\r\nbody"
    )
    assert parsed["parse_status"] == "partial"
    assert parsed["warnings"] == ["header_line_not_recognized"]
    assert parsed["headers"] == {"cookie": ["fixture=value"]}
    assert parsed["body"] == "raw\r\nbody"


@pytest.mark.parametrize("target,method,uri", [
    ("/search?q=%2Fadmin&q=&q=two", "GET", "/search"),
    ("http://synthetic.test/search?q=value", "GET", "/search"),
    ("https://[2001:db8::1]:443/search?q=value", "GET", "/search"),
    ("//[literal-origin-path?q=value", "GET", "//[literal-origin-path"),
    ("*", "OPTIONS", "*"),
    ("synthetic.test:443", "CONNECT", "synthetic.test:443"),
    ("[2001:db8::1]:443", "CONNECT", "[2001:db8::1]:443"),
    ("/dav", "PROPFIND", "/dav"),
])
def test_standard_request_forms_and_extension_methods_remain_supported(target, method, uri):
    parsed = parse_http_payload(f"{method} {target} HTTP/1.1\r\nHost: synthetic.test\r\n\r\n")
    assert parsed["parse_status"] == "success"
    assert parsed["method"] == method
    assert parsed["uri"] == uri
    assert parsed["warnings"] == []


@pytest.mark.parametrize("line_ending", ["\r\n", "\n"])
def test_duplicate_headers_and_query_values_are_preserved(line_ending):
    parsed = parse_http_payload(line_ending.join([
        "GET /?q=%2Fadmin&q=&q=two HTTP/1.0", "X-Test: one", "x-test:\ttwo\t", "", "",
    ]))
    assert parsed["parse_status"] == "success"
    assert parsed["headers"] == {"x-test": ["one", "two"]}
    assert parsed["query"] == [("q", "/admin"), ("q", ""), ("q", "two")]


def test_literal_export_newlines_are_flagged_but_never_silently_decoded():
    raw = r"POST / HTTP/1.1\r\nCookie: synthetic=value\r\n\r\nbody\nvalue"
    parsed = parse_http_payload(raw)
    assert parsed["parse_status"] == "partial"
    assert "escaped_line_endings_not_decoded" in parsed["warnings"]
    assert parsed["request_line"] == raw
    assert parsed["headers"] == {}
    assert parsed["body"] == ""


def test_first_header_boundary_wins_and_literal_body_escapes_stay_untouched():
    body = '  {"text":"literal\\n and \\r\\n"}\r\n\r\ntrailing\n'
    raw = "POST / HTTP/1.1\nHost: synthetic.test\n\n" + body
    parsed = parse_http_payload(raw)
    assert parsed["parse_status"] == "success"
    assert parsed["body"] == body
    assert parsed["request_line"] == "POST / HTTP/1.1"


def test_repeated_invalid_headers_do_not_create_unbounded_warning_entries():
    parsed = parse_http_payload("GET / HTTP/1.1\r\n" + "synthetic-invalid\r\n" * 1000 + "\r\n")
    assert parsed["parse_status"] == "partial"
    assert parsed["warnings"] == ["header_line_not_recognized"]
