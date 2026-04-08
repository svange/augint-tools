"""Check plan resolution."""

from dataclasses import dataclass, field

from augint_tools.checks.phases import PRESETS, Phase
from augint_tools.detection.commands import CommandPlan


@dataclass
class CheckPhase:
    """A single resolved phase in a check plan."""

    name: str
    command: str
    required: bool = True
    fixable: bool = False


@dataclass
class CheckPlan:
    """Resolved validation plan ready for execution."""

    preset: str
    phases: list[CheckPhase] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "preset": self.preset,
            "phases": [
                {"name": p.name, "command": p.command, "required": p.required, "fixable": p.fixable}
                for p in self.phases
            ],
            "skipped": self.skipped,
        }


# Phases where mechanical fixes can be attempted
_FIXABLE_PHASES = {Phase.QUALITY}


def resolve_plan(
    command_plan: CommandPlan,
    preset: str = "default",
    skip: list[str] | None = None,
) -> CheckPlan:
    """Build a check plan from a command plan, preset, and skip list.

    Args:
        command_plan: Resolved commands per phase from detection engine.
        preset: Preset name (quick, default, full, ci).
        skip: Phase names to skip.

    Returns:
        CheckPlan ready for execution.
    """
    skip_set = {s.lower() for s in (skip or [])}

    # Resolve which phases to include
    if preset == "ci":
        # CI mirrors "full" for now; could be resolved from CI config later
        phase_list = PRESETS["full"]
    else:
        phase_list = PRESETS.get(preset, PRESETS["default"])

    # Map phases to commands
    phase_commands: dict[Phase, str | None] = {
        Phase.QUALITY: command_plan.quality,
        Phase.SECURITY: command_plan.security,
        Phase.LICENSES: command_plan.licenses,
        Phase.TESTS: command_plan.tests,
        Phase.BUILD: command_plan.build,
    }

    plan = CheckPlan(preset=preset)
    skipped: list[str] = []

    for phase in phase_list:
        if phase.value in skip_set:
            skipped.append(phase.value)
            continue

        command = phase_commands.get(phase)
        if command is None:
            skipped.append(phase.value)
            continue

        plan.phases.append(
            CheckPhase(
                name=phase.value,
                command=command,
                required=True,
                fixable=phase in _FIXABLE_PHASES,
            )
        )

    plan.skipped = skipped
    return plan
