from fastapi import APIRouter

from app.api.deps import DB, CurrentUser, medical_snapshot
from app.models import HealthProfile, User, UserSettings
from app.schemas.auth import SettingsOut, SettingsPatch, UserOut, UserPatch
from app.schemas.tracking import HealthProfileIn, HealthProfileOut, MutationResult

router = APIRouter(prefix="/users/me", tags=["users"])


@router.get("", response_model=UserOut)
def get_me(user: CurrentUser) -> User:
    return user


@router.patch("", response_model=UserOut)
def patch_me(body: UserPatch, user: CurrentUser, db: DB) -> User:
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(user, k, v.value if hasattr(v, "value") else v)
    db.commit()
    db.refresh(user)
    return user


def _settings(db, user) -> UserSettings:
    s = db.get(UserSettings, user.id)
    if s is None:
        s = UserSettings(user_id=user.id)
        db.add(s)
        db.flush()
        db.refresh(s)
    return s


@router.get("/settings", response_model=SettingsOut)
def get_settings_(user: CurrentUser, db: DB) -> UserSettings:
    s = _settings(db, user)
    db.commit()
    return s


@router.patch("/settings", response_model=SettingsOut)
def patch_settings(body: SettingsPatch, user: CurrentUser, db: DB) -> UserSettings:
    s = _settings(db, user)
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(s, k, v)
    db.commit()
    db.refresh(s)
    return s


@router.get("/health-profile", response_model=HealthProfileOut | None)
def get_health_profile(user: CurrentUser, db: DB):
    return db.get(HealthProfile, user.id)


@router.put("/health-profile", response_model=MutationResult[HealthProfileOut])
def put_health_profile(body: HealthProfileIn, user: CurrentUser, db: DB):
    """Onboarding screens 3–4. All conditions are user-reported, never app-generated."""
    hp = db.get(HealthProfile, user.id) or HealthProfile(user_id=user.id)
    for k, v in body.model_dump().items():
        setattr(hp, k, v.value if hasattr(v, "value") else v)
    db.add(hp)
    db.flush()
    snap = medical_snapshot(db, user)
    db.commit()
    db.refresh(hp)
    return MutationResult(data=HealthProfileOut.model_validate(hp), medical=snap)
