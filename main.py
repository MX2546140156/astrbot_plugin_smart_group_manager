"""
群管理插件 - 支持成员互动、自动审批、进群欢迎等功能。
"""

import asyncio
import json
import os
import random
import re
from pathlib import Path

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.event.filter import on_llm_response
from astrbot.api.provider import LLMResponse
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

# 尝试导入 astrbot.api.web 插件页面框架，fallback 到 Quart 原生 API
try:
    from astrbot.api.web import json_response, error_response, request
except ModuleNotFoundError:
    from quart import request as _quart_request

    class _CompatRequest:
        """兼容包装，提供 request.json(default=...) 接口"""
        async def json(self, default=None):
            try:
                data = await _quart_request.get_json()
                if data is not None:
                    return data
            except Exception:
                pass
            return default

    request = _CompatRequest()

    def json_response(data):
        """返回 JSON 成功响应"""
        return data

    def error_response(message, status_code=400):
        """返回 JSON 错误响应"""
        return {"message": message}, status_code


@register("astrbot_plugin_smart_group_manager", "developer", "智能QQ群管理插件", "1.4.0")
class SmartGroupManager(Star):
    """智能QQ群管理插件"""

    # 使用 AstrBot 标准插件数据目录
    _DATA_DIR = Path(get_astrbot_data_path()) / "plugin_data" / "astrbot_plugin_smart_group_manager"
    os.makedirs(_DATA_DIR, exist_ok=True)
    BLACKLIST_FILE = str(_DATA_DIR / "blacklist.json")
    INVITATION_FILE = str(_DATA_DIR / "invitations.json")
    APPLICANTS_FILE = str(_DATA_DIR / "public_applicants.json")

    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.config = config

        # 基础配置
        self.enable_friend_request: bool = config.get("enable_friend_request", False)
        self.enable_group_request: bool = config.get("enable_group_request", False)
        self.auto_approve_group_join: bool = config.get("auto_approve_group_join", False)
        # 统一转为 str，兼容用户填数字的情况（schema items type 为 string，但示例为数字）
        self.whitelist: list = [str(item) for item in config.get("whitelist", [])]
        self.whitelist_group: list = [str(item) for item in config.get("whitelist_group", [])]
        # 合并配置文件黑名单（作为全局黑名单）+ 持久化黑名单
        config_blacklist = [str(item) for item in config.get("blacklist", [])]
        loaded = self._load_blacklist()
        self.global_blacklist: list = list(dict.fromkeys(config_blacklist + loaded.get("global", [])))
        self.group_blacklist: dict = loaded.get("groups", {})
        self.friend_blacklist: list = [str(item) for item in loaded.get("friends", [])]

        # 欢迎配置
        self.welcome_text: str = config.get("welcome_text", "欢迎加入本群，请遵守群规～")
        raw_images = config.get("welcome_image_url", [])
        if isinstance(raw_images, list):
            self.welcome_image_urls: list = raw_images
        elif isinstance(raw_images, str):
            self.welcome_image_urls = [raw_images] if raw_images else []
        else:
            self.welcome_image_urls = []

        # 退群通知配置
        self.leave_text: str = config.get("leave_text", "")
        raw_leave_images = config.get("leave_image_url", [])
        if isinstance(raw_leave_images, list):
            self.leave_image_urls: list = raw_leave_images
        elif isinstance(raw_leave_images, str):
            self.leave_image_urls = [raw_leave_images] if raw_leave_images else []
        else:
            self.leave_image_urls = []

        # 自动禁言配置
        self.enable_auto_mute: bool = config.get("enable_auto_mute", False)
        self.mute_keywords: list = config.get("mute_keywords", [])
        self.mute_ai_review: bool = config.get("mute_ai_review", False)
        self.mute_ai_prompt: str = config.get("mute_ai_prompt", "判断以下群聊消息是否包含违规内容（广告、刷屏、恶意链接等）。仅回复yes或no：")
        self.mute_duration: int = int(config.get("mute_duration", 600))
        self.mute_recall: bool = config.get("mute_recall", False)
        self.mute_whitelist: list = [str(item) for item in config.get("mute_whitelist", [])]
        self.mute_reply: str = config.get("mute_reply", "")
        self.blacklist_mute_duration: int = int(config.get("blacklist_mute_duration", self.mute_duration))
        self.blacklist_mute_reply: str = config.get("blacklist_mute_reply", "")
        self.enable_admin_commands: bool = config.get("enable_admin_commands", False)
        self.blacklist_admin: list = [str(item) for item in config.get("blacklist_admin", [])]
        self.enable_public_commands: bool = config.get("enable_public_commands", False)
        # 持久化的自助申请白名单（运行时与 blacklist_admin 合并生效）
        self._public_applicants: list = self._load_public_applicants()
        self.enable_auto_kick: bool = config.get("enable_auto_kick", False)
        self.enable_chain_blacklist: bool = config.get("enable_chain_blacklist", False)
        self.poke_enabled: bool = config.get("poke_enabled", True)

        # LLM 回复过滤配置
        self.llm_filter_rules: list = config.get("llm_filter_rules", [])

        # 戳一戳回复配置（直接读取配置，默认值由 _conf_schema.json 提供）
        self.poke_back_replies: list = config.get("poke_back_replies", [])
        self.poke_noreply_replies: list = config.get("poke_noreply_replies", [])

        # ============================================================
        #  Scoped 配置（好友/群单独覆盖）
        # ============================================================
        self.scoped_config_file = str(self._DATA_DIR / "scoped_config.json")
        self.scoped_config = self._load_scoped_config()
        self._apply_default_overrides()

        # 缓存的机器人客户端（供管理页面获取群/好友列表）
        self._cached_bot = None

        # ============================================================
        #  注册 Web API（管理页面用）
        # ============================================================
        self._register_web_apis(context)

    # ============================================================
    #  白名单检查
    # ============================================================

    def _is_user_allowed(self, user_id: str) -> bool:
        """检查用户是否在私聊白名单内"""
        if not self.whitelist:
            return True
        return str(user_id) in self.whitelist

    def _is_group_allowed(self, group_id: str) -> bool:
        """检查群是否在群白名单内"""
        if not self.whitelist_group:
            return True
        return str(group_id) in self.whitelist_group

    # ============================================================
    #  黑名单持久化
    # ============================================================

    def _load_blacklist(self) -> dict:
        """从本地文件加载黑名单（全局 + 群维度的）"""
        try:
            if os.path.exists(self.BLACKLIST_FILE):
                with open(self.BLACKLIST_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        return {
                            "global": [str(item) for item in data.get("global", [])],
                            "groups": {str(k): [str(v) for v in vals] for k, vals in data.get("groups", {}).items()},
                            "friends": [str(item) for item in data.get("friends", [])],
                        }
        except Exception as e:
            logger.warning(f"加载黑名单文件失败: {e}")
        return {"global": [], "groups": {}, "friends": []}

    def _save_blacklist(self):
        """保存黑名单到本地文件"""
        try:
            data = {
                "global": self.global_blacklist,
                "groups": self.group_blacklist,
                "friends": self.friend_blacklist,
            }
            with open(self.BLACKLIST_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存黑名单文件失败: {e}")

    # ============================================================
    #  邀请关系追踪（连带拉黑用）
    # ============================================================

    def _load_invitations(self) -> dict:
        """加载邀请关系数据，结构：{group_id: [[inviter, invitee], ...]}"""
        try:
            if os.path.exists(self.INVITATION_FILE):
                with open(self.INVITATION_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception as e:
            logger.warning(f"加载邀请关系文件失败: {e}")
        return {}

    def _save_invitations(self, data: dict):
        """保存邀请关系数据"""
        try:
            with open(self.INVITATION_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存邀请关系文件失败: {e}")

    def _record_invitation(self, group_id: str, inviter_id: str, invitee_id: str):
        """记录一条邀请关系"""
        data = self._load_invitations()
        group_key = str(group_id)
        pairs = data.get(group_key, [])
        # 已经存在的不重复记录
        pair = [inviter_id, invitee_id]
        if pair not in pairs:
            pairs.append(pair)
            data[group_key] = pairs
            self._save_invitations(data)
            logger.info(f"[邀请追踪] 群 {group_id}：{inviter_id} 邀请了 {invitee_id}")

    def _get_chain_targets(self, group_id: str, target_id: str) -> list:
        """获取与 target_id 在同一邀请链上的所有成员（不含 target_id 自身）"""
        data = self._load_invitations()
        pairs = data.get(str(group_id), [])
        if not pairs:
            return []

        # 构建无向图邻接表
        graph: dict[str, set] = {}
        for a, b in pairs:
            graph.setdefault(a, set()).add(b)
            graph.setdefault(b, set()).add(a)

        if target_id not in graph:
            return []

        # BFS 找连通分量
        visited = set()
        queue = [target_id]
        visited.add(target_id)
        while queue:
            node = queue.pop(0)
            for neighbor in graph.get(node, []):
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append(neighbor)

        visited.discard(target_id)
        return list(visited)

    def _is_blacklisted(self, group_id: str | None, user_id: str) -> bool:
        """检查用户是否在黑名单中（全局 / 群 / 好友）"""
        if str(user_id) in self.global_blacklist:
            return True
        if group_id and str(user_id) in self.group_blacklist.get(str(group_id), []):
            return True
        if not group_id and str(user_id) in self.friend_blacklist:
            return True
        return False

    # ============================================================
    #  自助申请管理权限（enable_public_commands）
    # ============================================================

    def _load_public_applicants(self) -> list:
        """加载持久化的自助申请白名单"""
        try:
            if os.path.exists(self.APPLICANTS_FILE):
                with open(self.APPLICANTS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        return [str(item) for item in data]
        except Exception as e:
            logger.warning(f"加载自助申请白名单失败: {e}")
        return []

    def _save_public_applicants(self):
        """保存自助申请白名单"""
        try:
            with open(self.APPLICANTS_FILE, "w", encoding="utf-8") as f:
                json.dump(self._public_applicants, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存自助申请白名单失败: {e}")

    def _add_public_applicant(self, user_id: str) -> bool:
        """添加自助申请用户，返回 True 表示新增"""
        if user_id in self._public_applicants:
            return False
        self._public_applicants.append(user_id)
        self._save_public_applicants()
        return True

    def _is_public_applicant(self, user_id: str) -> bool:
        """检查用户是否在自助申请白名单中"""
        return str(user_id) in self._public_applicants

    # ============================================================
    #  群管理员检查
    # ============================================================

    async def _get_member_display(self, event: AstrMessageEvent, group_id: str, target_id: str) -> str:
        """获取成员显示名，格式：[CQ:at,qq=xxx]（xxx）；若成员不在群中则只返回 QQ 号"""
        bot = getattr(event, "bot", None)
        if bot:
            try:
                await bot.api.call_action("get_group_member_info", group_id=int(group_id), user_id=int(target_id))
                return f"[CQ:at,qq={target_id}]（{target_id}）"
            except Exception:
                pass
        return f"{target_id}"

    async def _is_group_admin(self, event: AstrMessageEvent, group_id: str, user_id: str) -> bool:
        """检查用户是否为群管理员/群主"""
        try:
            info = await self._call_api(event, "get_group_member_info", group_id=int(group_id), user_id=int(user_id))
            if isinstance(info, dict):
                data = info.get("data", info)
                role = data.get("role", "")
                if role in ("owner", "admin"):
                    return True
        except Exception:
            pass
        return False

    async def _is_group_owner(self, event: AstrMessageEvent, group_id: str, user_id: str) -> bool:
        """检查用户是否为群主"""
        try:
            info = await self._call_api(event, "get_group_member_info", group_id=int(group_id), user_id=int(user_id))
            if isinstance(info, dict):
                data = info.get("data", info)
                return data.get("role", "") == "owner"
        except Exception:
            pass
        return False

    async def _can_manage(self, event: AstrMessageEvent, group_id: str, user_id: str) -> bool:
        """检查用户是否有管理权限（群管理员/群主、blacklist_admin 或自助申请白名单用户）"""
        if str(user_id) in self.blacklist_admin:
            return True
        if self._is_public_applicant(user_id):
            return True
        return await self._is_group_admin(event, group_id, user_id)

    async def _can_blacklist(self, event: AstrMessageEvent, group_id: str, user_id: str, target_id: str) -> bool:
        """检查是否有权限拉黑目标：群主可拉黑所有人，管理员只能拉黑普通成员"""
        # 直接查目标角色，避免调用 _is_group_admin 触发 get_group_member_info 错误日志
        bot = getattr(event, "bot", None)
        target_role = ""
        if bot:
            try:
                info = await bot.api.call_action("get_group_member_info", group_id=int(group_id), user_id=int(target_id))
                if isinstance(info, dict):
                    data = info.get("data", info)
                    target_role = data.get("role", "")
            except Exception:
                pass  # 目标不在群中，视为普通成员权限
        # 目标如果是群主，任何人都不能拉黑
        if target_role == "owner":
            return False
        # 目标是管理员（非群主），只有群主能拉黑
        if target_role == "admin":
            return await self._is_group_owner(event, group_id, user_id)
        # 目标是普通成员或不在群中，有管理权限即可
        return await self._can_manage(event, group_id, user_id)

    # ============================================================
    #  群管理命令
    # ============================================================

    async def _handle_admin_command(self, event: AstrMessageEvent, group_id: str, user_id: str, msg_text: str, raw_message) -> bool:
        """处理群内管理命令：拉黑/全局拉黑/解黑/全局解黑/踢出/禁言/解禁/黑名单列表。返回 True 表示已处理"""
        if not self.enable_admin_commands:
            return False
        # 获取机器人自身 QQ 号
        raw = event.message_obj.raw_message
        self_id = str(self._get_raw_field(raw, "self_id", ""))

        text = msg_text.strip()
        # 剥离开头残留的纯文本 @昵称 前缀（兼容部分客户端将 at 转为纯文本的情况）
        # 1) 优先按 at 段中的昵称精确剥离（支持无空格的情况，如 "@机器人拉黑123456"）
        at_names = []
        if isinstance(raw_message, list):
            for seg in raw_message:
                if isinstance(seg, dict) and seg.get("type") == "at":
                    name = seg.get("data", {}).get("name", "")
                    if name:
                        at_names.append(name)
        for name in sorted(at_names, key=len, reverse=True):
            for prefix in ("@" + name, name):
                if text.startswith(prefix):
                    text = text[len(prefix):].lstrip()
                    break
        # 2) 兜底剥离无对应 at 段的 "@非空白+空白" 前缀（如纯文本 "@xx 拉黑..."）
        text = re.sub(r'^(?:@\S+\s+)+', '', text) or text
        # 3) 剥离常见的请求前缀（帮我、请、麻烦等），使 "帮我拉黑123456" 能匹配拉黑命令
        #    使用正向预查确保前缀后紧跟命令词才剥离，避免误伤昵称恰好为"帮我"的用户
        _cmd_words = r'(?:全局拉黑|全局解黑|黑名单列表|申请管理权限|申请权限|拉黑|解黑|踢出|禁言|解禁)'
        text = re.sub(r'^(?:(?:帮我|帮忙|帮助|帮|请|麻烦|给我|来|能不能|可以|我要|我想)\s*)+(?=' + _cmd_words + r')', '', text) or text

        # 权限不足提示（开启自助申请时附上提示）
        if self.enable_public_commands:
            _perm_denied_msg = "你没有权限执行此操作，发送「申请管理权限」获取权限"
        else:
            _perm_denied_msg = "只有群管理员/群主才能执行此操作"

        # 从原始消息段中提取所有 @ 的 QQ 号（排除机器人自身，避免把 @机器人 当成命令目标）
        def extract_at_qids(raw_msg):
            qids = []
            if isinstance(raw_msg, list):
                for seg in raw_msg:
                    if isinstance(seg, dict) and seg.get("type") == "at":
                        qq = seg.get("data", {}).get("qq", "")
                        if qq and str(qq) != self_id:
                            qids.append(str(qq))
            return qids

        # 尝试提取命令中的 QQ 号：先看 @，再看纯文本数字
        def extract_target(text, raw_msg):
            at_qids = extract_at_qids(raw_msg)
            if at_qids:
                return at_qids[0]
            # 纯文本中找数字
            m = re.search(r"(\d{5,})", text)
            if m:
                return m.group(1)
            return None

        target = None

        # ======== 申请管理权限（自助申请白名单） ========
        if self.enable_public_commands and re.match(r"^(申请管理权限|申请权限)(?:\s|$)", text, re.IGNORECASE):
            if await self._is_group_admin(event, group_id, user_id):
                await self._call_api(event, "send_group_msg", group_id=int(group_id), message="你已经是群管理员，无需申请")
                return True
            if str(user_id) in self.blacklist_admin:
                await self._call_api(event, "send_group_msg", group_id=int(group_id), message="你已在管理白名单中，无需重复申请")
                return True
            if self._add_public_applicant(user_id):
                logger.info(f"[自助申请] {user_id} 申请并获得了管理命令权限 (群 {group_id})")
                await self._call_api(event, "send_group_msg", group_id=int(group_id), message="申请成功，你现在可以使用群管理命令了")
            else:
                await self._call_api(event, "send_group_msg", group_id=int(group_id), message="你已申请过，无需重复申请")
            return True

        # ======== 拉黑 ========
        if re.match(r"^(拉黑)(?:\s|$|\d)", text, re.IGNORECASE):
            target = extract_target(text, raw_message)
            if not target:
                await self._call_api(event, "send_group_msg", group_id=int(group_id), message="格式：拉黑 QQ号")
                return True
            if target == user_id:
                await self._call_api(event, "send_group_msg", group_id=int(group_id), message="不好意思，不能拉黑自己哦")
                return True
            if target == self_id:
                await self._call_api(event, "send_group_msg", group_id=int(group_id), message="抱歉，我不能拉黑自己")
                return True
            if not await self._can_manage(event, group_id, user_id):
                await self._call_api(event, "send_group_msg", group_id=int(group_id), message=_perm_denied_msg)
                return True
            if not await self._can_blacklist(event, group_id, user_id, target):
                if await self._is_group_owner(event, group_id, target):
                    await self._call_api(event, "send_group_msg", group_id=int(group_id), message=f"不能拉黑群主哦")
                else:
                    await self._call_api(event, "send_group_msg", group_id=int(group_id), message=f"只有群主才能拉黑管理员")
                return True
            if self._is_blacklisted(group_id, target):
                display = await self._get_member_display(event, group_id, target)
                await self._call_api(event, "send_group_msg", group_id=int(group_id), message=f"{display} 已在当前群黑名单中")
                return True
            display = await self._get_member_display(event, group_id, target)
            # 收集需要连带拉黑的目标
            chain_targets = []
            if self.enable_chain_blacklist:
                chain_targets = self._get_chain_targets(group_id, target)
            all_targets = [target] + chain_targets
            # 拉黑所有目标
            for t in all_targets:
                if self._is_blacklisted(group_id, t):
                    continue
                self.group_blacklist.setdefault(str(group_id), []).append(t)
                d = await self._get_member_display(event, group_id, t)
                if self.enable_auto_kick:
                    await self._call_api(event, "set_group_kick", group_id=int(group_id), user_id=int(t), reject_add_request=True)
                else:
                    await self._call_api(event, "set_group_ban", group_id=int(group_id), user_id=int(t), duration=self.blacklist_mute_duration)
                logger.info(f"[群管理命令] {user_id} 将 {t} 加入黑名单 (群 {group_id})")
            self._save_blacklist()
            # 回复消息
            action_name = '踢出' if self.enable_auto_kick else '禁言'
            if len(all_targets) > 1:
                await self._call_api(event, "send_group_msg", group_id=int(group_id), message=f"已将 {display} 及其邀请链上的 {len(all_targets)-1} 人加入黑名单并{action_name}")
            else:
                await self._call_api(event, "send_group_msg", group_id=int(group_id), message=f"已将 {display} 加入黑名单并{action_name}")
            return True

        # ======== 全局拉黑 ========
        if re.match(r"^(全局拉黑)(?:\s|$|\d)", text, re.IGNORECASE):
            target = extract_target(text, raw_message)
            if not target:
                await self._call_api(event, "send_group_msg", group_id=int(group_id), message="格式：全局拉黑 QQ号")
                return True
            if target == user_id:
                await self._call_api(event, "send_group_msg", group_id=int(group_id), message="不好意思，不能拉黑自己哦")
                return True
            if target == self_id:
                await self._call_api(event, "send_group_msg", group_id=int(group_id), message="抱歉，我不能拉黑自己")
                return True
            if not await self._can_manage(event, group_id, user_id):
                await self._call_api(event, "send_group_msg", group_id=int(group_id), message=_perm_denied_msg)
                return True
            if not await self._can_blacklist(event, group_id, user_id, target):
                if await self._is_group_owner(event, group_id, target):
                    await self._call_api(event, "send_group_msg", group_id=int(group_id), message=f"不能拉黑群主哦")
                else:
                    await self._call_api(event, "send_group_msg", group_id=int(group_id), message=f"只有群主才能拉黑管理员")
                return True
            if target in self.global_blacklist:
                display = await self._get_member_display(event, group_id, target)
                await self._call_api(event, "send_group_msg", group_id=int(group_id), message=f"{display} 已在全局黑名单中")
                return True
            display = await self._get_member_display(event, group_id, target)
            # 收集需要连带拉黑的目标
            chain_targets = []
            if self.enable_chain_blacklist:
                chain_targets = self._get_chain_targets(group_id, target)
            all_targets = [target] + chain_targets
            # 加入全局黑名单
            for t in all_targets:
                if t not in self.global_blacklist:
                    self.global_blacklist.append(t)
                    logger.info(f"[群管理命令] {user_id} 将 {t} 加入全局黑名单 (群 {group_id})")
            self._save_blacklist()
            # 作用于所有 whitelist_group 中的群
            total_groups = len(self.whitelist_group)
            acted_groups = []
            for gid in self.whitelist_group:
                for t in all_targets:
                    try:
                        if self.enable_auto_kick:
                            await self._call_api(event, "set_group_kick", group_id=int(gid), user_id=int(t), reject_add_request=True)
                        else:
                            await self._call_api(event, "set_group_ban", group_id=int(gid), user_id=int(t), duration=self.blacklist_mute_duration)
                    except Exception:
                        pass
                acted_groups.append(gid)
            action_name = '踢出' if self.enable_auto_kick else '禁言'
            if len(all_targets) > 1:
                msg = f"已将 {display} 及其邀请链上的 {len(all_targets)-1} 人加入全局黑名单"
            else:
                msg = f"已将 {display} 加入全局黑名单"
            if acted_groups:
                msg += f" 并在 {len(acted_groups)}/{total_groups} 个群中{action_name}成功"
            else:
                msg += f"（{action_name}失败，目标可能不在管理的群中）"
            await self._call_api(event, "send_group_msg", group_id=int(group_id), message=msg)
            return True

        # ======== 解黑（从当前群黑名单移除） ========
        if re.match(r"^(解黑)(?:\s|$|\d)", text, re.IGNORECASE):
            target = extract_target(text, raw_message)
            if not target:
                await self._call_api(event, "send_group_msg", group_id=int(group_id), message="格式：解黑 QQ号")
                return True
            if not await self._can_manage(event, group_id, user_id):
                await self._call_api(event, "send_group_msg", group_id=int(group_id), message=_perm_denied_msg)
                return True
            group_list = self.group_blacklist.get(str(group_id), [])
            global_has = target in self.global_blacklist
            group_has = target in group_list
            if not global_has and not group_has:
                display = await self._get_member_display(event, group_id, target)
                await self._call_api(event, "send_group_msg", group_id=int(group_id), message=f"{display} 不在当前群黑名单中")
                return True
            display = await self._get_member_display(event, group_id, target)
            # 从群黑名单移除
            if group_has:
                self.group_blacklist[str(group_id)] = [x for x in group_list if x != target]
            self._save_blacklist()
            logger.info(f"[群管理命令] {user_id} 将 {target} 移出群黑名单 (群 {group_id})")
            await self._call_api(event, "set_group_ban", group_id=int(group_id), user_id=int(target), duration=0)
            await self._call_api(event, "send_group_msg", group_id=int(group_id), message=f"已将 {display} 移出当前群黑名单")
            return True

        # ======== 全局解黑 ========
        if re.match(r"^(全局解黑)(?:\s|$|\d)", text, re.IGNORECASE):
            target = extract_target(text, raw_message)
            if not target:
                await self._call_api(event, "send_group_msg", group_id=int(group_id), message="格式：全局解黑 QQ号")
                return True
            if not await self._can_manage(event, group_id, user_id):
                await self._call_api(event, "send_group_msg", group_id=int(group_id), message=_perm_denied_msg)
                return True
            if target not in self.global_blacklist:
                display = await self._get_member_display(event, group_id, target)
                await self._call_api(event, "send_group_msg", group_id=int(group_id), message=f"{display} 不在全局黑名单中")
                return True
            display = await self._get_member_display(event, group_id, target)
            self.global_blacklist = [x for x in self.global_blacklist if x != target]
            self._save_blacklist()
            logger.info(f"[群管理命令] {user_id} 将 {target} 移出全局黑名单 (群 {group_id})")
            # 作用于所有 whitelist_group 中的群
            for gid in self.whitelist_group:
                try:
                    await self._call_api(event, "set_group_ban", group_id=int(gid), user_id=int(target), duration=0)
                except Exception:
                    pass
            await self._call_api(event, "send_group_msg", group_id=int(group_id), message=f"已将 {display} 移出全局黑名单")
            return True

        # ======== 黑名单列表 ========
        if re.match(r"^(黑名单列表)(?:\s|$)", text, re.IGNORECASE):
            if not await self._can_manage(event, group_id, user_id):
                await self._call_api(event, "send_group_msg", group_id=int(group_id), message=_perm_denied_msg)
                return True
            if not self.global_blacklist and not self.group_blacklist.get(str(group_id), []):
                await self._call_api(event, "send_group_msg", group_id=int(group_id), message="黑名单为空")
                return True
            parts = []
            group_list = self.group_blacklist.get(str(group_id), [])
            if group_list:
                parts.append(f"▎本群黑名单：\n" + "\n".join(group_list))
            if self.global_blacklist:
                parts.append(f"▎全局黑名单：\n" + "\n".join(self.global_blacklist))
            await self._call_api(event, "send_group_msg", group_id=int(group_id), message="\n\n".join(parts))
            return True

        # ======== 踢出 ========
        if re.match(r"^(踢出)(?:\s|$|\d)", text, re.IGNORECASE):
            target = extract_target(text, raw_message)
            if not target:
                await self._call_api(event, "send_group_msg", group_id=int(group_id), message="格式：踢出 QQ号")
                return True
            if target == user_id:
                await self._call_api(event, "send_group_msg", group_id=int(group_id), message="不能踢出自己")
                return True
            if not await self._can_manage(event, group_id, user_id):
                await self._call_api(event, "send_group_msg", group_id=int(group_id), message=_perm_denied_msg)
                return True
            display = await self._get_member_display(event, group_id, target)
            await self._call_api(event, "set_group_kick", group_id=int(group_id), user_id=int(target))
            logger.info(f"[群管理命令] {user_id} 踢出 {target} (群 {group_id})")
            await self._call_api(event, "send_group_msg", group_id=int(group_id), message=f"已踢出 {display}")
            return True

        # ======== 禁言 ========
        if re.match(r"^(禁言)(?:\s|$|\d)", text, re.IGNORECASE):
            target = extract_target(text, raw_message)
            if not target:
                await self._call_api(event, "send_group_msg", group_id=int(group_id), message="格式：禁言 @用户 秒数 或 禁言 QQ号 秒数")
                return True
            if target == user_id:
                await self._call_api(event, "send_group_msg", group_id=int(group_id), message="不能禁言自己")
                return True
            if not await self._can_manage(event, group_id, user_id):
                await self._call_api(event, "send_group_msg", group_id=int(group_id), message=_perm_denied_msg)
                return True
            # 提取禁言时长
            rest = re.sub(r"^(禁言)\s*", "", text, flags=re.IGNORECASE)
            rest = rest.replace(str(target), "").strip()
            duration_match = re.search(r"(\d+)", rest)
            duration = int(duration_match.group(1)) if duration_match else self.mute_duration
            display = await self._get_member_display(event, group_id, target)
            await self._call_api(event, "set_group_ban", group_id=int(group_id), user_id=int(target), duration=duration)
            logger.info(f"[群管理命令] {user_id} 禁言 {target} {duration}秒 (群 {group_id})")
            if duration > 0:
                await self._call_api(event, "send_group_msg", group_id=int(group_id), message=f"已将 {display} 禁言{self._format_duration(duration)}")
            else:
                await self._call_api(event, "send_group_msg", group_id=int(group_id), message=f"已解除 {display} 的禁言")
            return True

        # ======== 解禁 ========
        if re.match(r"^(解禁)(?:\s|$|\d)", text, re.IGNORECASE):
            target = extract_target(text, raw_message)
            if not target:
                await self._call_api(event, "send_group_msg", group_id=int(group_id), message="格式：解禁 @用户 或 解禁 QQ号")
                return True
            if not await self._can_manage(event, group_id, user_id):
                await self._call_api(event, "send_group_msg", group_id=int(group_id), message=_perm_denied_msg)
                return True
            display = await self._get_member_display(event, group_id, target)
            await self._call_api(event, "set_group_ban", group_id=int(group_id), user_id=int(target), duration=0)
            logger.info(f"[群管理命令] {user_id} 解禁 {target} (群 {group_id})")
            await self._call_api(event, "send_group_msg", group_id=int(group_id), message=f"已解除 {display} 的禁言")
            return True

        return False

    # ============================================================
    #  进群欢迎
    # ============================================================

    async def _send_welcome_message(self, event: AstrMessageEvent, group_id: str, user_id: str, nickname: str):
        """发送进群欢迎消息"""
        try:
            if not self.welcome_text:
                return
            text = self.welcome_text.replace("{nickname}", nickname).replace("{user_id}", str(user_id)).replace("\\n", "\n")

            # 构造 CQ 码消息字符串
            message = f"[CQ:at,qq={user_id}] {text}"

            # 可选图片（支持多张）
            for url in self.welcome_image_urls:
                if url.startswith("http://") or url.startswith("https://"):
                    message += f"\n[CQ:image,url={url}]"
                else:
                    local_path = os.path.join(os.path.dirname(__file__), url)
                    if os.path.exists(local_path):
                        local_path = os.path.abspath(local_path)
                        message += f"\n[CQ:image,file={local_path}]"
                    else:
                        logger.warning(f"[进群欢迎] 本地图片文件不存在：{local_path}")

            await self._call_api(
                event, "send_group_msg",
                group_id=int(group_id), message=message
            )
        except Exception as e:
            logger.error(f"发送进群欢迎消息失败：{e}")

    async def _send_leave_message(self, event: AstrMessageEvent, group_id: str, user_id: str, sub_type: str):
        """发送退群通知"""
        try:
            if not self.leave_text:
                return
            text = self.leave_text.replace("{user_id}", str(user_id)).replace("\\n", "\n")

            message = text

            # 可选图片（支持多张）
            for url in self.leave_image_urls:
                if url.startswith("http://") or url.startswith("https://"):
                    message += f"\n[CQ:image,url={url}]"
                else:
                    local_path = os.path.join(os.path.dirname(__file__), url)
                    if os.path.exists(local_path):
                        local_path = os.path.abspath(local_path)
                        message += f"\n[CQ:image,file={local_path}]"
                    else:
                        logger.warning(f"[退群通知] 本地图片文件不存在：{local_path}")

            await self._call_api(
                event, "send_group_msg",
                group_id=int(group_id), message=message
            )
        except Exception as e:
            logger.error(f"发送退群通知失败：{e}")

    async def _call_api(self, event: AstrMessageEvent, action: str, **params) -> dict | None:
        """调用 OneBot API（兼容 message / notice / request 各类事件）"""
        try:
            bot = getattr(event, "bot", None)
            if bot is None:
                logger.warning(f"事件对象没有 bot 属性，无法调用 API：{action}")
                return None
            if self._cached_bot is None:
                self._cached_bot = bot
            return await bot.api.call_action(action, **params)
        except Exception as e:
            logger.error(f"调用 OneBot API 失败：{action}, err={e}")
            return None

    async def _handle_poke_reply(self, event: AstrMessageEvent, group_id: str, user_id: str):
        """被戳后：随机选一条回复 + 50% 概率戳回去"""
        try:
            replies = self.poke_back_replies if random.random() < 0.5 else self.poke_noreply_replies
            text = random.choice(replies) if replies else ""
            need_poke_back = replies is self.poke_back_replies

            await self._call_api(
                event, "send_group_msg",
                group_id=int(group_id), message=text
            )

            if need_poke_back:
                await asyncio.sleep(1)
                await self._call_api(
                    event, "group_poke",
                    group_id=int(group_id), user_id=int(user_id)
                )
        except Exception as e:
            logger.error(f"戳一戳回复失败：{e}")

    async def _check_and_mute(self, event: AstrMessageEvent, group_id: str, user_id: str, msg_text: str):
        """检查消息是否违规并执行禁言（支持群配置覆盖）"""
        gcfg = self._get_group_overrides(group_id) if group_id else {}

        def go(key, default):
            """获取群覆盖值或默认值"""
            return gcfg.get(key, default)

        try:
            # 黑名单检查，黑名单用户发消息自动处理
            if self._is_blacklisted(str(group_id), str(user_id)):
                logger.info(f"[黑名单] 群 {group_id} 黑名单用户 {user_id} 发送消息")
                # 群主/管理员不受禁言/踢出影响，跳过所有操作
                if await self._is_group_admin(event, group_id, str(user_id)):
                    logger.info(f"[黑名单] 用户 {user_id} 是群管理员/群主，跳过操作")
                    return True
                actual_kick = go("enable_auto_kick", self.enable_auto_kick)
                actual_bl_duration = go("blacklist_mute_duration", self.blacklist_mute_duration)
                actual_bl_reply = go("blacklist_mute_reply", self.blacklist_mute_reply)
                actual_recall = go("mute_recall", self.mute_recall)
                if actual_kick:
                    await self._call_api(
                        event, "set_group_kick",
                        group_id=int(group_id), user_id=int(user_id), reject_add_request=True
                    )
                else:
                    await self._call_api(
                        event, "set_group_ban",
                        group_id=int(group_id), user_id=int(user_id), duration=actual_bl_duration
                    )
                if actual_recall:
                    raw = event.message_obj.raw_message
                    message_id = self._get_raw_field(raw, "message_id")
                    if message_id:
                        await self._call_api(event, "delete_msg", message_id=int(message_id))
                if actual_bl_reply:
                    reply = actual_bl_reply.replace("{user_id}", str(user_id)).replace("{mute_duration}", self._format_duration(actual_bl_duration))
                    await self._call_api(event, "send_group_msg", group_id=int(group_id), message=reply)
                return True

            actual_auto_mute = go("enable_auto_mute", self.enable_auto_mute)
            if not actual_auto_mute:
                return False

            # 禁言白名单检查（支持群覆盖）
            actual_whitelist = go("mute_whitelist", self.mute_whitelist)
            if str(user_id) in actual_whitelist:
                return False

            should_mute = False
            reason = ""

            # 关键词正则匹配（支持群覆盖）
            actual_keywords = go("mute_keywords", self.mute_keywords)
            if actual_keywords:
                for pattern in actual_keywords:
                    try:
                        if re.search(pattern, msg_text):
                            should_mute = True
                            reason = f"关键词匹配: {pattern}"
                            break
                    except re.error as e:
                        logger.warning(f"禁言正则表达式错误 [{pattern}]: {e}")

            # AI 审核（关键词未命中时走 AI，支持群覆盖）
            actual_ai_review = go("mute_ai_review", self.mute_ai_review)
            actual_ai_prompt = go("mute_ai_prompt", self.mute_ai_prompt)
            if not should_mute and actual_ai_review:
                try:
                    llm = self._get_llm()
                    if llm:
                        full_prompt = f"{actual_ai_prompt}\n\n{msg_text}"
                        resp = await llm.text_chat(prompt=full_prompt, session_id=f"mute_{group_id}")
                        result_text = self._extract_llm_text(resp)
                        if result_text.strip().lower().startswith("yes"):
                            should_mute = True
                            reason = "AI审核判定违规"
                except Exception as e:
                    logger.warning(f"AI审核调用失败: {e}")

            # 执行禁言（支持群覆盖时长/撤回/回复）
            if should_mute:
                actual_duration = go("mute_duration", self.mute_duration)
                actual_recall = go("mute_recall", self.mute_recall)
                actual_reply = go("mute_reply", self.mute_reply)
                logger.info(f"[自动禁言] 群 {group_id} 用户 {user_id} {reason}，禁言 {actual_duration} 秒")
                await self._mute_user(event, group_id, user_id,
                                      duration=actual_duration,
                                      recall=actual_recall,
                                      reply=actual_reply)
                return True

            return False
        except Exception as e:
            logger.error(f"自动禁言处理失败: {e}")
            return False

    async def _mute_user(self, event: AstrMessageEvent, group_id: str, user_id: str,
                         duration: int | None = None, recall: bool | None = None,
                         reply: str | None = None):
        """执行禁言操作（禁言 + 撤回 + 回复），支持覆盖默认配置"""
        mute_duration = duration if duration is not None else self.mute_duration
        mute_recall = recall if recall is not None else self.mute_recall
        mute_reply = reply if reply is not None else self.mute_reply
        try:
            await self._call_api(
                event, "set_group_ban",
                group_id=int(group_id), user_id=int(user_id), duration=mute_duration
            )

            # 撤回违规消息
            if mute_recall:
                raw = event.message_obj.raw_message
                message_id = self._get_raw_field(raw, "message_id")
                if message_id:
                    await self._call_api(event, "delete_msg", message_id=int(message_id))

            # 回复提示
            if mute_reply:
                reply_text = mute_reply.replace("{user_id}", str(user_id)).replace("{mute_duration}", self._format_duration(mute_duration))
                await self._call_api(
                    event, "send_group_msg",
                    group_id=int(group_id),
                    message=reply_text
                )
        except Exception as e:
            logger.error(f"执行禁言操作失败: {e}")

    def _get_llm(self):
        """获取当前使用的 LLM 提供商"""
        try:
            # 新版 AstrBot
            return self.context.get_llm()
        except AttributeError:
            pass
        try:
            pm = self.context.provider_manager
            # 方式1: get_using_provider("llm")
            try:
                return pm.get_using_provider("llm")
            except Exception:
                pass
            # 方式2: 直接取当前实例
            if hasattr(pm, 'curr_provider_inst') and pm.curr_provider_inst is not None:
                return pm.curr_provider_inst
            # 方式3: 从 provider_insts 中取第一个
            if hasattr(pm, 'provider_insts') and pm.provider_insts:
                insts = pm.provider_insts
                if isinstance(insts, dict):
                    for v in insts.values():
                        return v
                elif isinstance(insts, list):
                    return insts[0]
        except Exception:
            pass
        logger.warning("未找到可用的 AI 提供商，AI 审核不可用")
        return None

    @staticmethod
    def _extract_llm_text(resp) -> str:
        """从 LLM 响应中提取文本内容（兼容 LLMResponse / openai SDK / 字符串等）"""
        if isinstance(resp, str):
            return resp
        # AstrBot LLMResponse: resp.result_chain.chain[0].text
        if hasattr(resp, 'result_chain'):
            chain = resp.result_chain
            if hasattr(chain, 'chain') and chain.chain:
                for comp in chain.chain:
                    if hasattr(comp, 'text') and comp.text:
                        return comp.text
            if isinstance(chain, str):
                return chain
        # ChatCompletion / ChatCompletionResponse 对象
        if hasattr(resp, 'choices') and resp.choices:
            choice = resp.choices[0]
            if hasattr(choice, 'message') and choice.message:
                msg = choice.message
                if hasattr(msg, 'content') and msg.content:
                    return msg.content
                if isinstance(msg, dict):
                    return msg.get('content', '')
            if isinstance(choice, dict):
                msg = choice.get('message', {})
                if isinstance(msg, dict):
                    return msg.get('content', '')
        # 常见属性兜底
        for attr in ('completion', 'text', 'content', 'response'):
            val = getattr(resp, attr, None)
            if val and isinstance(val, str):
                return val
        return str(resp)

    @staticmethod
    def _format_duration(seconds: int) -> str:
        """将秒数转换为可读的天时分秒"""
        days, remainder = divmod(seconds, 86400)
        hours, remainder = divmod(remainder, 3600)
        minutes, secs = divmod(remainder, 60)
        parts = []
        if days > 0:
            parts.append(f"{days}天")
        if hours > 0:
            parts.append(f"{hours}小时")
        if minutes > 0:
            parts.append(f"{minutes}分")
        if secs > 0 or not parts:
            parts.append(f"{secs}秒")
        return "".join(parts)

    @staticmethod
    def _extract_plain_text(message) -> str:
        """从 OneBot 消息中提取纯文本（兼容字符串和消息段列表）"""
        if isinstance(message, str):
            return message
        if isinstance(message, list):
            parts = []
            for seg in message:
                if isinstance(seg, dict) and seg.get("type") == "text":
                    text = seg.get("data", {}).get("text", "")
                    if text:
                        parts.append(text)
            return " ".join(parts)
        return str(message) if message else ""

    @on_llm_response()
    async def _filter_llm_response(self, event: AstrMessageEvent, response: LLMResponse) -> None:
        """过滤 LLM 回复内容，替换自定义关键词/正则"""
        if not self.llm_filter_rules or not response.completion_text:
            return

        text = response.completion_text
        for rule in self.llm_filter_rules:
            if "=>" not in rule:
                continue
            pattern, replacement = rule.split("=>", 1)
            pattern = pattern.strip()
            if not pattern:
                continue
            try:
                text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
            except re.error as e:
                logger.warning(f"LLM过滤正则表达式错误 [{pattern}]: {e}")

        response.completion_text = text

    @staticmethod
    def _get_raw_field(obj, key, default=None):
        """从 raw_message 中安全取值，兼容 dict 和 Event 对象"""
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_message(self, event: AstrMessageEvent):
        """监听所有事件：戳一戳、进群通知、好友/群请求等"""
        try:
            raw = event.message_obj.raw_message

            post_type = self._get_raw_field(raw, "post_type")

            # ============================================================
            #  处理好友/群聊请求（request 类型事件）
            # ============================================================
            if post_type == "request":
                request_type = self._get_raw_field(raw, "request_type")
                flag = self._get_raw_field(raw, "flag")

                # 自动通过好友申请
                if request_type == "friend" and self.enable_friend_request:
                    user_id = self._get_raw_field(raw, "user_id")
                    logger.info(f"自动通过好友申请：{user_id}")
                    await self._call_api(event, "set_friend_add_request", flag=flag, approve=True)
                    return

                # 处理群聊请求（邀请机器人 / 加群申请）
                if request_type == "group":
                    sub_type = self._get_raw_field(raw, "sub_type")
                    group_id = self._get_raw_field(raw, "group_id")
                    user_id = self._get_raw_field(raw, "user_id")

                    # 黑名单用户申请加群 → 拒绝
                    if sub_type == "add" and self._is_blacklisted(str(group_id), str(user_id)):
                        logger.info(f"黑名单用户 {user_id} 申请加入群 {group_id}，自动拒绝")
                        await self._call_api(
                            event, "set_group_add_request",
                            flag=flag, sub_type="add", approve=False
                        )
                        return

                    if sub_type == "invite" and self.enable_group_request:
                        logger.info(f"自动接受群聊邀请：用户 {user_id} 邀请机器人加入群 {group_id}")
                        await self._call_api(
                            event, "set_group_add_request",
                            flag=flag, sub_type="invite", approve=True
                        )
                    elif sub_type == "add" and self.auto_approve_group_join:
                        logger.info(f"自动通过加群申请：用户 {user_id} 申请加入群 {group_id}")
                        await self._call_api(
                            event, "set_group_add_request",
                            flag=flag, sub_type="add", approve=True
                        )
                    else:
                        logger.info(f"收到群聊请求（{sub_type}），跳过自动处理：用户 {user_id} -> 群 {group_id}")
                    return

                return

            # ============================================================
            #  处理通知事件（戳一戳、进群等）
            # ============================================================
            if post_type == "notice":
                notice_type = self._get_raw_field(raw, "notice_type")
                sub_type = self._get_raw_field(raw, "sub_type")

                # 群白名单检查（notice 事件都属于群相关）
                group_id = self._get_raw_field(raw, "group_id")
                if group_id and not self._is_group_allowed(str(group_id)):
                    logger.info(f"群 {group_id} 不在白名单中，跳过处理")
                    event.stop_event()
                    return

                # 处理戳一戳事件
                if notice_type == "notify" and sub_type == "poke":
                    if not self.poke_enabled:
                        return

                    user_id = self._get_raw_field(raw, "user_id")
                    target_id = self._get_raw_field(raw, "target_id")
                    self_id = self._get_raw_field(raw, "self_id")

                    if not (group_id and user_id and target_id):
                        return

                    if str(target_id) == str(self_id):
                        asyncio.create_task(self._handle_poke_reply(event, str(group_id), str(user_id)))
                    return

                # 处理进群通知
                if notice_type == "group_increase":
                    user_id = self._get_raw_field(raw, "user_id")
                    self_id = self._get_raw_field(raw, "self_id")

                    if not (group_id and user_id):
                        return

                    # bot 自己加群时不发送欢迎
                    if str(user_id) == str(self_id):
                        logger.info(f"[进群欢迎] bot 自身加入群 {group_id}，跳过欢迎")
                        return

                    # 记录邀请关系（连带拉黑用），仅当 sub_type 为 "invite" 且有 operator_id 时
                    if sub_type == "invite":
                        operator_id = self._get_raw_field(raw, "operator_id", "")
                        if operator_id and str(operator_id) != str(user_id):
                            self._record_invitation(str(group_id), str(operator_id), str(user_id))

                    # 先尝试从原始事件获取昵称
                    nickname = self._get_raw_field(raw, "nickname", "")
                    if not nickname:
                        try:
                            member_info = await self._call_api(
                                event, "get_group_member_info",
                                group_id=int(group_id), user_id=int(user_id)
                            )
                            if isinstance(member_info, dict):
                                data = member_info.get("data", member_info)
                            else:
                                data = {}
                            card = data.get("card", "")
                            if card and card.strip():
                                nickname = card
                            else:
                                nickname = data.get("nickname", str(user_id))
                        except Exception as e:
                            logger.warning(f"获取成员信息失败，使用用户ID作为昵称：{e}")
                            nickname = str(user_id)

                    if not self.welcome_text:
                        logger.info(f"[进群欢迎] 欢迎语为空，跳过发送。群 {group_id} 新成员：{nickname} ({user_id})")
                        return

                    logger.info(f"[进群欢迎] 群 {group_id} 新成员加入：{nickname} ({user_id})")
                    asyncio.create_task(self._send_welcome_message(event, str(group_id), str(user_id), nickname))
                    return

                # 处理退群通知
                if notice_type == "group_decrease":
                    user_id = self._get_raw_field(raw, "user_id")
                    if not (group_id and user_id):
                        return

                    if not self.leave_text:
                        return

                    logger.info(f"[退群通知] 群 {group_id} 成员离开：{user_id}, sub_type={sub_type}")
                    asyncio.create_task(self._send_leave_message(event, str(group_id), str(user_id), sub_type))
                    return

            # ============================================================
            #  处理群消息事件（自动禁言）
            # ============================================================
            if post_type == "message":
                message_type = self._get_raw_field(raw, "message_type")

                # 私聊消息白名单检查
                if message_type == "private":
                    user_id = self._get_raw_field(raw, "user_id")
                    if not self._is_user_allowed(str(user_id)):
                        logger.info(f"私聊用户 {user_id} 不在白名单中，忽略消息")
                        event.stop_event()
                        return
                    return

                if message_type != "group":
                    return

                group_id = self._get_raw_field(raw, "group_id")
                user_id = self._get_raw_field(raw, "user_id")
                if not (group_id and user_id):
                    return

                # 群白名单检查
                if not self._is_group_allowed(str(group_id)):
                    event.stop_event()
                    return

                raw_message = self._get_raw_field(raw, "message", "")
                msg_text = self._extract_plain_text(raw_message)
                if not msg_text:
                    return

                # 先检查是否是群管理命令（拉黑/解黑等），处理后阻止事件继续传播给 AI
                if await self._handle_admin_command(event, str(group_id), str(user_id), msg_text, raw_message):
                    event.stop_event()
                    return

                await self._check_and_mute(event, str(group_id), str(user_id), msg_text)
                return

        except Exception as e:
            logger.error(f"处理事件失败：{e}")

    # ============================================================
    #  Scoped 配置管理（好友/群单独覆盖）
    # ============================================================

    def _load_scoped_config(self) -> dict:
        """从本地文件加载作用域配置"""
        try:
            if os.path.exists(self.scoped_config_file):
                with open(self.scoped_config_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        return {
                            "default": data.get("default", {}),
                            "friends": {str(k): v for k, v in data.get("friends", {}).items()},
                            "groups": {str(k): v for k, v in data.get("groups", {}).items()},
                            "tracked_groups": [str(x) for x in data.get("tracked_groups", [])],
                        }
        except Exception as e:
            logger.warning(f"加载作用域配置失败: {e}")
        return {"default": {}, "friends": {}, "groups": {}, "tracked_groups": []}

    def _save_scoped_config(self):
        """保存作用域配置到本地文件"""
        try:
            with open(self.scoped_config_file, "w", encoding="utf-8") as f:
                json.dump(self.scoped_config, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存作用域配置失败: {e}")

    def _apply_default_overrides(self):
        """将默认作用域覆盖应用到实例属性"""
        overrides = self.scoped_config.get("default", {})
        for key, value in overrides.items():
            if hasattr(self, key):
                try:
                    setattr(self, key, value)
                except Exception:
                    pass
        # 处理属性名映射
        if "welcome_image_url" in overrides:
            self.welcome_image_urls = overrides["welcome_image_url"]
        if "leave_image_url" in overrides:
            self.leave_image_urls = overrides["leave_image_url"]

    def _get_group_overrides(self, group_id: str | None) -> dict:
        """获取指定群聊的作用域覆盖"""
        if not group_id:
            return {}
        return self.scoped_config.get("groups", {}).get(str(group_id), {})

    async def _get_bot_client(self):
        """获取已连接的机器人客户端（用于管理页面主动调用 OneBot API）"""
        try:
            if self._cached_bot is not None:
                return self._cached_bot
            # 通过平台管理器获取
            for platform in self.context.platform_manager.platform_insts:
                client = getattr(platform, 'bot', None)
                if client is not None and hasattr(client, 'api'):
                    self._cached_bot = client
                    return client
        except Exception as e:
            logger.warning(f"获取机器人客户端失败: {e}")
        return None

    @staticmethod
    def _get_config_schema() -> dict:
        """读取 _conf_schema.json 配置定义"""
        try:
            schema_path = os.path.join(os.path.dirname(__file__), "_conf_schema.json")
            if os.path.exists(schema_path):
                with open(schema_path, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception as e:
            logger.warning(f"读取配置 schema 失败: {e}")
        return {}

    # ============================================================
    #  Web API 处理程序（管理页面）
    # ============================================================

    def _register_web_apis(self, context: Context):
        """注册所有 Web API 路由"""
        prefix = "astrbot_plugin_smart_group_manager"

        context.register_web_api(
            f"/{prefix}/config", self.api_get_config, ["GET"],
            "获取全部配置信息（默认 + 作用域 + schema）"
        )
        context.register_web_api(
            f"/{prefix}/config/default", self.api_save_default_config, ["POST"],
            "保存默认配置覆盖"
        )
        context.register_web_api(
            f"/{prefix}/config/group/<group_id>", self.api_save_group_config, ["POST"],
            "保存/更新群配置覆盖"
        )
        context.register_web_api(
            f"/{prefix}/config/group/<group_id>", self.api_delete_group_config, ["DELETE"],
            "删除群配置覆盖"
        )
        context.register_web_api(
            f"/{prefix}/config/group/<group_id>/delete", self.api_delete_group_config, ["POST"],
            "删除群配置覆盖（POST 版本，供管理页面使用）"
        )
        context.register_web_api(
            f"/{prefix}/config/group/<group_id>/reset_policy", self.api_reset_group_blacklist_policy, ["POST"],
            "重置群黑名单策略（仅清除黑名单策略覆盖，保留其他配置）"
        )
        context.register_web_api(
            f"/{prefix}/config/friend/<friend_id>", self.api_save_friend_config, ["POST"],
            "保存/更新好友配置覆盖"
        )
        context.register_web_api(
            f"/{prefix}/config/friend/<friend_id>", self.api_delete_friend_config, ["DELETE"],
            "删除好友配置覆盖"
        )
        context.register_web_api(
            f"/{prefix}/blacklist", self.api_get_blacklist, ["GET"],
            "获取黑名单数据"
        )
        context.register_web_api(
            f"/{prefix}/blacklist/add", self.api_add_blacklist, ["POST"],
            "添加黑名单"
        )
        context.register_web_api(
            f"/{prefix}/blacklist/remove", self.api_remove_blacklist, ["POST"],
            "移除黑名单"
        )
        context.register_web_api(
            f"/{prefix}/groups", self.api_get_groups_list, ["GET"],
            "获取机器人加入的群列表"
        )
        context.register_web_api(
            f"/{prefix}/friends", self.api_get_friends_list, ["GET"],
            "获取机器人的好友列表"
        )
        context.register_web_api(
            f"/{prefix}/config/track_group", self.api_track_group, ["POST"],
            "跟踪群黑名单侧栏群组（持久化显示）"
        )
        context.register_web_api(
            f"/{prefix}/config/untrack_group", self.api_untrack_group, ["POST"],
            "取消跟踪群黑名单侧栏群组"
        )
        context.register_web_api(
            f"/{prefix}/whitelist/toggle", self.api_toggle_whitelist, ["POST"],
            "切换群/好友白名单开关"
        )
        context.register_web_api(
            f"/{prefix}/groups/<group_id>/members", self.api_get_group_members, ["GET"],
            "获取指定群的成员列表"
        )

    async def api_get_config(self):
        """GET /config - 返回所有配置"""
        schema = self._get_config_schema()
        default = {}
        for key in schema:
            if hasattr(self, key):
                default[key] = getattr(self, key)
            elif key in self.config:
                default[key] = self.config[key]

        # 修正属性名映射差异
        default["welcome_image_url"] = self.welcome_image_urls
        default["leave_image_url"] = self.leave_image_urls

        return json_response({
            "default": default,
            "scoped": {
                "default": self.scoped_config.get("default", {}),
                "groups": self.scoped_config.get("groups", {}),
                "friends": self.scoped_config.get("friends", {}),
                "tracked_groups": self.scoped_config.get("tracked_groups", []),
                "global_blacklist": self.global_blacklist,
                "group_blacklist": {k: v for k, v in self.group_blacklist.items() if v},
                "friend_blacklist": self.friend_blacklist,
            },
            "schema": schema,
        })

    async def api_save_default_config(self):
        """POST /config/default - 保存默认配置覆盖并同步到 AstrBot 配置系统"""
        data = await request.json(default=None)
        if not isinstance(data, dict):
            return error_response("无效的请求数据")

        # 只保存 schema 中定义的键
        schema = self._get_config_schema()
        filtered = {k: v for k, v in data.items() if k in schema}

        # 写入 scoped 配置（启动时覆盖）
        self.scoped_config["default"] = filtered
        self._save_scoped_config()

        # 同步到 AstrBot 配置系统，存到 astrbot_plugin_smart_group_manager_config.json
        for k, v in filtered.items():
            self.config[k] = v
        self.config.save_config()

        # 应用到运行时
        self._apply_default_overrides()
        return json_response({"status": "ok", "message": "默认配置已保存"})

    async def api_save_group_config(self, group_id: str):
        """POST /config/group/<group_id> - 保存群配置覆盖"""
        data = await request.json(default=None)
        if not isinstance(data, dict):
            return error_response("无效的请求数据")

        self.scoped_config.setdefault("groups", {})
        schema = self._get_config_schema()
        filtered = {k: v for k, v in data.items() if k in schema and v is not None}
        self.scoped_config["groups"][str(group_id)] = filtered
        # 保存配置时自动跟踪该群
        tracked = list(dict.fromkeys(self.scoped_config.get("tracked_groups", [])))
        if str(group_id) not in tracked:
            tracked.append(str(group_id))
            self.scoped_config["tracked_groups"] = tracked
        self._save_scoped_config()
        return json_response({"status": "ok", "message": f"群 {group_id} 配置已保存"})

    async def api_delete_group_config(self, group_id: str):
        """DELETE /config/group/<group_id> - 删除群配置覆盖"""
        self.scoped_config.setdefault("groups", {}).pop(str(group_id), None)
        tracked = list(dict.fromkeys(self.scoped_config.get("tracked_groups", [])))
        gid_str = str(group_id)
        if gid_str in tracked:
            tracked.remove(gid_str)
            self.scoped_config["tracked_groups"] = tracked
        self._save_scoped_config()
        return json_response({"status": "ok", "message": f"群 {group_id} 配置已清除"})

    async def api_reset_group_blacklist_policy(self, group_id: str):
        """POST /config/group/<group_id>/reset_policy - 只重置黑名单策略覆盖"""
        self.scoped_config.setdefault("groups", {})
        current = self.scoped_config["groups"].get(str(group_id), {})
        if not current:
            return json_response({"status": "ok", "message": f"群 {group_id} 无配置覆盖"})
        # 只移除黑名单策略相关键，保留其他配置
        bl_keys = {"blacklist_mute_duration", "blacklist_mute_reply", "enable_admin_commands", "blacklist_admin", "enable_auto_kick"}
        changed = False
        for k in bl_keys:
            if k in current:
                del current[k]
                changed = True
        if changed:
            self.scoped_config["groups"][str(group_id)] = current if current else {}
            self._save_scoped_config()
        return json_response({"status": "ok", "message": f"群 {group_id} 黑名单策略已重置"})

    async def api_save_friend_config(self, friend_id: str):
        """POST /config/friend/<friend_id> - 保存好友配置覆盖"""
        data = await request.json(default=None)
        if not isinstance(data, dict):
            return error_response("无效的请求数据")

        self.scoped_config.setdefault("friends", {})
        schema = self._get_config_schema()
        filtered = {k: v for k, v in data.items() if k in schema and v is not None}
        if not filtered:
            self.scoped_config["friends"].pop(str(friend_id), None)
            self._save_scoped_config()
            return json_response({"status": "ok", "message": f"好友 {friend_id} 配置已清除"})
        self.scoped_config["friends"][str(friend_id)] = filtered
        self._save_scoped_config()
        return json_response({"status": "ok", "message": f"好友 {friend_id} 配置已保存"})

    async def api_delete_friend_config(self, friend_id: str):
        """DELETE /config/friend/<friend_id> - 删除好友配置覆盖"""
        self.scoped_config.setdefault("friends", {}).pop(str(friend_id), None)
        self._save_scoped_config()
        return json_response({"status": "ok", "message": f"好友 {friend_id} 配置已清除"})

    async def api_get_blacklist(self):
        """GET /blacklist - 获取黑名单数据"""
        return json_response({
            "global": self.global_blacklist,
            "groups": self.group_blacklist,
        })

    async def api_add_blacklist(self):
        """POST /blacklist/add - 添加黑名单"""
        data = await request.json(default=None)
        if not isinstance(data, dict):
            return error_response("无效的请求数据")

        user_id = str(data.get("user_id", ""))
        scope = data.get("type", "global")
        group_id = str(data.get("group_id", "")) if scope == "group" else None

        if not user_id:
            return error_response("请提供用户 QQ 号")

        if scope == "global":
            if user_id not in self.global_blacklist:
                self.global_blacklist.append(user_id)
                self._save_blacklist()
                return json_response({"status": "ok", "message": f"已添加 {user_id} 到全局黑名单"})
            return error_response(f"{user_id} 已在全局黑名单中")

        if scope == "group":
            if not group_id:
                return error_response("群黑名单需要提供 group_id")
            self.group_blacklist.setdefault(str(group_id), [])
            if user_id not in self.group_blacklist[str(group_id)]:
                self.group_blacklist[str(group_id)].append(user_id)
                self._save_blacklist()
                # 确保该群在 tracked_groups 中有记录，侧栏始终显示
                tracked = list(dict.fromkeys(self.scoped_config.get("tracked_groups", [])))
                gid_str = str(group_id)
                if gid_str not in tracked:
                    tracked.append(gid_str)
                self.scoped_config["tracked_groups"] = tracked
                self._save_scoped_config()
                return json_response({"status": "ok", "message": f"已添加 {user_id} 到群 {group_id} 黑名单"})
            return error_response(f"{user_id} 已在群 {group_id} 黑名单中")

        if scope == "friend":
            if user_id not in self.friend_blacklist:
                self.friend_blacklist.append(user_id)
                self._save_blacklist()
                return json_response({"status": "ok", "message": f"已添加 {user_id} 到好友黑名单"})
            return error_response(f"{user_id} 已在好友黑名单中")

        return error_response("无效的 scope 类型，请使用 global / group / friend")

    async def api_remove_blacklist(self):
        """POST /blacklist/remove - 移除黑名单"""
        data = await request.json(default=None)
        if not isinstance(data, dict):
            return error_response("无效的请求数据")

        user_id = str(data.get("user_id", ""))
        scope = data.get("type", "global")
        group_id = str(data.get("group_id", "")) if scope == "group" else None

        if not user_id:
            return error_response("请提供用户 QQ 号")

        if scope == "global":
            if user_id in self.global_blacklist:
                self.global_blacklist.remove(user_id)
                self._save_blacklist()
                return json_response({"status": "ok", "message": f"已从全局黑名单移除 {user_id}"})
            return error_response(f"{user_id} 不在全局黑名单中")

        if scope == "group":
            if not group_id:
                return error_response("群黑名单需要提供 group_id")
            group_list = self.group_blacklist.get(str(group_id), [])
            if user_id in group_list:
                new_list = [x for x in group_list if x != user_id]
                if new_list:
                    self.group_blacklist[str(group_id)] = new_list
                else:
                    self.group_blacklist.pop(str(group_id), None)
                self._save_blacklist()
                return json_response({"status": "ok", "message": f"已从群 {group_id} 黑名单移除 {user_id}"})
            return error_response(f"{user_id} 不在群 {group_id} 黑名单中")

        if scope == "friend":
            if user_id in self.friend_blacklist:
                self.friend_blacklist.remove(user_id)
                self._save_blacklist()
                return json_response({"status": "ok", "message": f"已从好友黑名单移除 {user_id}"})
            return error_response(f"{user_id} 不在好友黑名单中")

        return error_response("无效的 scope 类型，请使用 global / group / friend")

    async def api_track_group(self):
        """POST /config/track_group - 跟踪群"""
        data = await request.json(default=None)
        if not isinstance(data, dict):
            return error_response("无效数据")
        gid = str(data.get("id", ""))
        if not gid:
            return error_response("缺少 id")
        tracked = list(dict.fromkeys(self.scoped_config.get("tracked_groups", [])))
        if gid not in tracked:
            tracked.append(gid)
        self.scoped_config["tracked_groups"] = tracked
        self._save_scoped_config()
        return json_response({"status": "ok"})

    async def api_untrack_group(self):
        """POST /config/untrack_group - 取消跟踪群"""
        data = await request.json(default=None)
        if not isinstance(data, dict):
            return error_response("无效数据")
        gid = str(data.get("id", ""))
        if not gid:
            return error_response("缺少 id")
        tracked = list(dict.fromkeys(self.scoped_config.get("tracked_groups", [])))
        if gid in tracked:
            tracked.remove(gid)
        self.scoped_config["tracked_groups"] = tracked
        self._save_scoped_config()
        return json_response({"status": "ok"})

    async def api_get_groups_list(self):
        """GET /groups - 获取机器人加入的群列表"""
        bot = await self._get_bot_client()
        if bot is None:
            return error_response("机器人尚未就绪，请确保已收到至少一条消息", 503)
        try:
            groups = await bot.api.call_action("get_group_list")
            return json_response(groups if isinstance(groups, list) else [])
        except Exception as e:
            logger.error(f"获取群列表失败: {e}")
            return error_response(f"获取群列表失败: {e}")

    async def api_get_friends_list(self):
        """GET /friends - 获取机器人的好友列表"""
        bot = await self._get_bot_client()
        if bot is None:
            return error_response("机器人尚未就绪，请确保已收到至少一条消息", 503)
        try:
            friends = await bot.api.call_action("get_friend_list")
            return json_response(friends if isinstance(friends, list) else [])
        except Exception as e:
            logger.error(f"获取好友列表失败: {e}")
            return error_response(f"获取好友列表失败: {e}")

    async def api_toggle_whitelist(self):
        """POST /whitelist/toggle - 开关群/好友白名单"""
        data = await request.json(default=None)
        if not isinstance(data, dict):
            return error_response("无效的请求数据")

        scope = data.get("scope", "")  # "group" 或 "friend"
        id_str = str(data.get("id", ""))
        enabled = bool(data.get("enabled", False))

        if not id_str:
            return error_response("缺少 id")
        if scope not in ("group", "friend"):
            return error_response("scope 必须是 group 或 friend")

        all_ids = data.get("all_ids", [])

        if scope == "group":
            current = [str(x) for x in self.config.get("whitelist_group", [])]
            if enabled and id_str not in current:
                current.append(id_str)
            elif not enabled:
                # 关闭时：如果有 all_ids，先用全部已知群填充白名单，再移除当前群
                if all_ids and not current:
                    current = [str(x) for x in all_ids]
                if id_str in current:
                    current.remove(id_str)
            self.config["whitelist_group"] = current
            self.config.save_config()
            self.whitelist_group = current
            return json_response({"status": "ok", "enabled": enabled})

        if scope == "friend":
            current = [str(x) for x in self.config.get("whitelist", [])]
            if enabled and id_str not in current:
                current.append(id_str)
            elif not enabled:
                if all_ids and not current:
                    current = [str(x) for x in all_ids]
                if id_str in current:
                    current.remove(id_str)
            self.config["whitelist"] = current
            self.config.save_config()
            self.whitelist = current
            return json_response({"status": "ok", "enabled": enabled})

    async def api_get_group_members(self, group_id: str):
        """GET /groups/<group_id>/members - 获取群成员列表"""
        bot = await self._get_bot_client()
        if bot is None:
            return error_response("机器人尚未就绪", 503)
        try:
            raw = await bot.api.call_action("get_group_member_list", group_id=int(group_id))
            if isinstance(raw, dict):
                raw = raw.get("data", raw)
            if not isinstance(raw, list):
                return json_response([])
            result = []
            for m in raw:
                uid = str(m.get("user_id", "") or "")
                if not uid:
                    continue
                nickname = m.get("card") or m.get("nickname") or ""
                result.append({"user_id": uid, "nickname": nickname})
            logger.info(f"获取群 {group_id} 成员列表: {len(result)} 人")
            return json_response(result)
        except Exception as e:
            logger.error(f"获取群 {group_id} 成员列表失败: {e}")
            return error_response(f"获取群成员列表失败: {e}")

    async def terminate(self):
        """插件被卸载时调用"""
        logger.info("群管理插件已卸载")
