from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator

from app.core.passwords import password_problem


class RegisterRequest(BaseModel):
    organization_name: str = Field(min_length=2, max_length=200)
    industry: str | None = Field(default=None, max_length=100)
    timezone: str = "Asia/Kolkata"
    full_name: str = Field(min_length=1, max_length=200)
    email: EmailStr
    # bcrypt only uses the first 72 bytes.
    password: str = Field(min_length=8, max_length=72)

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("Unknown timezone") from None
        return value

    @model_validator(mode="after")
    def strong_password(self):
        if problem := password_problem(self.password, self.email):
            raise ValueError(problem)
        return self


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(max_length=72)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(max_length=72)
    new_password: str = Field(max_length=72)


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
