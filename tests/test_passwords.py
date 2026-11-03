import pytest

from app import passwords
from app.passwords import PasswordRejected, check_new_password, hash_password, verify_password


def rejected(password: str) -> PasswordRejected:
    with pytest.raises(PasswordRejected) as caught:
        check_new_password(password)
    return caught.value


def test_a_password_needs_at_least_eight_characters():
    error = rejected("abc1234")
    assert error.code == "password_too_weak"
    assert "8" in error.message
    check_new_password("kettle-purple-17")  # long enough, not common
    check_new_password("u7!kQ2#x")  # exactly 8


def test_the_length_counts_characters_not_bytes():
    check_new_password("ñandú-ñandú")
    rejected("ñandú12")  # 7 characters, 9 bytes


def test_a_password_can_be_at_most_128_characters():
    check_new_password("k" * 63 + "-" + "q" * 64)  # exactly 128
    error = rejected("k" * 63 + "-" + "q" * 65)
    assert error.code == "password_too_weak" and "128" in error.message


@pytest.mark.parametrize("common", ["password", "Password1", "QWERTY123", "iloveyou", "12345678"])
def test_common_passwords_are_refused_whatever_the_case(common):
    error = rejected(common)
    assert error.code == "password_too_weak"
    assert "common" in error.message


def test_there_are_no_composition_rules():
    check_new_password("only lowercase words here")
    check_new_password("1357924680135")  # digits only, but not a common one


def test_spaces_are_part_of_the_password():
    check_new_password("      x  y  z     ")  # 8+ characters even though most are spaces


def test_the_common_list_has_only_lower_case_entries_of_the_minimum_length():
    words = passwords.common_passwords()
    assert len(words) >= 250
    assert all(word == word.casefold() and len(word) >= passwords.MIN_LENGTH for word in words)


def test_a_hash_is_argon2id_and_salted():
    first, second = hash_password("kettle-purple-17"), hash_password("kettle-purple-17")
    assert first.startswith("$argon2id$") and first != second
    assert verify_password(first, "kettle-purple-17") is True
    assert verify_password(first, "kettle-purple-18") is False


def test_a_missing_or_damaged_hash_never_matches():
    assert verify_password(None, "kettle-purple-17") is False
    assert verify_password("not a hash", "kettle-purple-17") is False
    assert verify_password("", "") is False


def test_an_unknown_account_still_costs_one_hash_check(monkeypatch):
    """The login runs the password against a dummy hash when there is no account (or it has no
    password), so the response time does not tell the two cases apart."""
    seen = []
    real = passwords._hasher

    class Spy:
        def verify(self, stored, given):
            seen.append(stored)
            return real.verify(stored, given)

    monkeypatch.setattr(passwords, "_hasher", Spy())
    assert verify_password(None, "kettle-purple-17") is False
    assert seen == [passwords.DUMMY_HASH]


def test_an_absurdly_long_login_password_is_refused_without_hashing(monkeypatch):

    class Refuses:
        def verify(self, *args):
            pytest.fail("a huge password must not be hashed")

    monkeypatch.setattr(passwords, "_hasher", Refuses())
    assert verify_password(passwords.DUMMY_HASH, "x" * (passwords.MAX_LOGIN_LENGTH + 1)) is False
