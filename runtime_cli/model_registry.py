from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Union


# Current UI model ids are sourced from Antigravity GetUserStatus
# userStatus.cascadeModelConfigData.clientModelConfigs. The selected preference
# in state.vscdb stores MODEL_PLACEHOLDER_M37 as numeric 1037.
DEFAULT_MODEL_ID = 1037

KNOWN_ENUM_MODEL_IDS: Dict[str, int] = {
    "MODEL_OPENAI_GPT_OSS_120B_MEDIUM": 342,
    "OPENAI_GPT_OSS_120B_MEDIUM": 342,
}

MODEL_OPTIONS = [
    {"id": DEFAULT_MODEL_ID, "label": "Gemini 3.1 Pro (High)", "default": True},
    {"id": 1036, "label": "Gemini 3.1 Pro (Low)"},
    {"id": 1084, "label": "Gemini 3 Flash"},
    {"id": 1035, "label": "Claude Sonnet 4.6 (Thinking)"},
    {"id": 1026, "label": "Claude Opus 4.6 (Thinking)"},
    {"id": 342, "label": "GPT-OSS 120B (Medium)"},
]


def _load_json_source(raw: str) -> Any:
    candidate = Path(raw).expanduser()
    if candidate.exists():
        return json.loads(candidate.read_text(encoding="utf-8"))
    return json.loads(raw)


def _normalize_model_options(payload: Any) -> List[dict[str, Any]]:
    if isinstance(payload, dict):
        payload = payload.get("models", [])
    if not isinstance(payload, list):
        raise ValueError("model config must be a list or an object with a models list")

    options: List[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError("each model config item must be an object")
        if "id" not in item or "label" not in item:
            raise ValueError("each model config item must include id and label")
        normalized = {
            "id": int(item["id"]),
            "label": str(item["label"]),
        }
        if bool(item.get("default")):
            normalized["default"] = True
        options.append(normalized)

    if not options:
        raise ValueError("model config must include at least one model")
    if not any(item.get("default") for item in options):
        options[0]["default"] = True
    return options


def model_enum_id(value: Any) -> Optional[int]:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        placeholder = re.fullmatch(r"(?:MODEL_)?PLACEHOLDER_M(\d+)", value)
        if placeholder:
            return 1000 + int(placeholder.group(1))
        return KNOWN_ENUM_MODEL_IDS.get(value)
    return None


def _model_or_alias_id(model_or_alias: Any) -> Optional[int]:
    if not isinstance(model_or_alias, dict):
        return None
    if "model" in model_or_alias:
        return model_enum_id(model_or_alias["model"])
    choice = model_or_alias.get("choice")
    if isinstance(choice, dict) and choice.get("case") == "model":
        return model_enum_id(choice.get("value"))
    return None


def extract_model_options_from_user_status(payload: Any) -> List[dict[str, Any]]:
    user_status = payload.get("userStatus") if isinstance(payload, dict) else None
    if not isinstance(user_status, dict):
        user_status = payload if isinstance(payload, dict) else {}

    config_data = user_status.get("cascadeModelConfigData")
    if not isinstance(config_data, dict):
        return []

    raw_configs = config_data.get("clientModelConfigs") or []
    configs_by_label: Dict[str, dict[str, Any]] = {}
    for raw in raw_configs:
        if not isinstance(raw, dict) or raw.get("disabled"):
            continue
        label = raw.get("label")
        model_id = _model_or_alias_id(raw.get("modelOrAlias"))
        if not label or model_id is None:
            continue
        option: dict[str, Any] = {"id": model_id, "label": str(label)}
        if raw.get("supportsImages"):
            option["supportsImages"] = True
        if raw.get("tagTitle"):
            option["tagTitle"] = str(raw["tagTitle"])
        configs_by_label[str(label)] = option

    ordered_labels: List[str] = []
    for sort in config_data.get("clientModelSorts") or []:
        if not isinstance(sort, dict):
            continue
        for group in sort.get("groups") or []:
            if isinstance(group, dict):
                ordered_labels.extend(str(label) for label in group.get("modelLabels") or [])

    options: List[dict[str, Any]] = []
    for label in ordered_labels:
        option = configs_by_label.pop(label, None)
        if option:
            options.append(option)
    options.extend(configs_by_label.values())

    default_id = _model_or_alias_id((config_data.get("defaultOverrideModelConfig") or {}).get("modelOrAlias"))
    if default_id is None:
        default_id = DEFAULT_MODEL_ID
    default_applied = False
    for option in options:
        if option["id"] == default_id:
            option["default"] = True
            default_applied = True
            break
    if not default_applied and options:
        options[0]["default"] = True

    return options


def fetch_antigravity_user_status(*, cwd: Optional[Union[str, Path]] = None, launch: bool = False) -> Optional[dict[str, Any]]:
    """Read live Antigravity user status without making callers know LS details."""

    try:
        try:
            from .ag_runtime import RuntimeLocator, RuntimeRpcClient
        except ImportError:
            from ag_runtime import RuntimeLocator, RuntimeRpcClient

        locator = RuntimeLocator(Path(cwd).expanduser().resolve() if cwd else None)
        if not launch and not locator._process_rows():
            return None
        status = RuntimeRpcClient(locator.discover()).call("GetUserStatus", {})
        return status if isinstance(status, dict) else None
    except Exception:
        return None


def load_dynamic_model_options(*, cwd: Optional[Union[str, Path]] = None, launch: bool = False) -> List[dict[str, Any]]:
    """Return the same model options Antigravity's model picker receives from GetUserStatus."""

    status = fetch_antigravity_user_status(cwd=cwd, launch=launch)
    return extract_model_options_from_user_status(status) if status else []


def load_model_options(
    user_status_payload: Any = None,
    *,
    dynamic: bool = False,
    cwd: Optional[Union[str, Path]] = None,
    launch: bool = False,
) -> List[dict[str, Any]]:
    """Load model options, allowing local overrides as Antigravity changes ids."""

    raw = os.environ.get("ANTIGRAVITY_MODELS_JSON", "").strip()
    if raw:
        return _normalize_model_options(_load_json_source(raw))

    config_path = Path.home() / ".config" / "antigravity-cli" / "models.json"
    if config_path.exists():
        return _normalize_model_options(json.loads(config_path.read_text(encoding="utf-8")))

    if user_status_payload is not None:
        options = extract_model_options_from_user_status(user_status_payload)
        if options:
            return options

    if dynamic:
        options = load_dynamic_model_options(cwd=cwd, launch=launch)
        if options:
            return options

    return [dict(item) for item in MODEL_OPTIONS]


def default_model_id(
    *,
    dynamic: bool = False,
    cwd: Optional[Union[str, Path]] = None,
    launch: bool = False,
) -> int:
    for option in load_model_options(dynamic=dynamic, cwd=cwd, launch=launch):
        if option.get("default"):
            return int(option["id"])
    return DEFAULT_MODEL_ID
