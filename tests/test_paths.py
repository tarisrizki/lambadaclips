import pytest
import os
from app_core.paths import safe_filename, safe_join, validate_uuid

def test_safe_filename():
    assert safe_filename("hello world.mp4") == "hello_world.mp4"
    assert safe_filename("../../test.txt") == "test.txt"
    assert safe_filename("C:\\Windows\\System32\\cmd.exe") == "C_Windows_System32_cmd.exe"

def test_safe_join():
    base = "/var/www"
    assert safe_join(base, "file.txt") == os.path.normpath("/var/www/file.txt")

def test_validate_uuid():
    valid_uuid = "123e4567-e89b-12d3-a456-426614174000"
    assert validate_uuid(valid_uuid, "test") == valid_uuid

    with pytest.raises(Exception):
        validate_uuid("invalid-uuid", "test")
