# DouYin2Text

> 多媒体内容提取 + 语音识别工具  
> 支持 **抖音链接解析** 和 **本地视频 ASR** 两种模式

## 功能

| 模式 | 输入 | 处理流程 | 输出 |
|------|------|----------|------|
| 抖音链接 | 分享链接或整段分享文案（如 `https://v.douyin.com/xxx`） | 自动抽取分享文案中的抖音链接 → **MCP 协议调用** → 必要时 REST 回退 → 下载视频/封面 → ASR | 原始文案 + **逐句分行 / 段落整理** ASR + 封面/视频文件 + 视频元数据 |
| 本地视频 | `.mp4` / `.mov` 等视频文件路径 | 直接 ASR | **逐句分行 / 段落整理** ASR + 视频元数据 |
| 视频流 | `stdin` 二进制流 | 落盘临时文件 → ASR | **逐句分行 / 段落整理** ASR + 视频元数据 |

## 依赖

- Python 3.10+
- `ffmpeg`（系统级，需已安装）
- Python 包：`openai-whisper`, `requests`

```bash
pip install openai-whisper requests
```

## 启动 Web 服务

本项目提供可视化 Web 界面，支持链接提取、视频上传、结果预览、历史回看，以及手动切换火山模型 / Whisper。

### 前置要求

- Python 3.10+
- `ffmpeg`（系统级，需已安装）

### 详细步骤

1. **创建并激活虚拟环境**（推荐）

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate  # macOS/Linux
   # 或 Windows: .venv\Scripts\activate
   ```

2. **安装 Python 依赖**

   ```bash
   pip install -r requirements.txt
   ```

   依赖包包括：
   - `flask`：Web 服务框架
   - `requests`：HTTP 请求库
   - `openai-whisper`：本地语音识别模型

3. **安装 ffmpeg**

   **macOS**（使用 Homebrew）：
   ```bash
   brew install ffmpeg
   ```

   **Ubuntu/Debian**：
   ```bash
   sudo apt update && sudo apt install ffmpeg
   ```

   **Windows**：下载并安装 [ffmpeg](https://ffmpeg.org/download.html)，并添加到系统 PATH。

4. **准备配置文件**

   ```bash
   cp config.example.json config.json
   ```

   然后编辑 `config.json`，填入火山引擎 ASR 凭证。若你只想先验证页面能不能跑起来，也可以先保留默认配置，等真正开始提取时再补齐。

   **配置说明**：
   - 旧版控制台可填 `app_id + access_token`
   - 新版控制台可直接填 `api_key`
   - `provider` 可选：`auto`（优先火山引擎，失败回退 Whisper）、`volcengine`（只用火山引擎）、`whisper`（只用本地 Whisper）

5. **启动 Web 服务**

   ```bash
   python3 app.py
   ```

   默认会监听本地 `127.0.0.1:5088`。如需局域网访问，可这样启动：

   ```bash
   APP_HOST=0.0.0.0 APP_PORT=5088 python3 app.py
   ```

6. **打开浏览器访问**

   ```text
   http://127.0.0.1:5088
   ```

7. **关闭服务**

   在启动服务的终端里按 `Ctrl + C` 即可。

## Docker / NAS 部署

仓库已补齐 Docker 所需文件：`Dockerfile`、`docker-compose.yml`、`.dockerignore`。

### 适合 NAS 的默认建议

- **优先使用火山模型**：大多数 NAS CPU 性能有限，建议把页面里的识别引擎默认切到火山模型。
- **Whisper 作为可选能力**：如果 NAS 性能足够，构建镜像时再开启 Whisper 支持。
- **数据持久化**：配置、历史记录、输出文件和 Whisper 缓存都挂载到宿主机目录，容器重建后不会丢。

### 1. 准备持久化目录

在 NAS 上创建一个目录，例如：

```text
/volume1/docker/douyin2text
```

把仓库上传到 NAS，例如：

```text
/volume1/docker/douyin2text/app
```

然后在项目目录下准备持久化数据目录：

```bash
mkdir -p docker-data
cp config.example.json docker-data/config.json
```

### 2. 修改配置

编辑：

```text
docker-data/config.json
```

至少填好火山引擎凭证；如果你要用**抖音链接模式**，再补充 `douyin_parser`。推荐：

```json
{
  "volcengine_asr": {
    "provider": "volcengine",
    "app_id": "",
    "access_token": "",
    "api_key": "",
    "uid": "douyin2text",
    "resource_id": "volc.bigasr.auc_turbo",
    "audio_format": "mp3"
  },
  "douyin_parser": {
    "base_url": "",
    "api_key": ""
  }
}
```

### 3. 启动容器

#### 方案 A：普通 NAS，优先火山模型（推荐）

直接拉取 Docker Hub 预构建镜像，不安装 Whisper，镜像更小、启动更快：

```bash
docker compose pull
docker compose up -d
```

默认访问地址：

```text
http://NAS-IP:5088
```

#### 方案 B：需要本地 Whisper

Docker Hub 默认镜像不包含 Whisper。如果 NAS 性能足够，需要你在源码目录本地构建：

```bash
docker build --build-arg INSTALL_WHISPER=true -t douyin2text:whisper .
```

### 4. 常用命令

```bash
# 查看日志
docker compose logs -f

