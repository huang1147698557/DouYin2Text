#!/usr/bin/env python3
"""
DouYin2Text - 多媒体内容提取与语音识别工具

支持两种模式:
1. 抖音链接模式: 通过 MCP 服务解析链接，提取视频/文案/封面，再进行 ASR
2. 本地视频模式: 直接对本地视频文件进行 ASR
"""

import os
import re
import sys
import json
import base64
import tempfile
import argparse
import subprocess
import uuid
from pathlib import Path
import requests
import urllib.parse
import urllib.request
from typing import Optional

# ── 服务配置 ──────────────────────────────────────────────────────────────────
BASE_URL  = "http://16tufi081507.vicp.fun:8086"
API_KEY   = "763b0781bd293135b391347ebb83c7a9"
MCP_ENDPOINT = f"{BASE_URL}/mcp?key={API_KEY}"
VOLCENGINE_FLASH_URL = "https://openspeech.bytedance.com/api/v3/auc/bigmodel/recognize/flash"
DEFAULT_CONFIG_PATH = Path(__file__).with_name("config.json")
CONFIG_PATH = Path(
    os.environ.get("DOUYIN2TEXT_CONFIG_PATH", str(DEFAULT_CONFIG_PATH))
).expanduser()
WHISPER_MODEL_CHOICES = ("tiny", "base", "small", "medium", "large")
SUPPORTED_ASR_PROVIDERS = {"auto", "volcengine", "whisper"}

# ── MCP 客户端（优先走 MCP，必要时回退 REST）──────────────────────────────────

class MCPClient:
    """Minimal MCP Streamable-HTTP client (2024-11-05)."""

    def __init__(self, endpoint: str):
        self.endpoint = endpoint
        self.session_id: Optional[str] = None
        self._req_id = 0

    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id

    def _post(self, payload: dict) -> dict:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.session_id:
            headers["mcp-session-id"] = self.session_id

        resp = requests.post(self.endpoint, json=payload, headers=headers, timeout=30)
        sid = resp.headers.get("mcp-session-id")
        if sid:
            self.session_id = sid

        data = resp.json()
        if "error" in data:
            raise RuntimeError(f"MCP error: {data['error']}")
        return data.get("result", {})

    def initialize(self) -> dict:
        return self._post({
            "jsonrpc": "2.0", "id": self._next_id(),
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "douyin2text", "version": "1.0"},
            },
        })

    def list_tools(self) -> list:
        result = self._post({
            "jsonrpc": "2.0", "id": self._next_id(),
            "method": "tools/list", "params": {},
        })
        return result.get("tools", [])

    def call_tool(self, name: str, arguments: dict) -> dict:
        result = self._post({
            "jsonrpc": "2.0", "id": self._next_id(),
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        })
        content_blocks = result.get("content", [])
        text_blocks = []
        for block in content_blocks:
            if block.get("type") == "text":
                text_blocks.append(block.get("text", ""))
        return {
            "is_error": bool(result.get("isError")),
            "texts": text_blocks,
            "raw_result": result,
        }


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def get_volcengine_config() -> dict:
    config = load_config()
    return config.get("volcengine_asr", {})


def normalize_asr_provider(provider: Optional[str], default: str = "auto") -> str:
    value = (provider or "").strip().lower()
    if value in SUPPORTED_ASR_PROVIDERS:
        return value
    return default


def has_volcengine_credentials(cfg: Optional[dict] = None) -> bool:
    cfg = cfg or get_volcengine_config()
    return bool(
        cfg.get("api_key")
        or (cfg.get("app_id") and cfg.get("access_token"))
        or cfg.get("secret_key")
    )


