import os
from pathlib import Path
import yaml


SKILL_ROOT = Path(__file__).parent.parent.parent.resolve()


def load_config(config_path: str = "") -> dict:
    if not config_path:
        for candidate in [
            SKILL_ROOT / "config.yaml",
            SKILL_ROOT / "config" / "config.yaml",
            SKILL_ROOT / "config.local.yaml",
        ]:
            if candidate.is_file():
                config_path = str(candidate)
                break
    if not config_path or not Path(config_path).is_file():
        config_path = str(SKILL_ROOT / "config.yaml")
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}
    config = _expand_paths(config)
    config["_skill_root"] = str(SKILL_ROOT)
    config["_config_path"] = config_path
    return config


def _expand_paths(obj):
    if isinstance(obj, dict):
        return {k: _expand_paths(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_expand_paths(v) for v in obj]
    if isinstance(obj, str) and (obj.startswith("~") or obj.startswith("./")):
        if obj.startswith("~"):
            return str(Path(obj).expanduser())
        if obj.startswith("./"):
            return str((SKILL_ROOT / obj[2:]).resolve())
    return obj


def save_config(config: dict, config_path: str = ""):
    if not config_path:
        config_path = config.get("_config_path", str(SKILL_ROOT / "config.yaml"))
    save_dict = {k: v for k, v in config.items() if not k.startswith("_")}
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(save_dict, f, allow_unicode=True, sort_keys=False)
