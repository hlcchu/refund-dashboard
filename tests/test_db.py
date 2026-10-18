import struct

from app.db import SQL_COPT_SS_ACCESS_TOKEN, inject_entra_token, odbc_token_struct


def test_odbc_token_struct_is_length_prefixed_utf16():
    packed = odbc_token_struct("abc")
    (length,) = struct.unpack("<I", packed[:4])
    assert length == 6
    assert packed[4:].decode("utf-16-le") == "abc"


def test_fresh_entra_token_injected_per_connection():
    tokens = iter(["t1", "t2"])
    for expected in ("t1", "t2"):
        cparams: dict = {"attrs_before": {99: b"keep"}}
        inject_entra_token(cparams, lambda: next(tokens))
        assert cparams["attrs_before"][SQL_COPT_SS_ACCESS_TOKEN] == odbc_token_struct(expected)
        assert cparams["attrs_before"][99] == b"keep"
