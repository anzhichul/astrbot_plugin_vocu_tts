# astrbot_plugin_vocu_tts

AstrBot 插件 —— 通过 [Vocu](https://vocu.ai/) TTS API 将机器人的文本回复自动转为语音发送。特别适合 TRPG 跑团等场景，支持括号内容过滤与情绪映射。

> **原项目地址**：[Salieri-Amadeus/VocuTTS_Astrbot](https://github.com/Salieri-Amadeus/VocuTTS_Astrbot)
>
> 本仓库是基于原项目的**修复/增强版**，改动内容见下方 [与原项目差异](#与原项目差异)。

## 功能

- 在群聊中开启后，机器人发送文本后按概率自动转语音（命中只发语音、未命中只发文字）
- 支持会话级别的开关与配置覆盖，不同群可以使用不同的声音角色
- TRPG 友好：可自动过滤括号中的动作描述（不朗读），或将其中的情绪关键词映射为 Vocu 的情绪控制参数
- 完整暴露 Vocu API 的生成参数（预设、语速、语言、活泼表达、情绪控制等），均可在 WebUI 中配置
- AI 可主动调用 `send_voice` 工具发送语音

## 与原项目差异

本分支相对原项目 `Salieri-Amadeus/VocuTTS_Astrbot` 的修复/改进如下：

1. **语音触发概率**（新增配置 `voice_probability`，默认 50%）：命中概率时**只发语音不发文字**，未命中时**只发文字不发语音**。原项目是每条文字回复后都追加语音。通过发送前 `on_decorating_result` 钩子替换文字为语音实现，并增加 `VOCUTTS_DECORATING_DONE_FLAG` 防止与原 `after_message_sent` 逻辑重复处理。
2. **代理支持**（新增配置 `proxy`）：API 请求、音频下载、角色列表均支持 HTTP/SOCKS5 代理，解决国内网络访问 Vocu API 的问题。
3. **去除英文乱读**（新增配置 `remove_english`，默认开启）：合成前移除 URL、英文单词/标识符，防止插件名、链接等被中文 TTS 错误朗读。
4. **音频后处理**（新增配置 `post_process` / `post_slow_factor`）：发送前用 ffmpeg 做放慢（atempo）+ 响度归一化（loudnorm），改善 QQ 压缩后的听感。需要系统安装 `ffmpeg`。
5. **AI 主动发语音工具**（新增 LLM 工具 `send_voice`）：AI 可主动决定用语音回应，而不只是靠概率。

## 前置准备

1. 前往 [Vocu API Platform](https://app.vocu.ai/apiKey) 创建 API Key
2. 在 Vocu 控制台创建或选择一个声音角色，记录其 Voice Character ID
3. 在 AstrBot WebUI 的插件配置中填入上述信息

## 指令

所有指令通过 `/vocutts` 命令组调用：

| 指令 | 说明 |
|------|------|
| `/vocutts on` | 开启当前会话的语音合成 |
| `/vocutts off` | 关闭当前会话的语音合成 |
| `/vocutts status` | 查看当前会话的 VocuTTS 状态与配置 |
| `/vocutts voice <id>` | 为当前会话设置声音角色 ID |
| `/vocutts voices` | 列出账号下所有可用的声音角色 |
| `/vocutts style <id>` | 为当前会话设置声音风格（Style/Prompt ID） |
| `/vocutts preset <creative\|balance\|stable>` | 切换生成预设策略 |

## 配置项

在 AstrBot WebUI 的插件配置页面中设置：

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `api_key` | string | - | Vocu API Key |
| `api_base_url` | string | `https://v1.vocu.ai` | API 基础 URL |
| `proxy` | string | 空 | HTTP/SOCKS5 代理地址（如 `http://127.0.0.1:10808`），留空直连 |
| `voice_probability` | int | `50` | 语音触发概率（0-100）：命中只发语音、未命中只发文字 |
| `remove_english` | bool | `true` | 合成前移除英文/URL/标识符，防止中文 TTS 乱读 |
| `post_process` | bool | `true` | 发送前用 ffmpeg 处理音频（放慢 + 响度归一化） |
| `post_slow_factor` | float | `0.88` | 音频放慢系数（1.0=不变，越小越慢越清晰，建议 0.8-0.95） |
| `voice_id` | string | - | 默认 Voice Character ID |
| `prompt_id` | string | `default` | 声音风格 ID |
| `preset` | string | `balance` | 生成预设：`creative` / `balance` / `stable` |
| `language` | string | `auto` | 语言：`auto` `zh` `en` `ja` `ko` `fr` `es` `de` `pt` `yue` |
| `speech_rate` | float | `1.0` | 语速（0.5 - 2.0） |
| `vivid` | bool | `false` | 活泼表达模式（仅 V3.0 角色） |
| `break_clone` | bool | `true` | 情绪偏向文本（根据文本语境自动推断情绪） |
| `flash` | bool | `false` | 低延迟模式 |
| `bracket_mode` | string | `strip` | 括号处理方式（见下文） |
| `bracket_pattern` | string | 匹配中英文圆括号和方括号 | 括号匹配正则表达式 |
| `emotion_keywords` | text | 预置 16 个中文关键词 | 情绪关键词映射表（JSON） |

## 括号处理模式

针对 TRPG 等场景中括号内动作描述的处理，提供三种模式：

### `strip`（默认）

移除所有匹配括号及其内容，只朗读对白部分。

> 输入：`"你好啊。（微笑着挥手）今天天气真好。"`
> 朗读：`"你好啊。今天天气真好。"`

### `emotion_hint`

同样移除括号内容不朗读，但会从中提取情绪关键词，映射到 Vocu 的 `emo_switch` 参数影响语音情感。

> 输入：`"你怎么敢！（愤怒地拍桌子）"`
> 朗读：`"你怎么敢！"`（带愤怒情绪）

情绪映射为 5 维数组 `[愤怒, 开心, 中性, 悲伤, 匹配上下文]`，值域 0-10。预置关键词包括：愤怒、生气、开心、高兴、微笑、悲伤、难过、哭、平静、冷漠、温柔、紧张、害怕、惊讶等。可在 `emotion_keywords` 配置中自定义。

### `keep`

保留括号内容原样朗读。

## 工作原理

```
用户发消息 → AstrBot 处理并回复文本 → VocuTTS 钩子按概率决定转语音
→ 处理括号/去英文 → 调用 Vocu API 生成语音 → 发送语音消息（或保持文字）
```

## License

MIT
