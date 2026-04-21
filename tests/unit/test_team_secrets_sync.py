"""Tests for team_secrets.sync module."""

from augint_tools.team_secrets.sync import (
    compute_merge,
    parse_dotenv_content,
    serialize_dotenv,
)


class TestParseDotenvContent:
    def test_simple(self):
        content = "KEY=value\nOTHER=stuff\n"
        result = parse_dotenv_content(content)
        assert result == {"KEY": "value", "OTHER": "stuff"}

    def test_quoted_values(self):
        content = "KEY=\"hello world\"\nSINGLE='quoted'\n"
        result = parse_dotenv_content(content)
        assert result == {"KEY": "hello world", "SINGLE": "quoted"}

    def test_comments_and_blanks(self):
        content = "# comment\n\nKEY=value\n\n# another\nOTHER=x\n"
        result = parse_dotenv_content(content)
        assert result == {"KEY": "value", "OTHER": "x"}

    def test_export_prefix(self):
        content = "export KEY=value\nexport OTHER=stuff\n"
        result = parse_dotenv_content(content)
        assert result == {"KEY": "value", "OTHER": "stuff"}

    def test_inline_comments(self):
        content = "KEY=value # this is a comment\n"
        result = parse_dotenv_content(content)
        assert result == {"KEY": "value"}

    def test_no_value(self):
        content = "KEY=\n"
        result = parse_dotenv_content(content)
        assert result == {"KEY": ""}

    def test_equals_in_value(self):
        content = "URL=postgres://user:pass@host/db?sslmode=require\n"
        result = parse_dotenv_content(content)
        assert result["URL"] == "postgres://user:pass@host/db?sslmode=require"

    def test_malformed_lines_skipped(self):
        content = "GOOD=value\nno_equals_here\nALSO_GOOD=x\n"
        result = parse_dotenv_content(content)
        assert result == {"GOOD": "value", "ALSO_GOOD": "x"}


class TestSerializeDotenv:
    def test_simple(self):
        data = {"KEY": "value", "OTHER": "stuff"}
        result = serialize_dotenv(data)
        assert "KEY=value\n" in result
        assert "OTHER=stuff\n" in result

    def test_quoting(self):
        data = {"KEY": "hello world"}
        result = serialize_dotenv(data)
        assert 'KEY="hello world"' in result

    def test_empty_dict(self):
        assert serialize_dotenv({}) == ""

    def test_sorted_keys(self):
        data = {"Z": "1", "A": "2", "M": "3"}
        result = serialize_dotenv(data)
        lines = result.strip().split("\n")
        assert lines[0].startswith("A=")
        assert lines[1].startswith("M=")
        assert lines[2].startswith("Z=")


class TestComputeMerge:
    def test_no_conflicts(self):
        team = {"A": "1", "B": "2"}
        local = {"A": "1", "B": "2"}
        result = compute_merge(team, local)
        assert result.conflicts == []
        assert result.unchanged == ["A", "B"]
        assert result.merged == {"A": "1", "B": "2"}

    def test_additions(self):
        team = {"A": "1"}
        local = {"A": "1", "NEW": "new_val"}
        result = compute_merge(team, local)
        assert result.additions == ["NEW"]
        assert result.merged["NEW"] == "new_val"
        assert result.conflicts == []

    def test_team_only_keys(self):
        team = {"A": "1", "TEAM_ONLY": "secret"}
        local = {"A": "1"}
        result = compute_merge(team, local)
        assert result.merged["TEAM_ONLY"] == "secret"
        assert result.conflicts == []

    def test_conflicts(self):
        team = {"A": "team_val"}
        local = {"A": "local_val"}
        result = compute_merge(team, local)
        assert len(result.conflicts) == 1
        assert result.conflicts[0].key == "A"
        assert result.conflicts[0].team_value == "team_val"
        assert result.conflicts[0].local_value == "local_val"
        # Conflicting key not in merged
        assert "A" not in result.merged

    def test_mixed(self):
        team = {"SHARED": "same", "CONFLICT": "team_v", "TEAM_ONLY": "t"}
        local = {"SHARED": "same", "CONFLICT": "local_v", "LOCAL_ONLY": "l"}
        result = compute_merge(team, local)
        assert result.unchanged == ["SHARED"]
        assert result.additions == ["LOCAL_ONLY"]
        assert len(result.conflicts) == 1
        assert result.conflicts[0].key == "CONFLICT"
        assert result.merged["SHARED"] == "same"
        assert result.merged["TEAM_ONLY"] == "t"
        assert result.merged["LOCAL_ONLY"] == "l"