# 重启
docker compose restart

# 停止
docker compose down

# 拉取最新镜像并启动
docker compose pull
docker compose up -d
```

### 5. 在群晖 / 威联通图形界面里怎么填

如果你不用命令行，也可以直接在 NAS 的 Docker / Container Manager 里创建容器：

| 项目 | 建议值 |
|------|--------|
| 镜像名称 | `huang1147698557/douyin2text:latest` |
| 端口映射 | `5088:5088` |
| 环境变量 `APP_HOST` | `0.0.0.0` |
| 环境变量 `APP_PORT` | `5088` |
| 环境变量 `DOUYIN2TEXT_CONFIG_PATH` | `/data/config.json` |
| 环境变量 `DOUYIN2TEXT_WEB_OUTPUT_DIR` | `/data/output/web` |
| 环境变量 `XDG_CACHE_HOME` | `/data/.cache` |
| 环境变量 `DOUYIN_PARSER_BASE_URL` | 你的抖音解析服务地址（链接模式必填） |
| 环境变量 `DOUYIN_PARSER_API_KEY` | 你的抖音解析服务密钥（链接模式必填） |
| 卷映射 | NAS 宿主机目录映射到容器 `/data` |
| 重启策略 | `unless-stopped` |

### 6. 本次为了 Docker 做了哪些代码修改

1. `app.py` 支持通过环境变量设置 `APP_HOST` / `APP_PORT`，容器内可直接对外监听。  
2. `app.py` 和 `douyin2text.py` 支持通过环境变量指定配置文件和输出目录路径，方便挂载 NAS 持久化目录。  
3. 新增 `Dockerfile`、`docker-compose.yml`、`.dockerignore`，避免把本地 `config.json`、输出目录和无关文件打进镜像。  
4. 把抖音解析服务改成从环境变量 / `config.json` 读取，避免把敏感密钥写进公开镜像。  

### 7. NAS 部署注意事项

- 如果你的 NAS 是 **ARM 架构**，本地 Whisper 可能安装慢、镜像更大，建议优先用火山模型。
- 如果你的 NAS 是 **x86 架构**，开启 Whisper 成功率更高。
- 首次开启 Whisper 时会下载模型文件，缓存会保存在挂载目录 `/data/.cache`。
- 容器更新后，原有历史记录和输出文件都还在 `docker-data/` 里。

### 常见问题

- **端口被占用**：修改 `app.py` 最后一行的 `port=5088` 为其他端口号。
- **端口被占用**：换一个端口启动，例如 `APP_PORT=5090 python3 app.py`，或在 `docker-compose.yml` 中修改 `HOST_PORT`。
- **ASR 识别失败**：检查 `config.json` 中火山引擎凭证是否正确，或在页面里切换为 Whisper 本地模型。
- **抖音链接解析失败**：检查 `DOUYIN_PARSER_BASE_URL` / `DOUYIN_PARSER_API_KEY`，或 `config.json` 中 `douyin_parser` 配置是否正确。
- **视频上传失败**：确保 `ffmpeg` 已正确安装并可执行。

## 快速使用

```bash
# 先在 config.json 填入火山引擎 ASR 凭证
# 若要使用抖音链接模式，再配置 douyin_parser 或对应环境变量
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
  "douyin_parser": {
    "base_url": "https://your-parser-service.example.com",
    "api_key": ""
  },
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

## 抖音解析服务

- **配置方式**：优先读取环境变量 `DOUYIN_PARSER_BASE_URL` / `DOUYIN_PARSER_API_KEY`，否则读取 `config.json` 的 `douyin_parser`
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
