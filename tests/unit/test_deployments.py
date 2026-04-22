"""Tests for the deployment-links store."""

from __future__ import annotations

from pathlib import Path

import pytest

from augint_tools.dashboard import deployments as dep
from augint_tools.dashboard._data import RepoStatus


def _status(
    name: str = "aillc-web",
    full_name: str | None = None,
    *,
    tags: tuple[str, ...] = (),
    looks_like_service: bool = False,
    is_org: bool = False,
) -> RepoStatus:
    return RepoStatus(
        name=name,
        full_name=full_name or f"augmentingintegrations/{name}",
        has_dev_branch=False,
        main_status="success",
        main_error=None,
        dev_status=None,
        dev_error=None,
        open_issues=0,
        open_prs=0,
        draft_prs=0,
        tags=tags,
        looks_like_service=looks_like_service,
        is_org=is_org,
    )


@pytest.fixture
def yaml_path(tmp_path: Path) -> Path:
    return tmp_path / "deployments.yaml"


class TestLoadDeployments:
    def test_missing_file_returns_empty(self, yaml_path: Path):
        assert dep.load_deployments(yaml_path) == {}

    def test_malformed_yaml_returns_empty(self, yaml_path: Path):
        yaml_path.write_text("this is: [not: valid: yaml")
        assert dep.load_deployments(yaml_path) == {}

    def test_top_level_list_returns_empty(self, yaml_path: Path):
        yaml_path.write_text("- just\n- a\n- list\n")
        assert dep.load_deployments(yaml_path) == {}

    def test_round_trip(self, yaml_path: Path):
        yaml_path.write_text(
            "org/repo:\n"
            "  - {label: dev,  url: 'https://dev.example.com'}\n"
            "  - {label: main, url: 'https://example.com'}\n"
        )
        loaded = dep.load_deployments(yaml_path)
        assert set(loaded.keys()) == {"org/repo"}
        labels = [link.label for link in loaded["org/repo"]]
        assert labels == ["dev", "main"]
        assert all(link.source == "yaml" for link in loaded["org/repo"])

    def test_drops_malformed_entries(self, yaml_path: Path):
        yaml_path.write_text(
            "org/repo:\n"
            "  - {label: dev, url: 'https://ok.example'}\n"
            "  - 'not a dict'\n"
            "  - {label: nourl}\n"
            "  - {url: nolabel}\n"
        )
        loaded = dep.load_deployments(yaml_path)
        assert [link.label for link in loaded["org/repo"]] == ["dev"]


class TestMutations:
    def test_add_creates_file(self, yaml_path: Path):
        assert not yaml_path.exists()
        dep.add_link("org/repo", "main", "https://example.com", path=yaml_path)
        loaded = dep.load_deployments(yaml_path)
        assert [link.label for link in loaded["org/repo"]] == ["main"]
        assert yaml_path.exists()

    def test_add_appends_to_existing(self, yaml_path: Path):
        dep.add_link("org/repo", "dev", "https://dev.example", path=yaml_path)
        dep.add_link("org/repo", "main", "https://example.com", path=yaml_path)
        loaded = dep.load_deployments(yaml_path)
        assert [link.label for link in loaded["org/repo"]] == ["dev", "main"]

    def test_remove_only_drops_matching_entry(self, yaml_path: Path):
        dep.add_link("org/repo", "dev", "https://dev.example", path=yaml_path)
        dep.add_link("org/repo", "main", "https://example.com", path=yaml_path)
        dep.remove_link("org/repo", "dev", "https://dev.example", path=yaml_path)
        loaded = dep.load_deployments(yaml_path)
        assert [link.label for link in loaded["org/repo"]] == ["main"]

    def test_remove_last_entry_removes_repo_key(self, yaml_path: Path):
        dep.add_link("org/repo", "main", "https://example.com", path=yaml_path)
        dep.remove_link("org/repo", "main", "https://example.com", path=yaml_path)
        assert dep.load_deployments(yaml_path) == {}

    def test_remove_noop_when_missing(self, yaml_path: Path):
        # Should not raise even if the repo or entry doesn't exist.
        dep.remove_link("org/missing", "dev", "https://none", path=yaml_path)
        assert dep.load_deployments(yaml_path) == {}

    def test_update_replaces_entry(self, yaml_path: Path):
        dep.add_link("org/repo", "dev", "https://old.example", path=yaml_path)
        dep.update_link(
            "org/repo",
            "dev",
            "https://old.example",
            "dev",
            "https://new.example",
            path=yaml_path,
        )
        loaded = dep.load_deployments(yaml_path)
        assert loaded["org/repo"][0].url == "https://new.example"


