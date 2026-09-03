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
    assert parsed["method"] == "POST"
    assert parsed["uri"] == "/login"
    assert parsed["query"] == [("next", "/admin")]
    assert parsed["headers"]["cookie"] == ["session=important"]
    assert parsed["body"] == '{"username":"admin"}'
