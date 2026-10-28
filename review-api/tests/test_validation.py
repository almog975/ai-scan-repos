import pytest
from pydantic import ValidationError

from app.schemas import ReviewCreate, normalize_github_url


@pytest.mark.parametrize("url,expected", [
    ("https://github.com/almog975/FinalProject", "https://github.com/almog975/FinalProject"),
    ("https://github.com/a/b.git", "https://github.com/a/b"),
    ("  https://github.com/a-b/c_d.e/ ", "https://github.com/a-b/c_d.e"),
])
def test_valid(url, expected):
    assert normalize_github_url(url) == expected


@pytest.mark.parametrize("url", [
    "http://github.com/a/b", "https://gitlab.com/a/b", "https://github.com/a",
    "https://github.com/a/b/tree/main", "https://github.com.evil.com/a/b", "file:///etc/passwd",
    "https://github.com/a/b;rm -rf", "https://github.com/-a/b", "", "https://github.com/a/..",
])
def test_invalid(url):
    with pytest.raises(ValueError):
        normalize_github_url(url)


def test_depth_enum():
    with pytest.raises(ValidationError):
        ReviewCreate(repo_url="https://github.com/a/b", depth="extreme")
    assert ReviewCreate(repo_url="https://github.com/a/b", depth="deep").depth.value == "deep"
