#!/usr/bin/env python3
import json
import os
import re
import shutil
import uuid
from datetime import datetime
from pathlib import Path

import requests
from flask import Flask, jsonify, render_template, request, send_from_directory, url_for
from werkzeug.utils import secure_filename

from douyin2text import (
    CONFIG_PATH as DOUYIN_CONFIG_PATH,
    build_paragraphs_from_lines,
    process_url,
    process_video,
    split_text_into_lines,
)


BASE_DIR = Path(__file__).resolve().parent
WEB_OUTPUT_DIR = Path(
    os.environ.get("DOUYIN2TEXT_WEB_OUTPUT_DIR", str(BASE_DIR / "output" / "web"))
).expanduser()
CONFIG_PATH = DOUYIN_CONFIG_PATH
SUPPORTED_ASR_PROVIDERS = {"auto", "volcengine", "whisper"}
ARK_API_BASE = "https://ark.cn-beijing.volces.com/api/v3"
LITEART_MODEL_CATALOG = {
    "deepseek-v4-pro-260425": {
        "label": "模型一 · DeepSeek V4 Pro",
        "endpoint": "/chat/completions",
        "request_type": "chat_completions",
    },
    "doubao-seed-2-0-pro-260215": {
        "label": "模型二 · Doubao Seed 2.0 Pro",
        "endpoint": "/responses",
        "request_type": "responses",
    },
}
DEFAULT_LITEART_MODEL = "deepseek-v4-pro-260425"
LITEART_ALLOWED_CATEGORIES = (
    "趋势研判",
    "行业洞察",
    "核心观点",
    "实战指南",
    "风险提示",
)
LITEART_ALLOWED_BADGES = (
    "核心观点",
    "深度解析",
    "关键数据",
    "行动建议",
    "风险提示",
)
LITEART_SYSTEM_PROMPT = """你是 LiteArt 卡片文案生成助手。
你的任务是把用户提供的主题、文章、摘要或素材，整理成适合卡片化展示的中文文案。
输出必须克制、清晰、可直接用于视觉卡片，不要写解释，不要写多余前言。"""
WEB_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["MAX_CONTENT_LENGTH"] = 512 * 1024 * 1024


def create_job_dir() -> tuple[str, Path]:
    job_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
    job_dir = WEB_OUTPUT_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    return job_id, job_dir


def build_file_url(path: str | None) -> str | None:
    if not path:
        return None
    file_path = Path(path).resolve()
    try:
        relative = file_path.relative_to(WEB_OUTPUT_DIR)
    except ValueError:
        return None
    return url_for("serve_output_file", path=str(relative).replace("\\", "/"))


def build_web_result(job_id: str, result: dict) -> dict:
    raw_text = result.get("asr_raw_text", "")
    paragraphs = build_paragraphs_from_lines(split_text_into_lines(raw_text))
    payload = {
        "job_id": job_id,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "mode": result.get("mode"),
        "title": result.get("title") or Path(result.get("video_path", "")).name,
        "author": result.get("author"),
        "raw_desc": result.get("raw_desc"),
        "asr_provider": result.get("asr_provider"),
        "requested_asr_provider": result.get("requested_asr_provider"),
        "whisper_model": result.get("whisper_model"),
        "asr_raw_text": raw_text,
        "asr_paragraphs": paragraphs,
        "video_metadata": result.get("video_metadata") or {},
        "mcp_transport": result.get("mcp_transport"),
        "mcp_tool": result.get("mcp_tool"),
        "video_path": result.get("video_path"),
        "cover_path": result.get("cover_path"),
        "video_url": build_file_url(result.get("video_path")),
        "cover_url": build_file_url(result.get("cover_path")),
    }
    return payload


def normalize_provider(value: str | None, default: str = "auto") -> str:
    provider = (value or "").strip().lower()
    if provider in SUPPORTED_ASR_PROVIDERS:
        return provider
    return default


def load_app_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}

    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        config = json.load(f)

    if not isinstance(config, dict):
        raise RuntimeError("config.json 格式无效，顶层必须为 JSON 对象。")
    return config


