from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from lib.app_config import load_dotenv, load_llm_settings
from lib.detect_lang import (
    LANGUAGE_DETECT_SYSTEM_PROMPT,
    clear_language_detect_cache,
    detect_creator_lang,
)


class DetectLangTests(unittest.TestCase):
    def setUp(self) -> None:
        clear_language_detect_cache()

    def tearDown(self) -> None:
        clear_language_detect_cache()

    def test_empty_bio_defaults_to_english_without_calling_llm(self) -> None:
        with patch("lib.detect_lang._request_language_from_llm") as request_language:
            result = detect_creator_lang("")
        request_language.assert_not_called()
        self.assertEqual(result["lang"], "en")
        self.assertEqual(result["confidence"], "low")
        self.assertEqual(result["reason"], "empty-bio-default-en")
        self.assertEqual(result["source"], "empty-bio")

    def test_llm_json_classifies_spanish_bio(self) -> None:
        payload = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "lang": "es",
                                "confidence": "high",
                                "reason": "spanish-greeting-and-collab",
                            }
                        )
                    }
                }
            ]
        }
        with (
            patch(
                "lib.detect_lang.load_llm_settings",
                return_value={
                    "base_url": "https://api.deepseek.com/v1",
                    "api_key": "test-key",
                    "model_id": "deepseek-v4-flash",
                },
            ),
            patch("lib.detect_lang.httpx.post") as post,
        ):
            post.return_value.json.return_value = payload
            post.return_value.raise_for_status.return_value = None
            result = detect_creator_lang(
                "Hola, gracias por tu contenido y colaboración."
            )
        self.assertEqual(result["lang"], "es")
        self.assertEqual(result["feishu_lang"], "西班牙语")
        self.assertEqual(result["confidence"], "high")
        self.assertEqual(result["source"], "llm")
        request = post.call_args
        self.assertEqual(
            request.args[0],
            "https://api.deepseek.com/v1/chat/completions",
        )
        self.assertEqual(
            request.kwargs["json"]["response_format"],
            {"type": "json_object"},
        )
        self.assertEqual(
            request.kwargs["json"]["thinking"],
            {"type": "disabled"},
        )
        self.assertEqual(
            request.kwargs["json"]["messages"][0]["content"],
            LANGUAGE_DETECT_SYSTEM_PROMPT,
        )
        self.assertIn("json", LANGUAGE_DETECT_SYSTEM_PROMPT.lower())

    def test_missing_api_key_stays_low_confidence_english(self) -> None:
        with patch(
            "lib.detect_lang.load_llm_settings",
            return_value={
                "base_url": "https://api.deepseek.com/v1",
                "api_key": "",
                "model_id": "deepseek-v4-flash",
            },
        ):
            result = detect_creator_lang("Thanks for the collab")
        self.assertEqual(result["lang"], "en")
        self.assertEqual(result["confidence"], "low")
        self.assertEqual(result["reason"], "llm-detect-failed")
        self.assertEqual(result["source"], "llm-failed")

    def test_dotenv_loads_llm_settings_without_overwriting_existing_env(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            env_path = Path(temporary_directory) / ".env"
            env_path.write_text(
                "LLM_BASE_URL=https://api.deepseek.com/v1\n"
                "LLM_API_KEY=from-file\n"
                "LLM_MODEL_ID=deepseek-v4-flash\n",
                encoding="utf-8",
            )
            with patch.dict("os.environ", {"LLM_API_KEY": "from-process"}):
                load_dotenv(env_path)
                settings = load_llm_settings(env_path=env_path)
            self.assertEqual(settings["api_key"], "from-process")
            self.assertEqual(settings["base_url"], "https://api.deepseek.com/v1")
            self.assertEqual(settings["model_id"], "deepseek-v4-flash")


if __name__ == "__main__":
    unittest.main()
