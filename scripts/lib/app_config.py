#!/usr/bin/env python3
"""项目本地配置（config.toml）加载。

优先级（高 → 低）：
  1. 函数/CLI 显式参数
  2. 环境变量（FEISHU_*）
  3. 仓库根目录 config.toml（或 --config 指定路径）
  4. 代码默认值

含密钥的 config.toml **不得**提交 git；见 config.toml.example。
仓库根目录 `.env` 只提供 LLM 等环境变量，同样不得提交；见 `.env.example`。
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

# scripts/lib/app_config.py → 仓库根
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATHS = (
    PROJECT_ROOT / "config.toml",
    PROJECT_ROOT / "config" / "config.toml",
)
DEFAULT_ENV_PATH = PROJECT_ROOT / ".env"
DEFAULT_LLM_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_LLM_MODEL_ID = "deepseek-v4-flash"


class AppConfigError(RuntimeError):
    """配置文件无法解析。"""


def _load_toml_bytes(raw: bytes) -> dict[str, Any]:
    try:
        import tomllib  # Python 3.11+
    except ModuleNotFoundError:
        try:
            import tomli as tomllib  # type: ignore
        except ModuleNotFoundError as error:
            raise AppConfigError(
                "无法解析 TOML：需要 Python 3.11+（tomllib）或安装 tomli：pip install tomli"
            ) from error
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except Exception as error:
        raise AppConfigError(f"config.toml 解析失败: {error}") from error
    if not isinstance(data, dict):
        raise AppConfigError("config.toml 根节点必须是表")
    return data


def resolve_config_path(explicit: str | Path | None = None) -> Path | None:
    """返回第一个存在的配置文件路径；explicit 优先。"""
    if explicit is not None:
        path = Path(explicit).expanduser()
        if not path.is_file():
            raise AppConfigError(f"配置文件不存在: {path}")
        return path
    env_path = (os.environ.get("ZN_SAMPLE_CONFIG") or "").strip()
    if env_path:
        path = Path(env_path).expanduser()
        if not path.is_file():
            raise AppConfigError(f"ZN_SAMPLE_CONFIG 指向的文件不存在: {path}")
        return path
    for candidate in DEFAULT_CONFIG_PATHS:
        if candidate.is_file():
            return candidate
    return None


@lru_cache(maxsize=4)
def _cached_toml(path_str: str) -> dict[str, Any]:
    path = Path(path_str)
    return _load_toml_bytes(path.read_bytes())


def load_raw_config(config_path: str | Path | None = None) -> dict[str, Any]:
    """读取完整 TOML；无文件时返回 {}。"""
    path = resolve_config_path(config_path)
    if path is None:
        return {}
    return dict(_cached_toml(str(path.resolve())))


def _section(data: dict[str, Any], name: str) -> dict[str, Any]:
    section = data.get(name)
    if section is None:
        return {}
    if not isinstance(section, dict):
        raise AppConfigError(f"config.toml 中 [{name}] 必须是表")
    return section


def load_dotenv(env_path: str | Path | None = None) -> Path | None:
    """把仓库根目录 `.env` 载入环境变量；已存在的环境变量不覆盖。"""
    path = Path(env_path).expanduser() if env_path is not None else DEFAULT_ENV_PATH
    if not path.is_file():
        return None
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value
    return path


def load_llm_settings(*, env_path: str | Path | None = None) -> dict[str, str]:
    """读取简介语言识别用的 OpenAI 兼容接口配置。"""
    load_dotenv(env_path)
    return {
        "base_url": _pick_str(
            os.environ.get("LLM_BASE_URL"),
            default=DEFAULT_LLM_BASE_URL,
        )
        or DEFAULT_LLM_BASE_URL,
        "api_key": _pick_str(os.environ.get("LLM_API_KEY"), default="") or "",
        "model_id": _pick_str(
            os.environ.get("LLM_MODEL_ID"),
            default=DEFAULT_LLM_MODEL_ID,
        )
        or DEFAULT_LLM_MODEL_ID,
    }


def _pick_str(*candidates: Any, default: str | None = None) -> str | None:
    for value in candidates:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return default


def load_feishu_settings(
    *,
    config_path: str | Path | None = None,
    app_id: str | None = None,
    app_secret: str | None = None,
    hero_url: str | None = None,
    sheet_title: str | None = None,
    default_app_id: str | None = None,
    default_hero_url: str | None = None,
    default_sheet_title: str | None = None,
) -> dict[str, str | None]:
    """合并飞书相关配置。

    返回:
      app_id, app_secret, hero_url, sheet_title, config_path
    """
    raw = load_raw_config(config_path)
    feishu = _section(raw, "feishu")
    resolved_path = resolve_config_path(config_path)

    resolved_app_id = _pick_str(
        app_id,
        os.environ.get("FEISHU_APP_ID"),
        feishu.get("app_id"),
        default=default_app_id,
    )
    resolved_secret = _pick_str(
        app_secret,
        os.environ.get("FEISHU_APP_SECRET"),
        feishu.get("app_secret"),
    )
    resolved_url = _pick_str(
        hero_url,
        os.environ.get("FEISHU_HERO_URL"),
        feishu.get("hero_url"),
        feishu.get("url"),
        default=default_hero_url,
    )
    resolved_sheet = _pick_str(
        sheet_title,
        os.environ.get("FEISHU_HERO_SHEET"),
        feishu.get("sheet"),
        feishu.get("sheet_title"),
        default=default_sheet_title,
    )

    return {
        "app_id": resolved_app_id,
        "app_secret": resolved_secret,
        "hero_url": resolved_url,
        "sheet_title": resolved_sheet,
        "config_path": str(resolved_path) if resolved_path else None,
    }


def load_bitable_settings(
    *,
    config_path: str | Path | None = None,
    app_id: str | None = None,
    app_secret: str | None = None,
    app_token: str | None = None,
    table_id: str | None = None,
    view_id: str | None = None,
    default_app_id: str | None = None,
    default_app_token: str | None = None,
    default_table_id: str | None = None,
    default_view_id: str | None = None,
) -> dict[str, str | None]:
    """合并飞书多维表格（达人关系管理(新)）配置。

    优先级：CLI 参数 > 环境变量 FEISHU_BITABLE_* > [feishu.bitable] > [feishu] 回落 app 凭证 > 默认 ID。
    """
    raw = load_raw_config(config_path)
    feishu = _section(raw, "feishu")
    # TOML nested: [feishu.bitable] → raw["feishu"]["bitable"]
    nested = feishu.get("bitable")
    bitable = nested if isinstance(nested, dict) else {}
    resolved_path = resolve_config_path(config_path)

    resolved_app_id = _pick_str(
        app_id,
        os.environ.get("FEISHU_BITABLE_APP_ID"),
        bitable.get("app_id"),
        # 不默认回落到主推 app_id，避免写权限落到错误应用
        default=default_app_id,
    )
    resolved_secret = _pick_str(
        app_secret,
        os.environ.get("FEISHU_BITABLE_APP_SECRET"),
        bitable.get("app_secret"),
    )
    resolved_app_token = _pick_str(
        app_token,
        os.environ.get("FEISHU_BITABLE_APP_TOKEN"),
        bitable.get("app_token"),
        bitable.get("base_app_token"),
        default=default_app_token,
    )
    resolved_table_id = _pick_str(
        table_id,
        os.environ.get("FEISHU_BITABLE_TABLE_ID"),
        bitable.get("table_id"),
        bitable.get("base_table_id"),
        default=default_table_id,
    )
    resolved_view_id = _pick_str(
        view_id,
        os.environ.get("FEISHU_BITABLE_VIEW_ID"),
        bitable.get("view_id"),
        bitable.get("base_view_id"),
        default=default_view_id,
    )

    return {
        "app_id": resolved_app_id,
        "app_secret": resolved_secret,
        "app_token": resolved_app_token,
        "table_id": resolved_table_id,
        "view_id": resolved_view_id,
        "config_path": str(resolved_path) if resolved_path else None,
    }


def clear_config_cache() -> None:
    """测试用：清空 TOML 缓存。"""
    _cached_toml.cache_clear()
