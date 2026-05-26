#!/usr/bin/env python3
import json
import os
import shutil
import uuid
from datetime import datetime
from pathlib import Path

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

        with CONFIG_PATH.open("w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)

        return jsonify({"success": True})
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
