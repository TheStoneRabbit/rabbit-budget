import json
import os
from contextlib import contextmanager
from typing import Dict, List, Optional

from sqlalchemy import Boolean, Column, Float, ForeignKey, Integer, String, UniqueConstraint, create_engine, func
from sqlalchemy.orm import declarative_base, relationship, sessionmaker
from werkzeug.security import check_password_hash, generate_password_hash


DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///rabbit.db")
SQLITE_PREFIXES = ("sqlite:///", "sqlite:////")

engine_kwargs = {}
if DATABASE_URL.startswith(SQLITE_PREFIXES):
    engine_kwargs["connect_args"] = {"check_same_thread": False}

engine = create_engine(DATABASE_URL, **engine_kwargs)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

Base = declarative_base()


class NotFoundError(Exception):
    """Raised when a requested resource cannot be located."""


class ConflictError(Exception):
    """Raised when attempting to create a resource that already exists."""


class Profile(Base):
    __tablename__ = "profiles"

    id = Column(Integer, primary_key=True)
    name = Column(String(128), unique=True, nullable=False)

    categories = relationship("Category", back_populates="profile", cascade="all, delete-orphan")
    rules = relationship("Rule", back_populates="profile", cascade="all, delete-orphan")
    settings = relationship("ProfileSetting", back_populates="profile", cascade="all, delete-orphan", uselist=False)


class Category(Base):
    __tablename__ = "categories"

    id = Column(Integer, primary_key=True)
    profile_id = Column(Integer, ForeignKey("profiles.id"), nullable=False)
    name = Column(String(128), nullable=False)
    budget = Column(Float, default=0)

    profile = relationship("Profile", back_populates="categories")

    __table_args__ = (
        UniqueConstraint("profile_id", "name", name="uq_category_profile_name"),
    )


class Rule(Base):
    __tablename__ = "rules"

    id = Column(Integer, primary_key=True)
    profile_id = Column(Integer, ForeignKey("profiles.id"), nullable=False)
    keyword = Column(String(255), nullable=False)
    category_name = Column(String(128), nullable=False)

    profile = relationship("Profile", back_populates="rules")

    __table_args__ = (
        UniqueConstraint("profile_id", "keyword", name="uq_rule_profile_keyword"),
    )


class ProfileSetting(Base):
    __tablename__ = "profile_settings"

    id = Column(Integer, primary_key=True)
    profile_id = Column(Integer, ForeignKey("profiles.id"), nullable=False, unique=True)
    is_private = Column(Boolean, default=False)
    password_hash = Column(String(255), nullable=True)

    profile = relationship("Profile", back_populates="settings")


@contextmanager
def session_scope():
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db() -> None:
    """Create tables and bootstrap data from legacy JSON if present."""
    Base.metadata.create_all(bind=engine)
    _bootstrap_from_json()

def create_profile(name: str) -> Dict[str, str]:
    normalized = (name or "").strip()
    if not normalized:
        raise ValueError("Profile name is required.")

    with session_scope() as session:
        existing = (
            session.query(Profile)
            .filter(func.lower(Profile.name) == normalized.lower())
            .one_or_none()
        )
        if existing:
            raise ConflictError(f"Profile '{normalized}' already exists.")

        profile = Profile(name=normalized)
        session.add(profile)
        session.flush()
        _ensure_settings(session, profile.id)
        session.flush()
        return {"name": profile.name}

def delete_profile(name: str) -> None:
    normalized = (name or "").strip()
    if not normalized:
        raise ValueError("Profile name is required.")

    with session_scope() as session:
        profile = (
            session.query(Profile)
            .filter(func.lower(Profile.name) == normalized.lower())
            .one_or_none()
        )
        if profile is None:
            raise NotFoundError(f"Profile '{normalized}' not found.")

        session.delete(profile)


def _ensure_settings(session, profile_id: int) -> ProfileSetting:
    settings = (
        session.query(ProfileSetting)
        .filter(ProfileSetting.profile_id == profile_id)
        .one_or_none()
    )
    if settings is None:
        settings = ProfileSetting(profile_id=profile_id, is_private=False, password_hash=None)
        session.add(settings)
        session.flush()
    return settings


def get_profile_settings(profile_name: str) -> Dict[str, Optional[bool]]:
    with session_scope() as session:
        profile = _require_profile(session, profile_name)
        settings = _ensure_settings(session, profile.id)
        return {
            "is_private": bool(settings.is_private),
            "has_password": bool(settings.password_hash),
        }


