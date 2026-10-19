"""Google Vertex AI Agent Engine exports (docs/SPEC.md section 7.3)."""

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

PLATFORM = "vertex"

OWNER_LABEL = "ritvo-owner"
OWNER_TEAM_LABEL = "ritvo-owner-team"

EXPORT_NOTE = "the reasoningEngines.list REST method, saved as JSON"
EXPORT_COMMANDS = (
    'curl -H "Authorization: Bearer $(gcloud auth print-access-token)" '
    '"https://<location>-aiplatform.googleapis.com/v1/projects/<project>/locations/'
    '<location>/reasoningEngines" > vertex-reasoning-engines.json',
)

_REMEDY = "export it with the reasoningEngines.list REST method: " + EXPORT_COMMANDS[0]
_PARTIAL = (
    "holds only one page of reasoning engines (nextPageToken is set); re-export so the "
    "whole list is in one file: raise pageSize, or combine the reasoningEngines of every "
    "page into one JSON array"
)


def _label(
    labels: object, key: str, agent_id: str, path: Path, position: int, warnings: list[str]
) -> str | None:
    if not isinstance(labels, dict):
        return None
    value = labels.get(key)
    if value is None or value == "":
        return None
    if not isinstance(value, str) or not is_id(value):
        warnings.append(
            f"{path}: agent {position} ({agent_id}): label {key} is not a valid ID; "
            "it was ignored and the field stays unknown"
        )
        return None
    return value


def read(path: Path) -> ExportRead:
    """Copy only the mapped fields; spec.deploymentSpec and everything else are left alone."""
    data, sha256 = load_json(path, _REMEDY)
    if isinstance(data, dict) and "reasoningEngines" in data:
        if data.get("nextPageToken"):
            raise RegistryError(f"{path}: {_PARTIAL}")
        engines = records(data["reasoningEngines"], "reasoningEngines", path, _REMEDY)
    elif isinstance(data, list):
        engines = records(data, "the file", path, _REMEDY)
    elif isinstance(data, dict) and "name" in data:
        engines = [data]
    else:
        raise RegistryError(
            f"{path}: not a Vertex AI reasoningEngines list, array or single engine; {_REMEDY}"
        )

    agents = []
    warnings: list[str] = []
    for position, engine in enumerate(engines, start=1):
        name = required(engine, "name", path, position)
        agent_id = f"{PLATFORM}.{name.rsplit('/', 1)[-1]}"
        if not is_id(agent_id):
            raise RegistryError(f"{path}: agent {position}: name does not end in a valid ID")
        spec = engine.get("spec")
        if spec is not None and not isinstance(spec, dict):
            raise RegistryError(f"{path}: agent {position}: spec is not a JSON object")
        spec = spec or {}
        labels = engine.get("labels")
        agents.append(
            (
                position,
                RegistryAgent(
                    id=agent_id,
                    name=text(engine, "displayName", path, position),
                    platform=PLATFORM,
                    platform_ref=name,
                    framework=text(spec, "agentFramework", path, position),
                    owner=_label(labels, OWNER_LABEL, agent_id, path, position, warnings),
                    owner_team=_label(labels, OWNER_TEAM_LABEL, agent_id, path, position, warnings),
                    credential=(
                        text(spec, "effectiveIdentity", path, position)
                        or text(spec, "serviceAccount", path, position)
                    ),
                ),
            )
        )
    return ExportRead(sha256=sha256, agents=tuple(agents), warnings=tuple(warnings))