def save_app_config(config: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CONFIG_PATH.open("w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)


def get_volcengine_settings(config: dict | None = None) -> dict:
    config = config if config is not None else load_app_config()
    raw_volcengine = config.get("volcengine_asr")
    if raw_volcengine is not None and not isinstance(raw_volcengine, dict):
        raise RuntimeError("config.json 中 volcengine_asr 必须是对象。")

    volcengine = raw_volcengine or {}
    return {
        "provider": normalize_provider(volcengine.get("provider") or config.get("provider"), "auto"),
        "app_id": volcengine.get("app_id") or config.get("app_id", ""),
        "access_token": volcengine.get("access_token") or config.get("access_token", ""),
        "api_key": volcengine.get("api_key") or config.get("api_key", ""),
        "uid": volcengine.get("uid") or config.get("uid", ""),
    }


def get_liteart_llm_settings(config: dict | None = None) -> dict:
    config = config if config is not None else load_app_config()
    raw_liteart = config.get("liteart_llm")
    if raw_liteart is not None and not isinstance(raw_liteart, dict):
        raise RuntimeError("config.json 中 liteart_llm 必须是对象。")

    liteart = raw_liteart or {}
    active_model = (liteart.get("active_model") or DEFAULT_LITEART_MODEL).strip()
    if active_model not in LITEART_MODEL_CATALOG:
        active_model = DEFAULT_LITEART_MODEL

    api_key = (liteart.get("api_key") or os.environ.get("ARK_API_KEY") or "").strip()
    return {
        "active_model": active_model,
        "api_key": api_key,
        "models": [
            {
                "id": model_id,
                "label": meta["label"],
                "request_type": meta["request_type"],
            }
            for model_id, meta in LITEART_MODEL_CATALOG.items()
        ],
    }


def build_liteart_prompt(source_text: str) -> str:
    categories = "、".join(LITEART_ALLOWED_CATEGORIES)
    badges = "、".join(LITEART_ALLOWED_BADGES)
    return f"""请基于下面素材，整理出适合 LiteArt 页面展示的 6 页中文卡片文案。

输出要求：
1. 只输出最终文案，不要解释，不要前言，不要 Markdown 代码块。
2. 必须输出 1 个封面和 5 个内容页，总共 6 段。
3. [封面] 只能包含：标题、标签、副标题、正文。
4. [第1页] 到 [第5页] 都必须包含：标题、分类、徽标、副标题、正文。
5. 分类只能从以下选项中选择：{categories}。
6. 徽标只能从以下选项中选择：{badges}。
7. 标题尽量控制在 8 到 18 个字，副标题尽量控制在 10 到 22 个字，正文尽量控制在 45 到 85 个字。
8. 可以做提炼和重写，但不要编造数字、年份、人物观点或不存在的事实。
9. 输出结构必须严格遵守以下格式：

[封面]
标题：
标签：
副标题：
正文：

[第1页]
标题：
分类：
徽标：
副标题：
正文：

[第2页]
标题：
分类：
徽标：
副标题：
正文：

[第3页]
标题：
分类：
徽标：
副标题：
正文：

[第4页]
标题：
分类：
徽标：
副标题：
正文：

[第5页]
标题：
分类：
徽标：
副标题：
正文：

素材如下：
{source_text.strip()}
"""


def flatten_llm_text(value) -> str:
    if isinstance(value, str):
        return value.strip()

    if isinstance(value, list):
        parts = [flatten_llm_text(item) for item in value]
        return "\n".join(part for part in parts if part).strip()

    if isinstance(value, dict):
        for key in ("text", "output_text", "content", "value"):
            if key in value:
                text = flatten_llm_text(value[key])
                if text:
                    return text.strip()

    return ""


def extract_liteart_response_text(payload: dict) -> str:
    if not isinstance(payload, dict):
        raise RuntimeError("模型返回格式无效。")

    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message") or {}
        text = flatten_llm_text(message.get("content"))
        if text:
            return text.strip()

    for candidate in (payload.get("output_text"), payload.get("output")):
        text = flatten_llm_text(candidate)
        if text:
            return text.strip()

    raise RuntimeError("未从模型响应中提取到文案。")


def clean_liteart_generated_text(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[A-Za-z0-9_-]*\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)

    cover_index = cleaned.find("[封面]")
    if cover_index > 0:
        cleaned = cleaned[cover_index:]

    return cleaned.strip()


def extract_request_error_message(response: requests.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text.strip() or f"HTTP {response.status_code}"

    error = payload.get("error")
    if isinstance(error, dict):
        return error.get("message") or json.dumps(error, ensure_ascii=False)
    if error:
        return str(error)
    if payload.get("message"):
        return str(payload["message"])
    return json.dumps(payload, ensure_ascii=False)


def generate_liteart_copy(source_text: str, settings: dict) -> str:
    api_key = (settings.get("api_key") or "").strip()
    if not api_key:
        raise RuntimeError("请先在 LiteArt 页面右上角配置 API Key。")

    model_id = settings.get("active_model") or DEFAULT_LITEART_MODEL
    if model_id not in LITEART_MODEL_CATALOG:
        raise RuntimeError("所选模型不受支持。")

    model_meta = LITEART_MODEL_CATALOG[model_id]
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    prompt = build_liteart_prompt(source_text)

    if model_meta["request_type"] == "chat_completions":
        payload = {
            "model": model_id,
            "messages": [
                {"role": "system", "content": LITEART_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.7,
        }
    else:
        payload = {
            "model": model_id,
            "input": [
                {
                    "role": "system",
                    "content": [{"type": "input_text", "text": LITEART_SYSTEM_PROMPT}],
                },
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": prompt}],
                },
            ],
        }

    try:
        response = requests.post(
            f"{ARK_API_BASE}{model_meta['endpoint']}",
            headers=headers,
            json=payload,
            timeout=90,
        )
    except requests.RequestException as exc:
        raise RuntimeError(f"模型请求失败：{exc}") from exc

    if response.status_code >= 400:
        raise RuntimeError(f"模型请求失败：{extract_request_error_message(response)}")

    try:
        result = response.json()
    except ValueError as exc:
        raise RuntimeError("模型返回了无法解析的 JSON。") from exc

    return clean_liteart_generated_text(extract_liteart_response_text(result))


def persist_result(job_dir: Path, payload: dict) -> None:
    result_path = job_dir / "result.json"
    with result_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def load_history(limit: int = 12) -> list[dict]:
    items = []
    for result_file in sorted(WEB_OUTPUT_DIR.glob("*/result.json"), reverse=True):
        try:
            with result_file.open("r", encoding="utf-8") as f:
                data = json.load(f)
            items.append(data)
        except Exception:
            continue
        if len(items) >= limit:
            break
    return items


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/liteart")
def liteart():
    return send_from_directory(str(BASE_DIR / "LiteArt"), "LiteArt_Card.html")


@app.get("/api/history")
def history():
    return jsonify({"items": load_history()})


@app.post("/api/extract")
def extract():
    mode = request.form.get("mode", "url").strip()
    whisper_model = request.form.get("model", "base").strip() or "base"
    requested_asr_provider = normalize_provider(request.form.get("asr_provider"), "auto")
    job_id, job_dir = create_job_dir()

    try:
        if mode == "file":
            upload = request.files.get("file")
            if not upload or not upload.filename:
                return jsonify({"error": "请上传视频文件。"}), 400

            suffix = Path(secure_filename(upload.filename)).suffix or ".mp4"
            input_path = job_dir / f"input{suffix}"
            upload.save(input_path)
            result = process_video(
                str(input_path),
                whisper_model,
                requested_asr_provider,
            )
            if not result.get("video_path"):
                result["video_path"] = str(input_path)
        else:
            raw_input = request.form.get("url", "").strip()
            if not raw_input:
                return jsonify({"error": "请输入抖音链接或分享文案。"}), 400
            result = process_url(
                raw_input,
                str(job_dir),
                whisper_model,
                requested_asr_provider,
            )

        payload = build_web_result(job_id, result)
        persist_result(job_dir, payload)
        return jsonify(payload)
    except Exception as exc:
        shutil.rmtree(job_dir, ignore_errors=True)
        return jsonify({"error": str(exc)}), 500


@app.get("/files/<path:path>")
def serve_output_file(path: str):
    return send_from_directory(WEB_OUTPUT_DIR, path)


@app.get("/api/settings")
def get_settings():
    """Get current configuration settings."""
    if not CONFIG_PATH.exists():
        return jsonify({
            "provider": "auto",
            "app_id": "",
            "access_token": "",
            "api_key": "",
            "uid": "",
        })

    try:
        return jsonify(get_volcengine_settings())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.post("/api/settings")
def save_settings():
    """Save configuration settings."""
    try:
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({"error": "Invalid request"}), 400

        config = load_app_config()
        existing = get_volcengine_settings(config)
        provider = normalize_provider(data.get("provider"), existing["provider"])
        volcengine = config.get("volcengine_asr") or {}
        if not isinstance(volcengine, dict):
            raise RuntimeError("config.json 中 volcengine_asr 必须是对象。")

        volcengine.update({
            "provider": provider,
            "app_id": data.get("app_id", "").strip(),
            "access_token": data.get("access_token", "").strip(),
            "api_key": data.get("api_key", "").strip(),
            "uid": data.get("uid", "").strip() or "douyin2text",
        })
        config["volcengine_asr"] = volcengine

        # 兼容旧版顶层字段，同时保证 CLI 读取的嵌套配置保持一致。
        config["provider"] = volcengine["provider"]
        config["app_id"] = volcengine["app_id"]
        config["access_token"] = volcengine["access_token"]
        config["api_key"] = volcengine["api_key"]
        config["uid"] = volcengine["uid"]

        save_app_config(config)

        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.get("/api/liteart/settings")
def get_liteart_settings():
    try:
        return jsonify(get_liteart_llm_settings())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.post("/api/liteart/settings")
def save_liteart_settings():
    try:
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({"error": "Invalid request"}), 400

        config = load_app_config()
        current = get_liteart_llm_settings(config)
        active_model = (data.get("active_model") or current["active_model"]).strip()
        if active_model not in LITEART_MODEL_CATALOG:
            return jsonify({"error": "不支持的模型。"}), 400

        raw_liteart = config.get("liteart_llm")
        if raw_liteart is not None and not isinstance(raw_liteart, dict):
            raise RuntimeError("config.json 中 liteart_llm 必须是对象。")

        liteart = raw_liteart or {}
        liteart.update({
            "active_model": active_model,
            "api_key": str(data.get("api_key", "")).strip(),
        })
        config["liteart_llm"] = liteart
        save_app_config(config)

        return jsonify({
            "success": True,
            "settings": get_liteart_llm_settings(config),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.post("/api/liteart/generate-copy")
def api_generate_liteart_copy():
    try:
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({"error": "Invalid request"}), 400

        source_text = str(data.get("source_text", "")).strip()
        if not source_text:
            return jsonify({"error": "请输入主题、文章或段落内容。"}), 400

        settings = get_liteart_llm_settings()
        override_model = str(data.get("active_model", "")).strip()
        override_api_key = str(data.get("api_key", "")).strip()

        if override_model:
            if override_model not in LITEART_MODEL_CATALOG:
                return jsonify({"error": "不支持的模型。"}), 400
            settings["active_model"] = override_model
        if override_api_key:
            settings["api_key"] = override_api_key

        generated_text = generate_liteart_copy(source_text, settings)
        return jsonify({
            "generated_text": generated_text,
            "active_model": settings["active_model"],
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    host = os.environ.get("APP_HOST", "127.0.0.1")
    port = int(os.environ.get("APP_PORT", "5088"))
    debug = os.environ.get("APP_DEBUG", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    app.run(host=host, port=port, debug=debug)
