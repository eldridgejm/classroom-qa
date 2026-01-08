"""
Configuration management for the application
"""

import tomllib
from pathlib import Path
from typing import Any

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class CourseConfig:
    """Configuration for a single course"""

    def __init__(self, slug: str, data: dict[str, Any]) -> None:
        self.slug = slug
        self.secret: str = data["secret"]
        self.name: str = data["name"]


class Settings(BaseSettings):
    """Application settings loaded from environment variables"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Redis configuration
    redis_url: str = Field(default="redis://localhost:6379", description="Redis connection URL")

    # Security
    secret_key: str = Field(
        default="dev-secret-key-change-in-production",
        description="Secret key for HMAC cookie signing",
    )

    # Rate limiting
    rate_limit_ask: int = Field(default=1, description="Questions allowed per window")
    rate_limit_window: int = Field(default=10, description="Rate limit window in seconds")
    max_question_length: int = Field(
        default=1000, description="Maximum student question length"
    )

    # Session management
    session_ttl: int = Field(
        default=1800, description="Session data TTL after end (seconds)"
    )

    # Courses file path
    courses_file: str = Field(default="courses.toml", description="Path to courses TOML file")

    # Courses cache
    _courses: dict[str, CourseConfig] | None = None

    def load_courses(self) -> dict[str, CourseConfig]:
        """Load courses from TOML file"""
        if self._courses is not None:
            return self._courses

        courses_path = Path(self.courses_file)
        if not courses_path.exists():
            raise FileNotFoundError(f"Courses file not found: {self.courses_file}")

        with open(courses_path, "rb") as f:
            data = tomllib.load(f)

        if "courses" not in data:
            raise ValueError("Invalid courses.toml: missing 'courses' section")

        self._courses = {
            slug: CourseConfig(slug, course_data)
            for slug, course_data in data["courses"].items()
        }

        return self._courses

    def get_course(self, slug: str) -> CourseConfig | None:
        """Get course configuration by slug"""
        courses = self.load_courses()
        return courses.get(slug)


# Global settings instance
settings = Settings()
