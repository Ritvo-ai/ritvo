"""AWS Bedrock AgentCore Runtime exports (docs/SPEC.md section 7.3)."""

from pathlib import Path

from ritvo.connectors.registry.common import (
    ExportRead,
    RegistryAgent,
    RegistryError,
    is_id,
    load_json,
    records,
    required,
    text,
)

PLATFORM = "agentcore"

EXPORT_NOTE = (
    "the runtime list, and optionally one get-agent-runtime file per runtime, "
    "which adds the role it runs as"
)
EXPORT_COMMANDS = (
    "aws bedrock-agentcore-control list-agent-runtimes --output json > agentcore-runtimes.json",
    "aws bedrock-agentcore-control get-agent-runtime --agent-runtime-id <id> --output json "
    "> agentcore-runtime-<id>.json",
)

_REMEDY = "export it with: " + "; or ".join(EXPORT_COMMANDS)
_PARTIAL = (
    "holds only one page of runtimes (nextToken is set); re-export without --max-items "
    "or --no-paginate so the whole list is in one file"
)


def read(path: Path) -> ExportRead:
    """Copy only the mapped fields; environment variables and everything else are left alone."""
    data, sha256 = load_json(path, _REMEDY)
    if isinstance(data, dict) and "agentRuntimes" in data:
        if data.get("nextToken"):
            raise RegistryError(f"{path}: {_PARTIAL}")
        runtimes = records(data["agentRuntimes"], "agentRuntimes", path, _REMEDY)
        get_output = False
    elif isinstance(data, dict) and "agentRuntimeArn" in data:
        runtimes = [data]
        get_output = True
    else:
        raise RegistryError(
            f"{path}: not an AgentCore list-agent-runtimes or get-agent-runtime output; {_REMEDY}"
        )

    agents = []
    for position, runtime in enumerate(runtimes, start=1):
        arn = required(runtime, "agentRuntimeArn", path, position)
        name = required(runtime, "agentRuntimeName", path, position)
        agent_id = f"{PLATFORM}.{name.lower()}"
        if not is_id(agent_id):
            raise RegistryError(
                f"{path}: agent {position}: agentRuntimeName does not give a valid agent ID"
            )
        agents.append(
            (
                position,
                RegistryAgent(
                    id=agent_id,
                    name=name,
                    platform=PLATFORM,
                    platform_ref=arn,
                    framework=None,
                    # AgentCore exports carry no tags, so there is no owner to read.
                    owner=None,
                    owner_team=None,
                    credential=(text(runtime, "roleArn", path, position) if get_output else None),
                ),
            )
        )
    return ExportRead(sha256=sha256, agents=tuple(agents), warnings=())