def build_volcengine_headers(task_id: str, include_sequence: bool) -> dict:
    cfg = get_volcengine_config()
    headers = {
        "Content-Type": "application/json",
        "X-Api-Resource-Id": cfg.get("resource_id", "volc.seedasr.auc"),
        "X-Api-Request-Id": task_id,
    }
    if include_sequence:
        headers["X-Api-Sequence"] = "-1"

    api_key = cfg.get("api_key")
    app_id = cfg.get("app_id")
    access_token = cfg.get("access_token")

    if api_key:
        headers["X-Api-Key"] = api_key
    elif app_id and access_token:
        headers["X-Api-App-Key"] = app_id
        headers["X-Api-Access-Key"] = access_token
    elif cfg.get("secret_key"):
        headers["X-Api-Key"] = cfg["secret_key"]
    else:
        raise RuntimeError(
            f"未找到火山 ASR 凭证，请在 {CONFIG_PATH.name} 的 volcengine_asr 中配置 "
            "api_key 或 app_id + access_token。"
        )
    return headers


def guess_audio_format_from_url(url: str) -> str:
    path = urllib.parse.urlparse(url).path.lower()
    if path.endswith(".wav"):
        return "wav"
    if path.endswith(".ogg") or path.endswith(".opus"):
        return "ogg"
    return "mp3"


