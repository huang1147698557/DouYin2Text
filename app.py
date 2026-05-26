#!/usr/bin/env python3
import json
import shutil
import uuid
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_from_directory, url_for
from werkzeug.utils import secure_filename

from douyin2text import (
    build_paragraphs_from_lines,
    process_url,
    process_video,
    split_text_into_lines,
)


BASE_DIR = Path(__file__).resolve().parent
WEB_OUTPUT_DIR = BASE_DIR / "output" / "web"
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
    job_id, job_dir = create_job_dir()

    try:
        if mode == "file":
            upload = request.files.get("file")
            if not upload or not upload.filename:
                return jsonify({"error": "请上传视频文件。"}), 400

            suffix = Path(secure_filename(upload.filename)).suffix or ".mp4"
            input_path = job_dir / f"input{suffix}"
            upload.save(input_path)
            result = process_video(str(input_path), whisper_model)
            if not result.get("video_path"):
                result["video_path"] = str(input_path)
        else:
            raw_input = request.form.get("url", "").strip()
            if not raw_input:
                return jsonify({"error": "请输入抖音链接或分享文案。"}), 400
            result = process_url(raw_input, str(job_dir), whisper_model)

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
    config_path = BASE_DIR / "config.json"
    if not config_path.exists():
        return jsonify({
            "app_id": "",
            "access_token": "",
            "api_key": "",
            "uid": "",
        })
    
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        
        return jsonify({
            "app_id": config.get("app_id", ""),
            "access_token": config.get("access_token", ""),
            "api_key": config.get("api_key", ""),
            "uid": config.get("uid", ""),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.post("/api/settings")
def save_settings():
    """Save configuration settings."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Invalid request"}), 400
        
        config_path = BASE_DIR / "config.json"
        config = {}
        
        # Load existing config if exists
        if config_path.exists():
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    config = json.load(f)
            except:
                pass
        
        # Update with new values
        config["app_id"] = data.get("app_id", "").strip()
        config["access_token"] = data.get("access_token", "").strip()
        config["api_key"] = data.get("api_key", "").strip()
        config["uid"] = data.get("uid", "").strip() or "douyin2text"
        
        # Ensure provider is set
        if "provider" not in config:
            config["provider"] = "auto"
        
        # Save config
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5088, debug=False)
