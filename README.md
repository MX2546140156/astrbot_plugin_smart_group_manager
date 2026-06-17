# 智能群管理插件 (astrbot_plugin_smart_group_manager)

基于 AstrBot、aiocqhttp、napcat API 开发的QQ群互动、管理插件，目标成为更好用的QQ群管理插件。

## 功能

### 自动审批

| 配置项 | 行为 |
|--------|------|
| `enable_friend_request` | 启用后自动通过所有好友申请 |
| `enable_group_request` | 启用后自动接受他人邀请机器人进群 |
| `auto_approve_group_join` | 启用后机器人作为群管理时自动通过用户的加群申请 |

### 白名单

| 配置项 | 行为 |
|--------|------|
| `whitelist` | 用户白名单，仅在这些好友/私聊用户启用插件功能，否则忽略所有消息且不作回复及响应。留空则对所有用户生效 |
| `white_group` | 群聊白名单，仅在这些群启用插件功能，否则忽略所有消息且不作回复及响应。留空则对所有群生效 |
| `blacklist` | 用户黑名单，黑名单用户发消息时自动禁言、申请加群时自动拒绝，不受关键词/AI审核开关影响 |

### 戳一戳互动

| 触发方式 | 行为 |
|---------|------|
| 当有人戳机器人时 | 50% 概率随机回复一句并戳回去，50% 概率只回复不戳回 |

**说明：**
- 回复内容可通过 `poke_back_replies`（戳回去时使用）和 `poke_noreply_replies`（不戳回去时使用）配置项自定义
- 两个配置项均为字符串列表，留空则不回复
- 不配置时使用内置默认回复

### 进群欢迎

| 触发方式 | 行为 |
|---------|------|
| 新成员加入群聊 | @新成员 + 发送自定义欢迎语 + 可选欢迎图片（支持多张） |

**说明：**
- 消息默认自动 @新成员，无需手动添加
- 支持 `\n` 换行符
- 支持 `{nickname}` 和 `{user_id}` 模板变量
- 留空则不发送欢迎消息

**默认欢迎语：** `欢迎加入本群，请遵守群规～`

**图片支持（列表，可配置多张）：**
- URL：`https://example.com/welcome.png`
- 本地文件：`images/welcome.jpg`（相对于插件目录）

### 退群通知

| 触发方式 | 行为 |
|---------|------|
| 有成员退出群聊（主动退群/被踢） | 发送自定义退群通知消息 + 可选通知图片（支持多张） |

**说明：**
- 退群后无法获取昵称，仅支持 `{user_id}` 变量（输出QQ号）
- 支持 `\n` 换行符
- 留空则不发送退群通知

**图片支持（列表，可配置多张）：**
- URL：`https://example.com/leave.png`
- 本地文件：`images/leave.jpg`（相对于插件目录）

### 群管理命令

管理员可在群内发送以下命令操作黑名单和踢人：

| 命令 | 说明 |
|------|------|
| `拉黑 QQ号` `拉黑 @用户` | 将用户加入黑名单并禁言（时长由 `blacklist_mute_duration` 决定） |
| `解黑 QQ号` `解黑 @用户` | 将用户移出黑名单并解除禁言 |
| `黑名单列表` | 查看当前黑名单中的所有 QQ 号 |
| `禁言 @用户 秒数` `禁言 QQ号 秒数` | 禁言指定用户指定时长（秒），不填秒数则使用配置的 `mute_duration` |
| `解禁 @用户` `解禁 QQ号` | 解除指定用户的禁言 |
| `踢出 QQ号` `踢出 @用户` | 将用户踢出群聊 |

**说明：**
- 需在配置中启用 `enable_admin_commands` 开关
- 仅群管理员/群主可执行以上命令
- QQ号支持纯数字或 @提及
- 黑名单修改会自动保存到 `blacklist.json` 文件，重启后仍生效

### 自动禁言

| 触发方式 | 行为 |
|---------|------|
| 群成员发送违规消息 | 自动禁言该成员指定时长 + 可选回复提示 |

**检测方式（二选一或同时开启）：**
- **正则关键词** — 消息匹配到 `mute_keywords` 中任一正则即触发禁言
- **AI 审核** — 调用 AstrBot 配置的 AI 提供商判断消息是否违规

**说明：**
- 关键词与AI审核为「或」关系，任一触发即禁言
- 可设置 `mute_whitelist` 排除特定用户
- 开启 `mute_recall` 后可同时撤回违规消息
- 需机器人具备群管理员权限才能执行禁言和撤回

### LLM 回复内容过滤

| 触发方式 | 行为 |
|---------|------|
| AI（LLM）回复群聊/私聊消息时 | 自动按规则过滤替换回复内容中的指定文本 |

**说明：**
- 通过 `llm_filter_rules` 配置过滤规则，列表不为空即生效
- 每项格式为 `正则表达式=>替换文本`，按顺序依次匹配替换
- 支持正则分组引用（如 `\1`）
- 替换文本留空表示删除匹配到的内容
- 示例规则：
  - `敏感词=>***` — 将"敏感词"替换为 `***`
  - `https?://\S+=>[链接已过滤]` — 过滤 URL
  - `广告语=>` — 删除"广告语"（替换为空）

