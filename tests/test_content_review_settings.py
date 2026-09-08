import json
import os

from scripts.lib import app_config


def test_explicit_video_project_reuses_only_legacy_model_key(tmp_path, monkeypatch):
    external_project = tmp_path / "video-service"
    (external_project / "data").mkdir(parents=True)
    external_env = external_project / ".env"
    external_env.write_text("TIKHUB_API_KEY=test-tikhub\nLLM_API_KEY=must-not-import\n")
    (external_project / "data" / "local_settings.json").write_text(json.dumps({
        "doubao_api_key": "test-vision-key", "admin_token": "must-not-import",
    }))
    monkeypatch.setattr(app_config, "DEFAULT_ENV_PATH", tmp_path / "missing.env")
    monkeypatch.setattr(app_config, "load_raw_config", lambda: {
        "content_review": {"external_env_path": str(external_env)},
    })
    monkeypatch.delenv("ARK_API_KEY", raising=False)
    monkeypatch.delenv("TIKHUB_API_KEY", raising=False)
    before = dict(os.environ)
    settings = app_config.load_content_review_settings()
    assert settings["ark_api_key"] == "test-vision-key"
    assert settings["tikhub_api_key"] == "test-tikhub"
    assert "admin_token" not in settings
    assert dict(os.environ) == before

    monkeypatch.setattr(app_config, "load_raw_config", lambda: {
        "content_review": {
            "external_env_path": str(external_env), "ark_api_key": "project-vision-key",
        },
    })
    assert app_config.load_content_review_settings()["ark_api_key"] == "project-vision-key"
