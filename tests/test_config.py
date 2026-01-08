"""
Tests for configuration management (Phase 1)

This test file covers:
- Courses TOML loading and validation
- Course lookup by slug
- Invalid TOML handling
- Missing courses.toml handling
- Environment variable loading
- Secret validation
- Course data structure validation
"""

from pathlib import Path

import pytest

from app.config import CourseConfig, Settings


class TestCoursesLoading:
    """Test cases for loading courses from TOML file"""

    def test_load_courses_success(self, test_settings: Settings) -> None:
        """Test that courses.toml loads correctly"""
        courses = test_settings.load_courses()

        # Check that courses were loaded
        assert len(courses) == 2
        assert "test-course" in courses
        assert "another-course" in courses

    def test_load_courses_caching(self, test_settings: Settings) -> None:
        """Test that courses are cached after first load"""
        courses1 = test_settings.load_courses()
        courses2 = test_settings.load_courses()

        # Should return the same instance (cached)
        assert courses1 is courses2

    def test_missing_courses_file(self, tmp_path: Path) -> None:
        """Test that missing courses.toml raises FileNotFoundError"""
        nonexistent_file = tmp_path / "nonexistent.toml"

        settings = Settings(courses_file=str(nonexistent_file))

        with pytest.raises(FileNotFoundError) as exc_info:
            settings.load_courses()

        assert "Courses file not found" in str(exc_info.value)
        assert str(nonexistent_file) in str(exc_info.value)

    def test_invalid_toml_syntax(self, tmp_path: Path) -> None:
        """Test that invalid TOML syntax raises an error"""
        import tomllib

        invalid_file = tmp_path / "invalid.toml"
        invalid_file.write_text("""
[courses.test
invalid syntax here
""")

        settings = Settings(courses_file=str(invalid_file))

        # tomllib will raise a TOMLDecodeError
        with pytest.raises(tomllib.TOMLDecodeError):
            settings.load_courses()

    def test_missing_courses_section(self, tmp_path: Path) -> None:
        """Test that TOML without 'courses' section raises ValueError"""
        invalid_file = tmp_path / "no_courses.toml"
        invalid_file.write_text("""
[settings]
foo = "bar"
""")

        settings = Settings(courses_file=str(invalid_file))

        with pytest.raises(ValueError) as exc_info:
            settings.load_courses()

        assert "missing 'courses' section" in str(exc_info.value)

    def test_empty_courses_section(self, tmp_path: Path) -> None:
        """Test that empty courses section returns empty dict"""
        empty_file = tmp_path / "empty.toml"
        empty_file.write_text("""
[courses]
""")

        settings = Settings(courses_file=str(empty_file))
        courses = settings.load_courses()

        assert len(courses) == 0
        assert courses == {}


class TestCourseConfig:
    """Test cases for CourseConfig model"""

    def test_course_config_creation(self) -> None:
        """Test creating a CourseConfig instance"""
        data = {
            "secret": "test-secret",
            "name": "Test Course",
        }

        course = CourseConfig("test-slug", data)

        assert course.slug == "test-slug"
        assert course.secret == "test-secret"
        assert course.name == "Test Course"

    def test_course_config_missing_secret(self) -> None:
        """Test that missing secret raises KeyError"""
        data = {
            "name": "Test Course",
        }

        with pytest.raises(KeyError):
            CourseConfig("test-slug", data)

    def test_course_config_missing_name(self) -> None:
        """Test that missing name raises KeyError"""
        data = {
            "secret": "test-secret",
        }

        with pytest.raises(KeyError):
            CourseConfig("test-slug", data)

    def test_course_config_empty_secret(self) -> None:
        """Test that empty secret is allowed but stored"""
        data = {
            "secret": "",
            "name": "Test Course",
        }

        course = CourseConfig("test-slug", data)
        assert course.secret == ""

    def test_course_config_empty_name(self) -> None:
        """Test that empty name is allowed but stored"""
        data = {
            "secret": "test-secret",
            "name": "",
        }

        course = CourseConfig("test-slug", data)
        assert course.name == ""


class TestCourseLookup:
    """Test cases for course lookup methods"""

    def test_get_course_exists(self, test_settings: Settings) -> None:
        """Test getting an existing course"""
        course = test_settings.get_course("test-course")

        assert course is not None
        assert course.slug == "test-course"
        assert course.name == "Test Course"
        assert course.secret == "test-secret-123"

    def test_get_course_another(self, test_settings: Settings) -> None:
        """Test getting another existing course"""
        course = test_settings.get_course("another-course")

        assert course is not None
        assert course.slug == "another-course"
        assert course.name == "Another Test Course"
        assert course.secret == "another-secret-456"

    def test_get_course_not_exists(self, test_settings: Settings) -> None:
        """Test getting a non-existent course returns None"""
        course = test_settings.get_course("nonexistent-course")

        assert course is None

    def test_get_course_empty_slug(self, test_settings: Settings) -> None:
        """Test getting course with empty slug returns None"""
        course = test_settings.get_course("")

        assert course is None

    def test_get_course_case_sensitive(self, test_settings: Settings) -> None:
        """Test that course slugs are case-sensitive"""
        # Lowercase exists
        course_lower = test_settings.get_course("test-course")
        assert course_lower is not None

        # Uppercase doesn't exist
        course_upper = test_settings.get_course("TEST-COURSE")
        assert course_upper is None


