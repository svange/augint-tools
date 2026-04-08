"""Standardization data models."""

from dataclasses import dataclass, field


@dataclass
class Finding:
    """A single standardization finding."""

    id: str
    section: str  # github, pipeline, quality, dotfiles, renovate, release
    severity: str  # error, warning, info
    subject: str  # file path or resource name
    actual: str
    expected: str
    can_fix: bool
    fix_kind: str  # generate, patch, replace, external, manual
    source: str  # rule/template that detected this

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "section": self.section,
            "severity": self.severity,
            "subject": self.subject,
            "actual": self.actual,
            "expected": self.expected,
            "can_fix": self.can_fix,
            "fix_kind": self.fix_kind,
            "source": self.source,
        }


@dataclass
class SectionResult:
    """Result of checking one section."""

    section: str
    status: str  # pass, drift, missing, skipped
    error_count: int = 0
    warning_count: int = 0


@dataclass
class AuditSummary:
    """Top-level audit summary."""

    total_errors: int = 0
    total_warnings: int = 0
    sections_checked: int = 0
    sections_passing: int = 0
    fixable_count: int = 0


@dataclass
class AuditResult:
    """Complete audit result."""

    profile_id: str
    sections: dict[str, SectionResult] = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)
    summary: AuditSummary = field(default_factory=AuditSummary)

    def to_dict(self) -> dict:
        return {
            "profile_id": self.profile_id,
            "sections": {
                k: {
                    "section": v.section,
                    "status": v.status,
                    "error_count": v.error_count,
                    "warning_count": v.warning_count,
                }
                for k, v in self.sections.items()
            },
            "findings": [f.to_dict() for f in self.findings],
            "summary": {
                "total_errors": self.summary.total_errors,
                "total_warnings": self.summary.total_warnings,
                "sections_checked": self.summary.sections_checked,
                "sections_passing": self.summary.sections_passing,
                "fixable_count": self.summary.fixable_count,
            },
        }


@dataclass
class FixAction:
    """A planned or applied fix."""

    finding_id: str
    fix_kind: str  # generate, patch, replace, external, manual
    target: str  # file path or resource
    description: str

    def to_dict(self) -> dict:
        return {
            "finding_id": self.finding_id,
            "fix_kind": self.fix_kind,
            "target": self.target,
            "description": self.description,
        }


@dataclass
class FixResult:
    """Result of applying fixes."""

    actions_planned: list[FixAction] = field(default_factory=list)
    actions_applied: list[FixAction] = field(default_factory=list)
    actions_failed: list[FixAction] = field(default_factory=list)
    files_changed: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "actions_planned": [a.to_dict() for a in self.actions_planned],
            "actions_applied": [a.to_dict() for a in self.actions_applied],
            "actions_failed": [a.to_dict() for a in self.actions_failed],
            "files_changed": self.files_changed,
        }