## 配置项

在 AstrBot WebUI → 插件管理 → 群管理插件 → 配置 中修改：

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `enable_friend_request` | bool | `false` | 启用后，所有好友申请将自动通过 |
| `enable_group_request` | bool | `false` | 启用后，自动接受他人邀请机器人进群 |
| `auto_approve_group_join` | bool | `false` | 启用后，机器人作为群管理时自动通过用户的加群申请 |
| `whitelist` | list | `[]` | 用户白名单，仅在这些好友/私聊用户启用插件功能。示例：`[123456789, 987654321]` |
| `whitelist_group` | list | `[]` | 群聊白名单，仅在这些群启用插件功能。示例：`[123456789, 987654321]` |
| `blacklist` | list | `[]` | 用户黑名单，黑名单用户在任何群发消息时自动禁言，不依赖关键词/AI审核。示例：`["123456789"]` |
| `enable_admin_commands` | bool | `false` | 启用后群管理员可在群内发送「拉黑」「解黑」「踢出」等命令管理群聊 |
| `poke_enabled` | bool | `true` | 启用戳一戳互动，关闭后机器人不会对戳一戳事件做任何响应 |
| `poke_back_replies` | list | 内置默认列表 | 戳一戳互动中「戳回去」时随机回复的内容列表，留空则不回复。示例：`["反弹！", "看我戳回去"]` |
| `poke_noreply_replies` | list | 内置默认列表 | 戳一戳互动中「不戳回去」时随机回复的内容列表，留空则不回复。示例：`["干嘛戳我", "别戳了"]` |
| `welcome_text` | string | `欢迎加入本群，请遵守群规～` | 进群欢迎内容，支持 `\n` 换行和 `{nickname}`/`{user_id}` 模板，留空则不发送 |
| `welcome_image_url` | list | `[]` | 欢迎附带图片列表，支持 URL 或本地路径（相对于插件目录），留空则不附带 |
| `leave_text` | string | `` | 退群通知内容，支持 `\n` 换行和 `{user_id}` 变量，留空则不发送 |
| `leave_image_url` | list | `[]` | 退群通知附带图片列表，支持 URL 或本地路径（相对于插件目录），留空则不附带 |
| `enable_auto_mute` | bool | `false` | 启用后对触发规则的群成员自动禁言 |
| `mute_keywords` | list | `[]` | 禁言触发正则表达式列表，匹配任一即禁言。示例：`["广告", "\\\\d{11,}"]` |
| `mute_ai_review` | bool | `false` | 启用AI审核禁言，与关键词「或」关系 |
| `mute_ai_prompt` | string | `判断以下群聊消息是否包含违规内容...` | AI审核提示词，与用户消息拼接后发给AI |
| `mute_duration` | int | `600` | 禁言时长（秒），`0` 表示解除禁言 |
| `mute_recall` | bool | `false` | 启用后自动撤回触发禁言的违规消息 |
| `mute_whitelist` | list | `[]` | 禁言白名单，这些用户不会被禁言。示例：`["123456789"]` |
| `mute_reply` | string | `` | 关键词/AI审核触发禁言时的回复，留空不回复。支持 `{user_id}` 和 `{mute_duration}`（自动转为天时分秒） |
| `blacklist_mute_duration` | int | `2592000` | 黑名单用户禁言时长（秒），默认 30 天 |
| `blacklist_mute_reply` | string | `` | 黑名单用户自动禁言时的回复，与 `mute_reply` 分开配置。支持 `{user_id}` 和 `{mute_duration}`（自动转为天时分秒） |
| `llm_filter_rules` | list | `[]` | LLM 回复过滤规则，列表不为空即生效。每项格式 `正则=>替换`，替换为空则删除匹配内容。示例：`["敏感词=>***", "https?://\\\\S+=>[链接已过滤]"]` |

**配置示例：**

```json
{
    "enable_friend_request": true,
    "enable_group_request": true,
    "auto_approve_group_join": false,
    "whitelist": ["123456789", "987654321"],
    "whitelist_group": ["111111111", "222222222"],
    "blacklist": [],
    "enable_admin_commands": true,
    "welcome_text": "欢迎加入本群！\\n请遵守群规～",
    "welcome_image_url": ["images/welcome.jpg", "https://example.com/welcome.png"],
    "leave_text": "悄悄是离别的笙歌～{user_id} 离开了我们",
    "leave_image_url": [],
    "enable_auto_mute": true,
    "mute_keywords": ["广告", "\\d{11,}"],
    "mute_ai_review": false,
    "mute_duration": 600,
    "mute_whitelist": [],
    "mute_reply": "[CQ:at,qq={user_id}] 你已被禁言 {mute_duration}",
    "blacklist_mute_duration": 2592000,
    "blacklist_mute_reply": ""
}
```

## 依赖

- AstrBot >= 4.16
- OneBot v11 协议端（NapCat 等）

## 安装

将 `plugin_smart_group_manager` 目录放入 AstrBot 的 `data/plugins/` 目录下，重启 AstrBot 或在 WebUI 中重载插件即可。