def set_profile_privacy(profile_name: str, is_private: bool, password: Optional[str]) -> Dict[str, bool]:
    with session_scope() as session:
        profile = _require_profile(session, profile_name)
        settings = _ensure_settings(session, profile.id)

        if is_private:
            if not password:
                raise ValueError("Password is required to enable privacy.")
            settings.password_hash = generate_password_hash(password)
            settings.is_private = True
        else:
            settings.is_private = False
            settings.password_hash = None

        session.add(settings)
        session.flush()
        return {"is_private": bool(settings.is_private), "has_password": bool(settings.password_hash)}


def change_profile_password(profile_name: str, old_password: str, new_password: str) -> None:
    if not new_password:
        raise ValueError("New password is required.")
    with session_scope() as session:
        profile = _require_profile(session, profile_name)
        settings = _ensure_settings(session, profile.id)
        if not settings.password_hash or not settings.is_private:
            raise ValueError("Privacy is not enabled for this profile.")
        if not old_password or not check_password_hash(settings.password_hash, old_password):
            raise ValueError("Current password is incorrect.")
        settings.password_hash = generate_password_hash(new_password)
        session.add(settings)
        session.flush()


def verify_profile_password(profile_name: str, password: str) -> bool:
    with session_scope() as session:
        profile = _require_profile(session, profile_name)
        settings = _ensure_settings(session, profile.id)
        if not settings.password_hash or not settings.is_private:
            return True
        if not password:
            return False
        return check_password_hash(settings.password_hash, password)


def _bootstrap_from_json(base_path: str = "profiles") -> None:
    """Backfill the database from the existing JSON directory, if empty."""
    if not os.path.isdir(base_path):
        return

    with session_scope() as session:
        existing_profiles = session.query(Profile).count()
        if existing_profiles > 0:
            return

        for entry in sorted(os.listdir(base_path)):
            profile_path = os.path.join(base_path, entry)
            if not os.path.isdir(profile_path):
                continue

            profile = Profile(name=entry)
            session.add(profile)
            session.flush()  # ensure profile.id is available
            _ensure_settings(session, profile.id)

            categories_file = os.path.join(profile_path, "categories.json")
            if os.path.exists(categories_file):
                with open(categories_file, "r", encoding="utf-8") as fh:
                    for record in json.load(fh):
                        name = (record.get("name") or "").strip()
                        if not name:
                            continue
                        budget_raw = record.get("budget", 0)
                        try:
                            budget = float(budget_raw)
                        except (TypeError, ValueError):
                            budget = 0.0
                        session.add(Category(profile_id=profile.id, name=name, budget=budget))

            rules_file = os.path.join(profile_path, "category_rules.json")
            if os.path.exists(rules_file):
                with open(rules_file, "r", encoding="utf-8") as fh:
                    rules_payload = json.load(fh)
                for keyword, category in rules_payload.items():
                    keyword_clean = (keyword or "").strip()
                    if not keyword_clean:
                        continue
                    session.add(
                        Rule(
                            profile_id=profile.id,
                            keyword=keyword_clean.upper(),
                            category_name=(category or "").strip(),
                        )
                    )


def list_profiles() -> List[str]:
    with session_scope() as session:
        profiles = session.query(Profile).order_by(Profile.name.asc()).all()
        return [profile.name for profile in profiles]


def _require_profile(session, profile_name: str) -> Profile:
    profile = (
        session.query(Profile)
        .filter(func.lower(Profile.name) == profile_name.lower())
        .one_or_none()
    )
    if profile is None:
        raise NotFoundError(f"Profile '{profile_name}' not found.")
    return profile


def list_categories(profile_name: str) -> List[Dict]:
    with session_scope() as session:
        profile = _require_profile(session, profile_name)
        categories = (
            session.query(Category)
            .filter(Category.profile_id == profile.id)
            .order_by(Category.name.asc())
            .all()
        )
        return [{"name": c.name, "budget": c.budget} for c in categories]


def create_category(profile_name: str, name: str, budget: float) -> Dict:
    normalized = name.strip()
    if not normalized:
        raise ValueError("Category name is required.")

    with session_scope() as session:
        profile = _require_profile(session, profile_name)
        existing = (
            session.query(Category)
            .filter(Category.profile_id == profile.id, func.lower(Category.name) == normalized.lower())
            .one_or_none()
        )
        if existing:
            raise ConflictError(f"Category '{normalized}' already exists.")

        category = Category(profile_id=profile.id, name=normalized, budget=budget)
        session.add(category)
        session.flush()
        return {"name": category.name, "budget": category.budget}