class TestEnvironmentVariables:
    """Test cases for environment variable loading"""

    def test_default_values(self) -> None:
        """Test that settings have default values"""
        settings = Settings()

        assert settings.redis_url == "redis://localhost:6379"
        assert settings.secret_key == "dev-secret-key-change-in-production"
        assert settings.rate_limit_ask == 1
        assert settings.rate_limit_window == 10
        assert settings.max_question_length == 1000
        assert settings.session_ttl == 1800
        assert settings.courses_file == "courses.toml"

    def test_custom_values(self) -> None:
        """Test that settings can be overridden"""
        settings = Settings(
            redis_url="redis://custom:6379",
            secret_key="custom-secret",
            rate_limit_ask=5,
            rate_limit_window=60,
            max_question_length=500,
            session_ttl=3600,
            courses_file="custom.toml",
        )

        assert settings.redis_url == "redis://custom:6379"
        assert settings.secret_key == "custom-secret"
        assert settings.rate_limit_ask == 5
        assert settings.rate_limit_window == 60
        assert settings.max_question_length == 500
        assert settings.session_ttl == 3600
        assert settings.courses_file == "custom.toml"

    def test_redis_url_configuration(self) -> None:
        """Test Redis URL configuration"""
        settings = Settings(redis_url="redis://prod-server:6380/2")
        assert settings.redis_url == "redis://prod-server:6380/2"

    def test_rate_limiting_configuration(self) -> None:
        """Test rate limiting configuration"""
        settings = Settings(rate_limit_ask=10, rate_limit_window=30)
        assert settings.rate_limit_ask == 10
        assert settings.rate_limit_window == 30


class TestSecretValidation:
    """Test cases for secret validation"""

    def test_course_secret_matches(self, test_settings: Settings) -> None:
        """Test that course secrets are stored correctly"""
        course = test_settings.get_course("test-course")

        assert course is not None
        assert course.secret == "test-secret-123"

    def test_different_courses_different_secrets(
        self, test_settings: Settings
    ) -> None:
        """Test that different courses have different secrets"""
        course1 = test_settings.get_course("test-course")
        course2 = test_settings.get_course("another-course")

        assert course1 is not None
        assert course2 is not None
        assert course1.secret != course2.secret

    def test_secret_validation_function(self, test_settings: Settings) -> None:
        """Test secret validation against courses.toml"""
        course = test_settings.get_course("test-course")

        assert course is not None

        # Valid secret
        assert course.secret == "test-secret-123"

        # Invalid secret
        assert course.secret != "wrong-secret"

    def test_secret_is_not_empty_in_config(self, test_settings: Settings) -> None:
        """Test that configured secrets are not empty"""
        courses = test_settings.load_courses()

        for _slug, course in courses.items():
            # All test courses should have non-empty secrets
            assert course.secret != ""
            assert len(course.secret) > 0


class TestDataStructureValidation:
    """Test cases for validating course data structure"""

    def test_valid_course_structure(self, tmp_path: Path) -> None:
        """Test that valid course structure is accepted"""
        valid_file = tmp_path / "valid.toml"
        valid_file.write_text("""
[courses.valid-course]
secret = "valid-secret"
name = "Valid Course Name"
""")

        settings = Settings(courses_file=str(valid_file))
        courses = settings.load_courses()

        assert len(courses) == 1
        assert "valid-course" in courses

        course = courses["valid-course"]
        assert course.slug == "valid-course"
        assert course.secret == "valid-secret"
        assert course.name == "Valid Course Name"

    def test_multiple_courses_structure(self, tmp_path: Path) -> None:
        """Test that multiple courses are loaded correctly"""
        multi_file = tmp_path / "multi.toml"
        multi_file.write_text("""
[courses.course1]
secret = "secret1"
name = "Course One"

[courses.course2]
secret = "secret2"
name = "Course Two"

[courses.course3]
secret = "secret3"
name = "Course Three"
""")

        settings = Settings(courses_file=str(multi_file))
        courses = settings.load_courses()

        assert len(courses) == 3
        assert "course1" in courses
        assert "course2" in courses
        assert "course3" in courses

    def test_course_with_special_characters_in_name(self, tmp_path: Path) -> None:
        """Test course with special characters in name"""
        special_file = tmp_path / "special.toml"
        special_file.write_text("""
[courses.special-course]
secret = "secret"
name = "Course: Data Science & Machine Learning (2025)"
""")

        settings = Settings(courses_file=str(special_file))
        courses = settings.load_courses()

        course = courses["special-course"]
        assert course.name == "Course: Data Science & Machine Learning (2025)"

    def test_course_with_long_secret(self, tmp_path: Path) -> None:
        """Test course with a long secret"""
        long_file = tmp_path / "long.toml"
        long_secret = "a" * 100
        long_file.write_text(f"""
[courses.long-secret-course]
secret = "{long_secret}"
name = "Long Secret Course"
""")

        settings = Settings(courses_file=str(long_file))
        courses = settings.load_courses()

        course = courses["long-secret-course"]
        assert course.secret == long_secret
        assert len(course.secret) == 100

    def test_course_slug_formats(self, tmp_path: Path) -> None:
        """Test various valid course slug formats"""
        slug_file = tmp_path / "slugs.toml"
        slug_file.write_text("""
[courses.dsc80-wi25]
secret = "s1"
name = "DSC 80"

[courses.intro-to-python]
secret = "s2"
name = "Intro to Python"

[courses.cs101]
secret = "s3"
name = "CS 101"
""")

        settings = Settings(courses_file=str(slug_file))
        courses = settings.load_courses()

        assert len(courses) == 3
        assert "dsc80-wi25" in courses
        assert "intro-to-python" in courses
        assert "cs101" in courses

