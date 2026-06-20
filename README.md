# yt2bili

把已获授权的 YouTube 频道视频，自动加上中英双字幕，定时搬运到 B 站。

一句话原理：**三个后台定时任务全自动跑，你只在网页上点几下鼠标。**

```
YouTube 新视频
   │  ① 发现器（每小时）扫频道，发现新视频
   ▼
下载 → 英文字幕 → 中文翻译 → 烧录中字
   │  ② worker（每10分钟）一步步往下推
   ▼
就绪 ──┬─ 普通视频 ───────────────▶ 直接进发布队列
       └─ 重点视频 ─▶ 网页上等你批准 ─▶ 进队列
                                         │  ③ 发布器（每天19:00）上传
                                         ▼
                                      发到 B 站
```

---

## 一、首次准备（只做一次）

### 1. 安装 Python 依赖

```bash
cd ~/yt2bili
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

### 2. 外部工具（命令行工具，需提前装好）

| 工具 | 用途 | 安装 |
|------|------|------|
| yt-dlp | 下载视频/字幕 | `brew install yt-dlp` |
| ffmpeg | 烧录字幕 | `brew install ffmpeg` |
| whisper-cpp | 本地语音识别（没有CC时兜底） | `brew install whisper-cpp` |
| biliup | 上传到 B 站 | 见其官方文档 |

### 3. Whisper ASR 模型

whisper 需要单独下载模型文件（约 465MB，不入 git）：

```bash
mkdir -p ~/.whisper/models
curl -L "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.en.bin" \
  -o ~/.whisper/models/ggml-small.en.bin
```

验证：`ls -lh ~/.whisper/models/ggml-small.en.bin`（约 465MB）

> 管道默认用 `ggml-small.en.bin`。换其他模型需改 `src/yt2bili/stages/subtitle.py` 里的路径。

### 4. biliup 登录（B 站上传授权）

```bash
biliup login
```

用 B 站 App 扫码，凭证存到本地 `cookies.json`，有效期约 30 天，过期需重新登录。

> `cookies.json` 含账号凭证，已在 `.gitignore` 中，不会入 git。

### 5. 填写配置

复制示例配置，填入你的频道和 MiniMax key：

```bash
cp config.yaml.example ~/yt2bili/config.yaml
# 用编辑器打开 ~/yt2bili/config.yaml 修改
```

至少要改两处：
- `channels` 下填你已获授权的 YouTube 频道 ID（形如 `UCxxxx`）
- `api.minimax_key` 填你的 MiniMax API key（明文，配置文件已被 git 忽略）

> 数据默认放在 `~/yt2bili/`。想换目录，设环境变量 `YT2BILI_HOME`。

---

## 二、启动（一条命令）

```bash
bash deploy/install.sh
```

这会：渲染并加载 4 个 launchd 任务（发现器/worker/发布器/Web面板），创建数据目录和日志目录。重复运行安全（会先卸载旧任务）。

装好后：
- **控制台网页：** http://127.0.0.1:8080
- **日志：** `~/yt2bili/logs/`

停止全部任务：

```bash
bash deploy/uninstall.sh
```

---

## 三、日常使用：全在网页上

打开 http://127.0.0.1:8080：

- **顶部统计卡片**：处理中 / 待审核 / 已就绪 / 已发布 / 失败 / 跳过 各有多少
- **视频列表**：每条显示标题、频道、当前状态、字幕来源、更新时间
- **操作按钮**：
  - 待审核的重点视频 → 「批准」放行，或「跳过」忽略
  - 失败的视频 → 「重试」，下次 worker 重跑失败那一步
  - 任意视频 → 「标重点」翻转优先级
- **发布暂停提示**：连续 3 次上传失败会自动暂停发布（多半是 biliup 登录过期），重新 `biliup login` 后在网页点「恢复发布」

普通视频你的工作量为 0；重点视频只需扫一眼点个批准。

---

## 四、手动调试（可选）

不靠定时任务，手动跑某一个环节：

```bash
cd ~/yt2bili
export PYTHONPATH=src
.venv/bin/python -m yt2bili.runner discover   # 立即扫一次频道
.venv/bin/python -m yt2bili.runner worker     # 立即推进一轮流水线
.venv/bin/python -m yt2bili.runner publish    # 立即发布一轮
.venv/bin/python -m yt2bili.runner web        # 前台起控制台
```

跑测试：

```bash
.venv/bin/python -m pytest tests/ -v
```

详细设计见 `docs/plans/`。