def update_category(profile_name: str, original_name: str, new_name: str, budget: float) -> Dict:
    normalized = new_name.strip()
    if not normalized:
        raise ValueError("Category name cannot be empty.")

    with session_scope() as session:
        profile = _require_profile(session, profile_name)
        category = (
            session.query(Category)
            .filter(Category.profile_id == profile.id, func.lower(Category.name) == original_name.lower())
            .one_or_none()
        )
        if category is None:
            raise NotFoundError(f"Category '{original_name}' not found.")

        if normalized.lower() != category.name.lower():
            conflict = (
                session.query(Category)
                .filter(Category.profile_id == profile.id, func.lower(Category.name) == normalized.lower())
                .one_or_none()
            )
            if conflict:
                raise ConflictError(f"Category '{normalized}' already exists.")

        category.name = normalized
        category.budget = budget
        session.add(category)
        session.flush()
        return {"name": category.name, "budget": category.budget}


def delete_category(profile_name: str, name: str) -> None:
    with session_scope() as session:
        profile = _require_profile(session, profile_name)
        category = (
            session.query(Category)
            .filter(Category.profile_id == profile.id, func.lower(Category.name) == name.lower())
            .one_or_none()
        )
        if category is None:
            raise NotFoundError(f"Category '{name}' not found.")
        session.delete(category)


def list_rules(profile_name: str) -> Dict[str, str]:
    with session_scope() as session:
        profile = _require_profile(session, profile_name)
        rules = (
            session.query(Rule)
            .filter(Rule.profile_id == profile.id)
            .order_by(Rule.keyword.asc())
            .all()
        )
        return {rule.keyword: rule.category_name for rule in rules}


def create_rule(profile_name: str, keyword: str, category_name: str) -> Dict[str, str]:
    keyword_normalized = keyword.strip().upper()
    if not keyword_normalized:
        raise ValueError("Rule keyword is required.")

    category_normalized = category_name.strip()
    if not category_normalized:
        raise ValueError("Rule category is required.")

    with session_scope() as session:
        profile = _require_profile(session, profile_name)
        existing = (
            session.query(Rule)
            .filter(Rule.profile_id == profile.id, Rule.keyword == keyword_normalized)
            .one_or_none()
        )
        if existing:
            raise ConflictError(f"Rule for keyword '{keyword_normalized}' already exists.")

        rule = Rule(profile_id=profile.id, keyword=keyword_normalized, category_name=category_normalized)
        session.add(rule)
        session.flush()
        return {rule.keyword: rule.category_name}


def update_rule(profile_name: str, keyword: str, new_keyword: str, category_name: str) -> Dict[str, str]:
    new_keyword_normalized = new_keyword.strip().upper()
    if not new_keyword_normalized:
        raise ValueError("Rule keyword cannot be empty.")
    category_normalized = category_name.strip()
    if not category_normalized:
        raise ValueError("Rule category cannot be empty.")

    with session_scope() as session:
        profile = _require_profile(session, profile_name)
        rule = (
            session.query(Rule)
            .filter(Rule.profile_id == profile.id, Rule.keyword == keyword.strip().upper())
            .one_or_none()
        )
        if rule is None:
            raise NotFoundError(f"Rule '{keyword}' not found.")

        if new_keyword_normalized != rule.keyword:
            conflict = (
                session.query(Rule)
                .filter(Rule.profile_id == profile.id, Rule.keyword == new_keyword_normalized)
                .one_or_none()
            )
            if conflict:
                raise ConflictError(f"Rule for keyword '{new_keyword_normalized}' already exists.")

        rule.keyword = new_keyword_normalized
        rule.category_name = category_normalized
        session.add(rule)
        session.flush()
        return {rule.keyword: rule.category_name}


def delete_rule(profile_name: str, keyword: str) -> None:
    with session_scope() as session:
        profile = _require_profile(session, profile_name)
        rule = (
            session.query(Rule)
            .filter(Rule.profile_id == profile.id, Rule.keyword == keyword.strip().upper())
            .one_or_none()
        )
        if rule is None:
            raise NotFoundError(f"Rule '{keyword}' not found.")
        session.delete(rule)


def upsert_rule(profile_name: str, keyword: str, category_name: str) -> Dict[str, str]:
    keyword_normalized = keyword.strip().upper()
    category_normalized = category_name.strip()
    if not keyword_normalized:
        raise ValueError("Rule keyword is required.")
    if not category_normalized:
        category_normalized = "Uncategorized"

    with session_scope() as session:
        profile = _require_profile(session, profile_name)
        rule = (
            session.query(Rule)
            .filter(Rule.profile_id == profile.id, Rule.keyword == keyword_normalized)
            .one_or_none()
        )
        if rule is None:
            rule = Rule(profile_id=profile.id, keyword=keyword_normalized, category_name=category_normalized)
            session.add(rule)
        else:
            rule.category_name = category_normalized
            session.add(rule)
        session.flush()
        return {rule.keyword: rule.category_name}


def profile_exists(profile_name: str) -> bool:
    with session_scope() as session:
        result = (
            session.query(Profile.id)
            .filter(func.lower(Profile.name) == profile_name.lower())
            .first()
        )
        return result is not None
