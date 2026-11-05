"""Password rules, shared by registration, new users, password changes and admin resets."""

import re

MIN_LENGTH = 10
# The most common passwords (normalised: lower case, letters and digits only).
COMMON = {
    "password", "password1", "password123", "passw0rd", "12345678", "123456789", "1234567890", "qwerty123",
    "qwertyuiop", "iloveyou", "admin123", "welcome1", "welcome123", "letmein1", "abc12345", "abcd1234",
    "india123", "salesway", "salesway1", "salesway123", "changeme", "changeme1",
}


def password_problem(password: str, email: str | None = None) -> str | None:
    """None if the password is acceptable, otherwise what's wrong with it, in plain words."""
    if len(password) < MIN_LENGTH:
        return f"Use at least {MIN_LENGTH} characters."
    if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        return "Use both letters and numbers."
    normalised = re.sub(r"[^a-z0-9]", "", password.lower())
    if normalised in COMMON:
        return "That password is too common. Choose something less guessable."
    local = (email or "").split("@")[0].lower()
    if len(local) >= 4 and local in password.lower():
        return "Don't include your email address in your password."
    return None


DEMO_PASSWORDS_FIXED = "Passwords can't be changed in the shared demo. Register your own organisation to try this."
