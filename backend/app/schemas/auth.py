from datetime import date, datetime, time
from uuid import UUID
from zoneinfo import available_timezones

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.domain import Language

_TIMEZONES = available_timezones()


def _check_tz(v: str | None) -> str | None:
    if v is not None and v not in _TIMEZONES:
        raise ValueError("Unknown IANA timezone")
    return v


def _check_dob(v: date | None) -> date | None:
    if v is not None and (v > date.today() or v.year < 1900):
        raise ValueError("Invalid date of birth")
    return v


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=128)
    display_name: str | None = Field(default=None, max_length=80)
    date_of_birth: date | None = None
    timezone: str = "Africa/Cairo"
    language: Language = Language.AR

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, v):
        return _check_tz(v)

    @field_validator("date_of_birth")
    @classmethod
    def validate_dob(cls, v):
        return _check_dob(v)


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(max_length=128)


class RefreshIn(BaseModel):
    refresh_token: str = Field(min_length=20, max_length=200)


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"  # noqa: S105
    expires_in: int


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: EmailStr
    display_name: str | None
    date_of_birth: date | None
    timezone: str
    language: str
    created_at: datetime


class UserPatch(BaseModel):
    display_name: str | None = Field(default=None, max_length=80)
    date_of_birth: date | None = None
    timezone: str | None = None
    language: Language | None = None

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, v):
        return _check_tz(v)

    @field_validator("date_of_birth")
    @classmethod
    def validate_dob(cls, v):
        return _check_dob(v)


class DeleteAccountIn(BaseModel):
    password: str = Field(max_length=128)
    confirm: bool = Field(description="Must be true — deletes ALL of the user's data permanently.")


class SettingsOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    period_reminders: bool
    symptom_reminders: bool
    weekly_summary: bool
    medical_alerts: bool
    reminder_time: time | None


class SettingsPatch(BaseModel):
    period_reminders: bool | None = None
    symptom_reminders: bool | None = None
    weekly_summary: bool | None = None
    medical_alerts: bool | None = None
    reminder_time: time | None = None
