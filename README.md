# DouYin2Text

> 多媒体内容提取 + 语音识别工具  
> 支持 **抖音链接解析** 和 **本地视频 ASR** 两种模式

## 功能

| 模式 | 输入 | 处理流程 | 输出 |
|------|------|----------|------|
| 抖音链接 | 分享链接（如 `https://v.douyin.com/xxx`） | **MCP 协议调用** → 必要时 REST 回退 → 下载视频/封面 → ASR | 原始文案 + **逐句分行 / 段落整理** ASR + 封面/视频文件 + 视频元数据 |
| 本地视频 | `.mp4` / `.mov` 等视频文件路径 | 直接 ASR | **逐句分行 / 段落整理** ASR + 视频元数据 |
| 视频流 | `stdin` 二进制流 | 落盘临时文件 → ASR | **逐句分行 / 段落整理** ASR + 视频元数据 |

## 依赖

- Python 3.10+
- `ffmpeg`（系统级，需已安装）
- Python 包：`openai-whisper`, `requests`

```bash
pip install openai-whisper requests
```

## 快速使用

```bash
# 先在 config.json 填入火山引擎 ASR 凭证
# 若 provider=auto，则链接模式优先用火山引擎，失败时回退 Whisper

# 抖音链接模式
python3 douyin2text.py "https://v.douyin.com/ixxxxxx/"

# 指定输出目录和更大的模型（识别更准）
python3 douyin2text.py "https://v.douyin.com/ixxxxxx/" -o ./my_output -m small

# 本地视频模式
python3 douyin2text.py /path/to/video.mp4

# 视频流模式
cat /path/to/video.mp4 | python3 douyin2text.py - --stdin

# 输出为 JSON（含完整元数据；仅保留 `asr_raw_text` 作为识别文本）
python3 douyin2text.py "https://v.douyin.com/ixxxxxx/" --json
```

## 火山引擎 ASR 配置

编辑项目根目录下的 `config.json`：

```json
{
  "volcengine_asr": {
    "provider": "auto",
    "app_id": "",
    "access_token": "",
    "secret_key": "",
    "api_key": "",
    "resource_id": "volc.bigasr.auc_turbo",
    "audio_format": "mp3"
  }
}
```

说明：

- 旧版控制台可填 `app_id + access_token`
- 新版控制台可直接填 `api_key`
- 如果你手上只有 `Secret Key`，本实现会把它当作 `api_key` 兜底使用
- `provider` 可选：
  - `auto`：优先火山引擎，失败回退 Whisper
  - `volcengine`：只用火山引擎
  - `whisper`：只用本地 Whisper

## 火山引擎接口说明

- 文档参考：`大模型录音文件极速版识别API`
- 实际接入地址：
  - `POST https://openspeech.bytedance.com/api/v3/auc/bigmodel/recognize/flash`
- 资源 ID：
  - `volc.bigasr.auc_turbo`
- 当前实现使用 `audio.data` 方式上传提取后的音频 base64，因此：
  - **抖音链接模式**：下载视频后抽音频，再直接调用极速版
  - **本地文件 / stdin**：同样直接走极速版，不再依赖公网 URL

## 参数说明

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `input` | 抖音链接或视频文件路径 | 必填 |
| `-o / --output-dir` | 链接模式的输出目录 | `./output` |
| `-m / --model` | Whisper 模型：`tiny/base/small/medium/large` | `base` |
| `--json` | 以 JSON 格式输出完整结果 | 否 |
| `--stdin` | 从标准输入读取视频数据流 | 否 |

## MCP 服务

- **端点**：`http://16tufi081507.vicp.fun:8086/mcp?key=763b0781bd293135b391347ebb83c7a9`
- **协议**：MCP Streamable HTTP（2024-11-05）
- **使用工具**：`mcp_parse_video_api_mcp_parse_get`
- **实现策略**：先按 MCP `initialize -> tools/list -> tools/call` 调用；若服务端返回未登录/未授权，则自动回退到同服务提供的 `/api/mcp/parse`

## 输出示例

```
[MCP] 正在解析链接: https://v.douyin.com/ixxxxxx/
[解析结果]
  标题/文案: 为什么一定要上800V主动悬架？...
  作者: 李想
  视频URL: https://...
[下载] 正在下载视频...
[ASR] 提取音频中...
[ASR] 加载 Whisper 模型 (base)...
[ASR] 语音识别中，请稍候...

============================================================
【最终结果】
============================================================
▶ 原始文案:  为什么一定要上800V主动悬架？...
▶ 封面路径:  ./output/cover.jpg
▶ 视频路径:  ./output/video.mp4

▶ ASR 识别文字:
今天我们来聊一聊800V主动悬架的问题...
============================================================
```
