import pytest
from pydantic import ValidationError

from app.schemas.auth import LoginRequest, RegisterRequest

VALID = {"email": "alice@example.com", "password": "Str0ngPassw0rd", "display_name": "Alice"}


def test_valid_registration_is_accepted() -> None:
    request = RegisterRequest(**VALID)

    assert request.email == "alice@example.com"


def test_email_is_trimmed_and_lowercased() -> None:
    request = RegisterRequest(**{**VALID, "email": "  Alice.Smith@Example.COM "})

    assert request.email == "alice.smith@example.com"


@pytest.mark.parametrize(
    "email", ["", "plain", "a@", "@example.com", "a b@example.com", "a@@b.com"]
)
def test_invalid_emails_are_rejected(email: str) -> None:
    with pytest.raises(ValidationError):
        RegisterRequest(**{**VALID, "email": email})


@pytest.mark.parametrize(
    ("password", "fragment"),
    [
        ("Sh0rt", "at least 8"),
        ("alllowercase1", "uppercase"),
        ("ALLUPPERCASE1", "lowercase"),
        ("NoDigitsHere", "digit"),
        ("Aa1" + "x" * 70, "at most 72 bytes"),
    ],
)
def test_weak_passwords_are_rejected_with_a_reason(password: str, fragment: str) -> None:
    with pytest.raises(ValidationError) as error:
        RegisterRequest(**{**VALID, "password": password})

    assert fragment in str(error.value)


def test_validation_messages_do_not_echo_the_password() -> None:
    with pytest.raises(ValidationError) as error:
        RegisterRequest(**{**VALID, "password": "weakpassword"})

    for detail in error.value.errors():
        assert "weakpassword" not in detail["msg"]


@pytest.mark.parametrize("name", ["", "   ", "x" * 101, "bad\x00name", "line\nbreak", "tab\tname"])
def test_invalid_display_names_are_rejected(name: str) -> None:
    with pytest.raises(ValidationError):
        RegisterRequest(**{**VALID, "display_name": name})


def test_display_name_is_trimmed() -> None:
    assert RegisterRequest(**{**VALID, "display_name": "  Alice  "}).display_name == "Alice"


@pytest.mark.parametrize("extra", ["role", "is_active", "id", "password_hash", "is_superuser"])
def test_unknown_fields_are_rejected_to_block_mass_assignment(extra: str) -> None:
    with pytest.raises(ValidationError, match="Extra inputs"):
        RegisterRequest(**VALID, **{extra: "admin"})


def test_login_does_not_apply_the_password_policy() -> None:
    assert LoginRequest(email="a@example.com", password="x").password == "x"


def test_login_rejects_empty_and_oversized_passwords() -> None:
    with pytest.raises(ValidationError):
        LoginRequest(email="a@example.com", password="")
    with pytest.raises(ValidationError):
        LoginRequest(email="a@example.com", password="x" * 257)