class TestResolveLinks:
    def test_empty_yaml_no_library(self, yaml_path: Path):
        st = _status(tags=("sam",), looks_like_service=True)
        assert dep.resolve_links(st, yaml_path) == []

    def test_library_auto_pypi(self, yaml_path: Path):
        # Pure Python library: py tag, no IaC tags, no dev branch.
        st = _status(name="augint-tools", tags=("py",))
        links = dep.resolve_links(st, yaml_path)
        assert len(links) == 1
        assert links[0].label == "pypi"
        assert links[0].source == "auto"
        assert links[0].url == "https://pypi.org/project/augint-tools/"

    def test_service_no_auto_pypi(self, yaml_path: Path):
        st = _status(name="ai-lls-api", tags=("py",), looks_like_service=True)
        assert dep.resolve_links(st, yaml_path) == []

    def test_org_repo_no_auto_pypi(self, yaml_path: Path):
        st = _status(name="aillc-org", tags=("py",), is_org=True)
        assert dep.resolve_links(st, yaml_path) == []

    def test_iac_tags_suppress_auto_pypi(self, yaml_path: Path):
        """Repos with pyproject.toml + IaC tags use uv for dev tooling, not packaging."""
        for iac_tag in ("cdk", "tf", "next", "vite"):
            st = _status(name=f"myrepo-{iac_tag}", tags=("py", iac_tag))
            assert dep.resolve_links(st, yaml_path) == [], (
                f"py + {iac_tag} should not get auto pypi"
            )

    def test_sam_tag_does_not_suppress_auto_pypi(self, yaml_path: Path):
        """Python libraries with SAM for test infra still get auto pypi."""
        st = _status(name="my-lib", tags=("py", "sam"))
        links = dep.resolve_links(st, yaml_path)
        assert len(links) == 1
        assert links[0].label == "pypi"

    def test_dev_branch_suppresses_auto_pypi(self, yaml_path: Path):
        """Repos with a dev branch follow a deploy workflow, not a library release."""
        st = RepoStatus(
            name="my-service",
            full_name="org/my-service",
            has_dev_branch=True,
            main_status="success",
            main_error=None,
            dev_status="success",
            dev_error=None,
            open_issues=0,
            open_prs=0,
            draft_prs=0,
            tags=("py",),
        )
        assert dep.resolve_links(st, yaml_path) == []

    def test_manual_pypi_overrides_auto(self, yaml_path: Path):
        st = _status(name="augint-tools", tags=("py",))
        dep.add_link(
            st.full_name,
            "pypi",
            "https://pypi.org/project/custom-name/",
            path=yaml_path,
        )
        links = dep.resolve_links(st, yaml_path)
        # The auto entry must be suppressed because a manual pypi already exists.
        assert len(links) == 1
        assert links[0].source == "yaml"
        assert links[0].url == "https://pypi.org/project/custom-name/"

    def test_manual_links_plus_auto_pypi(self, yaml_path: Path):
        st = _status(name="augint-tools", tags=("py",))
        dep.add_link(st.full_name, "main", "https://example.com", path=yaml_path)
        links = dep.resolve_links(st, yaml_path)
        labels = [link.label for link in links]
        # Auto pypi gets appended after manual entries.
        assert labels == ["main", "pypi"]
        assert links[-1].source == "auto"


class TestDisplayHelpers:
    def test_sort_order_puts_reserved_first(self):
        links = [
            dep.DeploymentLink("jacksonhealthcare", "u1"),
            dep.DeploymentLink("pypi", "u2", source="auto"),
            dep.DeploymentLink("dashboard", "u3"),
            dep.DeploymentLink("dev", "u4"),
            dep.DeploymentLink("main", "u5"),
        ]
        ordered = [link.label for link in dep.sort_links_for_display(links)]
        # main, dev, pypi, then others in original list order.
        assert ordered == ["main", "dev", "pypi", "jacksonhealthcare", "dashboard"]

    @pytest.mark.parametrize(
        "label, glyph",
        [
            ("dev", "s"),
            ("main", "p"),
            ("pypi", "π"),
            ("dashboard", "d"),
            ("jacksonhealthcare", "j"),
            ("Staging", "s"),
            ("", "?"),
        ],
    )
    def test_tag_glyph(self, label: str, glyph: str):
        assert dep.tag_glyph(label) == glyph

    def test_find_link(self):
        links = [
            dep.DeploymentLink("dev", "u1"),
            dep.DeploymentLink("main", "u2"),
        ]
        assert dep.find_link(links, "main").url == "u2"
        assert dep.find_link(links, "pypi") is None


class TestSaveDeployments:
    def test_auto_entries_are_not_persisted(self, yaml_path: Path):
        links = {
            "org/repo": [
                dep.DeploymentLink("main", "https://example.com"),
                dep.DeploymentLink("pypi", "https://pypi.org/project/x/", source="auto"),
            ]
        }
        dep.save_deployments(links, path=yaml_path)
        reloaded = dep.load_deployments(yaml_path)
        assert [link.label for link in reloaded["org/repo"]] == ["main"]