def file_to_base64(file_path: str) -> str:
    with open(file_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def build_paragraphs_from_utterances(
    utterances: list[dict],
    pause_threshold_ms: int = 1400,
    max_chars_per_paragraph: int = 140,
    max_lines_per_paragraph: int = 4,
) -> list[str]:
    paragraphs: list[str] = []
    current_lines: list[str] = []
    current_chars = 0
    previous_end_time: Optional[int] = None

    for item in utterances:
        line = normalize_text_line(item.get("text", ""))
        if not line:
            continue

        start_time = item.get("start_time")
        pause_ms = (
            start_time - previous_end_time
            if isinstance(start_time, int) and isinstance(previous_end_time, int)
            else 0
        )

        should_break = bool(current_lines) and (
            pause_ms >= pause_threshold_ms
            or current_chars + len(line) > max_chars_per_paragraph
            or len(current_lines) >= max_lines_per_paragraph
            or current_lines[-1].endswith(("。", "！", "？", "!", "?"))
        )

        if should_break:
            paragraphs.append("".join(current_lines))
            current_lines = []
            current_chars = 0

        current_lines.append(line)
        current_chars += len(line)
        previous_end_time = item.get("end_time")

    if current_lines:
        paragraphs.append("".join(current_lines))

    return [normalize_text_line(p) for p in paragraphs if normalize_text_line(p)]


def build_paragraphs_from_lines(
    lines: list[str],
    max_chars_per_paragraph: int = 140,
    max_lines_per_paragraph: int = 4,
) -> list[str]:
    paragraphs: list[str] = []
    current_lines: list[str] = []
    current_chars = 0

    for line in lines:
        clean = normalize_text_line(line)
        if not clean:
            continue

        should_break = bool(current_lines) and (
            current_chars + len(clean) > max_chars_per_paragraph
            or len(current_lines) >= max_lines_per_paragraph
            or current_lines[-1].endswith(("。", "！", "？", "!", "?"))
        )

        if should_break:
            paragraphs.append("".join(current_lines))
            current_lines = []
            current_chars = 0

        current_lines.append(clean)
        current_chars += len(clean)

    if current_lines:
        paragraphs.append("".join(current_lines))

    return [normalize_text_line(p) for p in paragraphs if normalize_text_line(p)]


def extract_utterance_lines(result_payload: dict) -> tuple[str, list[str], str, list[str]]:
    result = result_payload.get("result") or {}
    raw_text = normalize_text_line(result.get("text", ""))
    utterances = result.get("utterances") or []
    lines = [
        normalize_text_line(item.get("text", ""))
        for item in utterances
        if normalize_text_line(item.get("text", ""))
    ]
    if not lines:
        lines = split_text_into_lines(raw_text)
    text = "\n".join(lines) if lines else raw_text
    paragraphs = (
        build_paragraphs_from_utterances(utterances)
        if utterances
        else build_paragraphs_from_lines(lines)
    )
    return text, lines, raw_text, paragraphs


# ── 视频解析 (抖音链接模式) ────────────────────────────────────────────────────

def _parse_text_response(text: str) -> dict:
    """
    解析 /api/mcp/parse 返回的文本格式，提取标题、作者、下载地址。

    格式示例:
        解析成功！
        标题：XXX
        作者：XXX
        下载地址：https://...
    """
    result: dict = {"raw_text": text}
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("标题："):
            result["title"] = line[3:].strip()
        elif line.startswith("作者："):
            result["author"] = line[3:].strip()
        elif line.startswith("下载地址："):
            result["video_url"] = line[5:].strip()
        elif line.startswith("封面："):
            result["cover"] = line[3:].strip()
    return result


def parse_douyin_url_via_mcp(url: str) -> dict:
    """按 MCP 协议握手并调用工具；如果工具返回未授权，则交给上层回退。"""
    client = MCPClient(MCP_ENDPOINT)
    client.initialize()
    tools = client.list_tools()
    tool_names = {tool.get("name", "") for tool in tools}
    if "mcp_parse_video_api_mcp_parse_get" not in tool_names:
        raise RuntimeError("MCP 服务未暴露 mcp_parse_video_api_mcp_parse_get 工具")

    tool_result = client.call_tool("mcp_parse_video_api_mcp_parse_get", {"url": url})
    text = "\n".join(tool_result.get("texts", [])).strip()
    if tool_result.get("is_error"):
        raise RuntimeError(text or "MCP 工具调用失败")

    if not text:
        raise RuntimeError("MCP 工具未返回可解析文本")

    try:
        data = json.loads(text)
        if isinstance(data, dict):
            data.setdefault("mcp_transport", "streamable-http")
            data.setdefault("mcp_tool", "mcp_parse_video_api_mcp_parse_get")
            return data
    except json.JSONDecodeError:
        pass

    data = _parse_text_response(text)
    data["mcp_transport"] = "streamable-http"
    data["mcp_tool"] = "mcp_parse_video_api_mcp_parse_get"
    return data


def parse_douyin_url_via_rest(url: str) -> dict:
    """
    通过 MCP 服务的 REST 接口解析抖音/短视频链接。
    端点: GET /api/mcp/parse?key=<KEY>&url=<URL>
    返回包含 title / author / video_url / cover 等字段的字典。
    """
    api_url = f"{BASE_URL}/api/mcp/parse"
    resp = requests.get(
        api_url,
        params={"key": API_KEY, "url": url},
        timeout=30,
    )
    resp.raise_for_status()

    raw = resp.text.strip()
    # 服务可能返回 JSON 字符串（带引号）或纯文本
    if raw.startswith('"') and raw.endswith('"'):
        raw = json.loads(raw)   # 去掉外层 JSON 字符串编码

    meta = _parse_text_response(raw)
    if not meta.get("video_url"):
        raise RuntimeError(f"无法从响应中提取视频地址。原始响应:\n{raw[:300]}")
    meta["mcp_transport"] = "rest-fallback"
    meta["mcp_tool"] = "api/mcp/parse"
    return meta


def parse_douyin_url(url: str) -> dict:
    """优先按 MCP 协议调用，失败时回退到同服务提供的解析接口。"""
    print(f"[MCP] 正在解析链接: {url}")
    try:
        return parse_douyin_url_via_mcp(url)
    except Exception as exc:
        message = str(exc)
        if "401" not in message and "Unauthorized" not in message and "未登录" not in message:
            raise
        print("[MCP] 工具调用需要登录，回退到 REST 解析接口。")
        return parse_douyin_url_via_rest(url)


def download_file(url: str, dest_path: str, label: str = "文件") -> bool:
    """下载远程文件到本地路径（支持防盗链绕过，增强重试和错误处理）。"""
    print(f"[下载] 正在下载{label}: {url[:80]}...")
    try:
        # 抖音视频链接需要完整的 URL，不能截断
        headers = {
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                          "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148",
            "Referer": "https://www.douyin.com/",
            "Accept": "video/mp4,video/*;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }
        
        # 使用 requests 库下载，更稳定
        resp = requests.get(url, headers=headers, timeout=120, stream=True)
        resp.raise_for_status()
        
        # 检查内容类型
        content_type = resp.headers.get('Content-Type', '')
        if 'video' not in content_type and 'octet-stream' not in content_type:
            print(f"[下载] 警告: 响应类型不是视频: {content_type}")
        
        # 流式写入文件
        with open(dest_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=65536):
                if chunk:
                    f.write(chunk)
        
        size_mb = os.path.getsize(dest_path) / 1024 / 1024
        print(f"[下载] 完成，大小: {size_mb:.1f} MB -> {dest_path}")
        return True
    except requests.exceptions.RequestException as e:
        print(f"[下载] 网络请求失败: {e}")
        return False
    except Exception as e:
        print(f"[下载] 失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def probe_media_metadata(media_path: str) -> dict:
    """使用 ffprobe 提取媒体元数据，便于链接模式输出校验信息。"""
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries",
        "format=duration,size,bit_rate:stream=codec_type,width,height,avg_frame_rate",
        "-of", "json",
        media_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return {}
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {}

    format_info = payload.get("format", {})
    streams = payload.get("streams", [])
    video_stream = next((s for s in streams if s.get("codec_type") == "video"), {})
    return {
        "duration_seconds": float(format_info["duration"]) if format_info.get("duration") else None,
        "size_bytes": int(format_info["size"]) if format_info.get("size") else None,
        "bit_rate": int(format_info["bit_rate"]) if format_info.get("bit_rate") else None,
        "width": video_stream.get("width"),
        "height": video_stream.get("height"),
        "avg_frame_rate": video_stream.get("avg_frame_rate"),
    }


def normalize_text_line(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text


def split_text_into_lines(text: str, max_chars: int = 36) -> list[str]:
    """
    兜底分行：优先按中文句号/问号/感叹号切分；
    没有明显标点时，按长度做近似分行，避免整段挤成一行。
    """
    clean = normalize_text_line(text)
    if not clean:
        return []

    if any(p in clean for p in "。！？!?；;"):
        pieces = re.split(r"(?<=[。！？!?；;])", clean)
    else:
        pieces = [clean[i:i + max_chars] for i in range(0, len(clean), max_chars)]

    return [normalize_text_line(piece) for piece in pieces if normalize_text_line(piece)]


def looks_like_share_input(text: str) -> bool:
    clean = (text or "").strip().lower()
    return clean.startswith(("http://", "https://")) or "douyin" in clean or "v.dy" in clean


def extract_share_url(text: str) -> str:
    clean = normalize_text_line(text)
    if not clean:
        raise RuntimeError("请输入抖音链接或包含链接的分享文案。")

    matches = [
        match.rstrip("，。！？；：,.!?:;)]】}>\"'")
        for match in re.findall(r"https?://[^\s\"'<>]+", clean, flags=re.IGNORECASE)
    ]
    if not matches and clean.startswith(("http://", "https://")):
        matches = [clean.rstrip("，。！？；：,.!?:;)]】}>\"'")]

    if not matches:
        raise RuntimeError("未从输入内容中提取到可用链接，请粘贴抖音分享链接或整段分享文案。")

    preferred = []
    for url in matches:
        hostname = urllib.parse.urlparse(url).netloc.lower()
        if any(domain in hostname for domain in ("douyin.com", "iesdouyin.com", "v.dy")):
            preferred.append(url)

    return (preferred or matches)[0]


# ── ASR (Whisper 语音识别) ────────────────────────────────────────────────────

def extract_audio(video_path: str, audio_path: str) -> bool:
    """用 ffmpeg 从视频中提取音频（16kHz 单声道 WAV）。"""
    cmd = [
        "ffmpeg", "-y", "-i", video_path,
        "-vn", "-ar", "16000", "-ac", "1", "-f", "wav",
        audio_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[ffmpeg] 错误: {result.stderr[-500:]}")
        return False
    return True


def extract_audio_for_volcengine(video_path: str, audio_path: str, audio_format: str = "mp3") -> bool:
    """
    为火山极速版准备较小的音频文件，避免直接上传 wav 体积过大。
    """
    if audio_format == "mp3":
        cmd = [
            "ffmpeg", "-y", "-i", video_path,
            "-vn", "-ar", "16000", "-ac", "1", "-b:a", "64k",
            audio_path,
        ]
    else:
        cmd = [
            "ffmpeg", "-y", "-i", video_path,
            "-vn", "-ar", "16000", "-ac", "1",
            audio_path,
        ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[ffmpeg] 火山音频准备失败: {result.stderr[-500:]}")
        return False
    return True


def run_volcengine_asr_flash(video_path: str) -> dict:
    """
    调用火山引擎录音文件极速识别 API。
    使用 audio.data(base64) 方式提交，因此链接模式、本地文件、stdin 都可统一处理。
    """
    cfg = get_volcengine_config()
    task_id = str(uuid.uuid4())
    audio_format = cfg.get("audio_format", "mp3")

    suffix = ".mp3" if audio_format == "mp3" else ".wav"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        audio_path = tmp.name

    try:
        print("[ASR] 正在准备火山极速版音频...")
        if not extract_audio_for_volcengine(video_path, audio_path, audio_format=audio_format):
            raise RuntimeError("音频提取失败")

        body = {
            "user": {
                "uid": cfg.get("uid", "douyin2text"),
            },
            "audio": {
                "data": file_to_base64(audio_path),
            },
            "request": {
                "model_name": cfg.get("model_name", "bigmodel"),
                "enable_itn": bool(cfg.get("enable_itn", True)),
                "enable_punc": bool(cfg.get("enable_punc", True)),
                "enable_ddc": bool(cfg.get("enable_ddc", True)),
                "show_utterances": bool(cfg.get("show_utterances", True)),
                "enable_speaker_info": bool(cfg.get("enable_speaker_info", False)),
                "enable_channel_split": bool(cfg.get("enable_channel_split", False)),
            },
        }
        if cfg.get("language"):
            body["audio"]["language"] = cfg["language"]
        if cfg.get("ssd_version"):
            body["request"]["ssd_version"] = cfg["ssd_version"]

        print("[ASR] 正在提交火山极速版识别...")
        response = requests.post(
            VOLCENGINE_FLASH_URL,
            headers=build_volcengine_headers(task_id, include_sequence=True),
            json=body,
            timeout=300,
        )
        payload = response.json()
        if response.status_code >= 400:
            raise RuntimeError(
                f"火山极速版提交失败: HTTP {response.status_code} - "
                f"{payload.get('header', {}).get('message', response.text)}"
            )

        api_code = response.headers.get("X-Api-Status-Code")
        api_message = response.headers.get("X-Api-Message", "")
        if api_code not in ("20000000", "20000003", None):
            raise RuntimeError(f"火山极速版识别失败: {api_code} - {api_message or payload}")

        text, lines, raw_text, paragraphs = extract_utterance_lines(payload)
        if api_code == "20000003":
            text, lines, raw_text, paragraphs = "", [], "", []

        return {
            "text": text,
            "lines": lines,
            "raw_text": raw_text,
            "paragraphs": paragraphs,
            "provider": "volcengine_flash",
            "task_id": task_id,
            "response": payload,
        }
    finally:
        if os.path.exists(audio_path):
            os.unlink(audio_path)


def run_whisper_asr(video_path: str, model_name: str = "base") -> dict:
    """
    使用 OpenAI Whisper 对视频/音频文件进行语音识别。
    model_name 可选: tiny / base / small / medium / large
    """
    import whisper

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        audio_path = tmp.name

    try:
        print(f"[ASR] 提取音频中...")
        if not extract_audio(video_path, audio_path):
            raise RuntimeError("音频提取失败")

        print(f"[ASR] 加载 Whisper 模型 ({model_name})...")
        model = whisper.load_model(model_name)

        print("[ASR] 语音识别中，请稍候...")
        result = model.transcribe(audio_path, language="zh", verbose=False)
        text = normalize_text_line(result["text"])
        segments = result.get("segments") or []
        lines = [
            normalize_text_line(segment.get("text", ""))
            for segment in segments
            if normalize_text_line(segment.get("text", ""))
        ]
        if not lines:
            lines = split_text_into_lines(text)

        paragraphs = build_paragraphs_from_lines(lines)
        return {
            "text": "\n".join(lines) if lines else text,
            "lines": lines,
            "raw_text": text,
            "paragraphs": paragraphs,
        }
    finally:
        if os.path.exists(audio_path):
            os.unlink(audio_path)


def run_asr(
    video_path: str,
    model_name: str = "base",
    provider_override: Optional[str] = None,
) -> dict:
    """
    优先尝试火山引擎极速版文件识别 API。
    当没有配置或调用失败时，自动回退到 Whisper。
    """
    cfg = get_volcengine_config()
    provider = normalize_asr_provider(provider_override, normalize_asr_provider(cfg.get("provider"), "auto"))

    should_try_volc = provider in ("auto", "volcengine")
    if should_try_volc and has_volcengine_credentials(cfg):
        try:
            return run_volcengine_asr_flash(video_path)
        except Exception as exc:
            if provider == "volcengine":
                raise
            print(f"[ASR] 火山引擎识别失败，回退到 Whisper: {exc}")

    if provider == "volcengine" and not has_volcengine_credentials(cfg):
        raise RuntimeError(
            f"provider=volcengine 但未在 {CONFIG_PATH.name} 中配置有效凭证。"
        )

    result = run_whisper_asr(video_path, model_name=model_name)
    result["provider"] = "whisper"
    return result


def materialize_stdin_stream(output_dir: str) -> str:
    """将 stdin 视频流落盘，供统一 ASR 处理。"""
    os.makedirs(output_dir, exist_ok=True)
    temp_path = os.path.join(output_dir, "stdin_input.mp4")
    print(f"[输入流] 正在写入临时文件: {temp_path}")
    with open(temp_path, "wb") as f:
        while True:
            chunk = sys.stdin.buffer.read(65536)
            if not chunk:
                break
            f.write(chunk)
    if os.path.getsize(temp_path) == 0:
        raise RuntimeError("stdin 未读取到任何视频数据")
    return temp_path


# ── 主流程 ────────────────────────────────────────────────────────────────────

def process_url(url: str, output_dir: str, whisper_model: str, asr_provider: Optional[str] = None) -> dict:
    """抖音链接模式：解析 -> 下载视频 -> ASR。"""
    os.makedirs(output_dir, exist_ok=True)
    resolved_url = extract_share_url(url)
    requested_provider = normalize_asr_provider(
        asr_provider,
        normalize_asr_provider(get_volcengine_config().get("provider"), "auto"),
    )

    # 1. 解析链接
    meta = parse_douyin_url(resolved_url)
    if not meta:
        raise RuntimeError("链接解析失败，未获得有效数据")

    print("\n[解析结果]")
    title     = meta.get("title", "")
    author    = meta.get("author", "")
    video_url = meta.get("video_url", "")
    cover_url = meta.get("cover", "")
    raw_text  = meta.get("title", "")

    print(f"  标题/文案: {title or raw_text}")
    print(f"  作者: {author}")
    print(f"  视频URL: {video_url[:80] if video_url else '(无)'}")
    print(f"  封面URL: {cover_url[:80] if cover_url else '(无)'}")

    # 2. 下载视频
    video_path = os.path.join(output_dir, "video.mp4")
    if not video_url:
        raise RuntimeError("未获得视频下载地址")
    if not download_file(video_url, video_path, "视频"):
        raise RuntimeError("视频下载失败")

    # 3. 下载封面（可选）
    cover_path = None
    if cover_url:
        cover_path = os.path.join(output_dir, "cover.jpg")
        download_file(cover_url, cover_path, "封面")

    # 4. ASR
    asr_result = run_asr(video_path, model_name=whisper_model, provider_override=asr_provider)
    video_meta = probe_media_metadata(video_path)

    return {
        "mode": "url",
        "url": resolved_url,
        "input_text": url,
        "title": title or raw_text,
        "author": author,
        "raw_desc": raw_text,
        "cover_path": cover_path,
        "video_path": video_path,
        "video_metadata": video_meta,
        "mcp_transport": meta.get("mcp_transport"),
        "mcp_tool": meta.get("mcp_tool"),
        "requested_asr_provider": requested_provider,
        "whisper_model": whisper_model if requested_provider == "whisper" else None,
        "asr_provider": asr_result.get("provider"),
        "asr_raw_text": asr_result["raw_text"],
        "meta": meta,
    }


def process_video(video_path: str, whisper_model: str, asr_provider: Optional[str] = None) -> dict:
    """本地视频模式：直接 ASR。"""
    if not os.path.isfile(video_path):
        raise FileNotFoundError(f"文件不存在: {video_path}")

    print(f"[本地视频] {video_path}")
    requested_provider = normalize_asr_provider(
        asr_provider,
        normalize_asr_provider(get_volcengine_config().get("provider"), "auto"),
    )
    asr_result = run_asr(video_path, model_name=whisper_model, provider_override=asr_provider)
    video_meta = probe_media_metadata(video_path)

    return {
        "mode": "file",
        "video_path": video_path,
        "video_metadata": video_meta,
        "requested_asr_provider": requested_provider,
        "whisper_model": whisper_model if requested_provider == "whisper" else None,
        "asr_provider": asr_result.get("provider"),
        "asr_raw_text": asr_result["raw_text"],
    }


def print_result(result: dict):
    print("\n" + "=" * 60)
    print("【最终结果】")
    print("=" * 60)
    if result["mode"] == "url":
        print(f"▶ 原始文案:  {result.get('raw_desc', '(无)')}")
        print(f"▶ 封面路径:  {result.get('cover_path', '(未下载)')}")
        print(f"▶ 视频路径:  {result.get('video_path')}")
        print(f"▶ MCP 通道:   {result.get('mcp_transport', '(未知)')}")
    print(f"▶ ASR 引擎:   {result.get('asr_provider', '(未知)')}")
    if result.get("video_metadata"):
        print(f"▶ 视频元数据: {json.dumps(result['video_metadata'], ensure_ascii=False)}")
    raw_text = result.get("asr_raw_text", "")
    asr_paragraphs = build_paragraphs_from_lines(split_text_into_lines(raw_text))
    asr_output = "\n\n".join(asr_paragraphs) if asr_paragraphs else (raw_text or "(未识别到内容)")
    print(f"\n▶ ASR 识别文字:\n{asr_output}")
    print("=" * 60)


# ── CLI 入口 ──────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="DouYin2Text: 抖音链接 / 本地视频 → 语音识别文字"
    )
    parser.add_argument("input", help="抖音分享链接 或 本地视频文件路径")
    parser.add_argument(
        "-o", "--output-dir",
        default="./output",
        help="输出目录（链接模式下保存视频和封面），默认 ./output",
    )
    parser.add_argument(
        "-m", "--model",
        default="base",
        choices=list(WHISPER_MODEL_CHOICES),
        help="Whisper 模型大小（越大越准但越慢），默认 base",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="以 JSON 格式输出完整结果"
    )
    parser.add_argument(
        "--stdin",
        action="store_true",
        help="从 stdin 读取视频数据流；此时 input 可传 -",
    )
    args = parser.parse_args()

    # 判断模式
    inp = args.input.strip()
    is_url = looks_like_share_input(inp) or bool(re.search(r"https?://", inp, flags=re.IGNORECASE))

    try:
        if args.stdin or inp == "-":
            stream_path = materialize_stdin_stream(args.output_dir)
            result = process_video(stream_path, args.model)
        elif is_url:
            result = process_url(inp, args.output_dir, args.model)
        else:
            result = process_video(inp, args.model)

        if args.json:
            # 排除 meta 中的大字段以便可读
            out = {k: v for k, v in result.items() if k != "meta"}
            print(json.dumps(out, ensure_ascii=False, indent=2))
        else:
            print_result(result)

    except Exception as e:
        print(f"\n[错误] {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
