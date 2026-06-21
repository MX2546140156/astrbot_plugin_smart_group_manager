"""
群管理插件 - 支持成员互动、自动审批、进群欢迎等功能。
"""

import asyncio
import json
import os
import random
import re

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.event.filter import on_llm_response
from astrbot.api.provider import LLMResponse
from astrbot.api.star import Context, Star, register
from astrbot.api import logger


@register("astrbot_plugin_smart_group_manager", "developer", "智能QQ群管理插件", "1.2.0")
class SmartGroupManager(Star):
    """智能QQ群管理插件"""

    BLACKLIST_FILE = os.path.join(os.path.dirname(__file__), "configs/blacklist.json")
    POKE_REPLIES_FILE = os.path.join(os.path.dirname(__file__), "configs/poke_replies.json")

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
        # 合并配置文件黑名单 + 持久化黑名单
        config_blacklist = [str(item) for item in config.get("blacklist", [])]
        self.blacklist: list = list(dict.fromkeys(config_blacklist + self._load_blacklist()))

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
        self.poke_enabled: bool = config.get("poke_enabled", True)

        # LLM 回复过滤配置
        self.llm_filter_rules: list = config.get("llm_filter_rules", [])

        # 戳一戳回复配置（从配置文件加载戳一戳回复配置内容）
        poke_replies = self._load_poke_replies()
        self.poke_back_replies: list = config.get(
            "poke_back_replies",
            poke_replies.get("poke_back_replies", [])
        )
        self.poke_noreply_replies: list = config.get(
            "poke_noreply_replies",
            poke_replies.get("poke_noreply_replies", [])
        )

        # 如果用户在 WebUI 配置了自定义回复，同步写入文件
        if "poke_back_replies" in config or "poke_noreply_replies" in config:
            self._save_poke_replies({
                "poke_back_replies": self.poke_back_replies,
                "poke_noreply_replies": self.poke_noreply_replies,
            })

        logger.info("群管理插件（v1.2.0）已加载")

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

    def _load_blacklist(self) -> list:
        """从本地文件加载黑名单"""
        try:
            if os.path.exists(self.BLACKLIST_FILE):
                with open(self.BLACKLIST_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        return [str(item) for item in data]
        except Exception as e:
            logger.warning(f"加载黑名单文件失败: {e}")
        return []

    def _save_blacklist(self):
        """保存黑名单到本地文件"""
        try:
            with open(self.BLACKLIST_FILE, "w", encoding="utf-8") as f:
                json.dump(self.blacklist, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存黑名单文件失败: {e}")

    def _load_poke_replies(self) -> dict:
        """从本地文件加载戳一戳回复列表"""
        try:
            if os.path.exists(self.POKE_REPLIES_FILE):
                with open(self.POKE_REPLIES_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        return data
        except Exception as e:
            logger.warning(f"加载默认戳一戳回复文件失败: {e}")
        return {}

    def _save_poke_replies(self, data: dict):
        """保存戳一戳回复到本地文件"""
        try:
            with open(self.POKE_REPLIES_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
        except Exception as e:
            logger.error(f"保存默认戳一戳回复文件失败: {e}")

    # ============================================================
    #  群管理员检查
    # ============================================================

    async def _get_member_display(self, event: AstrMessageEvent, group_id: str, target_id: str) -> str:
        """获取成员显示名，格式：[CQ:at,qq=xxx]（xxx）"""
        return f"[CQ:at,qq={target_id}]（{target_id}）"

    async def _is_group_admin(self, event: AstrMessageEvent, group_id: str, user_id: str) -> bool:
        """检查用户是否为群管理员/群主"""
        try:
            info = await self._call_api(event, "get_group_member_info", group_id=int(group_id), user_id=int(user_id))
            if isinstance(info, dict):
                data = info.get("data", info)
                role = data.get("role", "")
                return role in ("owner", "admin")
        except Exception:
            pass
        return False

    # ============================================================
    #  群管理命令
    # ============================================================

    async def _handle_admin_command(self, event: AstrMessageEvent, group_id: str, user_id: str, msg_text: str, raw_message) -> bool:
        """处理群内管理命令：拉黑/踢出/解黑/禁言/解禁。返回 True 表示已处理"""
        if not self.enable_admin_commands:
            return False
        text = msg_text.strip()

        # 从原始消息段中提取所有 @ 的 QQ 号
        def extract_at_qids(raw_msg):
            qids = []
            if isinstance(raw_msg, list):
                for seg in raw_msg:
                    if isinstance(seg, dict) and seg.get("type") == "at":
                        qq = seg.get("data", {}).get("qq", "")
                        if qq:
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

        # 检查命令前缀
        if re.match(r"^(拉黑)(?:\s|$)", text, re.IGNORECASE):
            target = extract_target(text, raw_message)
            if not target:
                await self._call_api(event, "send_group_msg", group_id=int(group_id), message="格式：拉黑 QQ号")
                return True
            if target == user_id:
                await self._call_api(event, "send_group_msg", group_id=int(group_id), message="不能拉黑自己")
                return True
            if not await self._is_group_admin(event, group_id, user_id):
                await self._call_api(event, "send_group_msg", group_id=int(group_id), message="只有群管理员/群主才能执行此操作")
                return True
            if target in self.blacklist:
                await self._call_api(event, "send_group_msg", group_id=int(group_id), message=f"[CQ:at,qq={target}] 已在黑名单中")
                return True
            display = await self._get_member_display(event, group_id, target)
            self.blacklist.append(target)
            self._save_blacklist()
            logger.info(f"[群管理命令] {user_id} 将 {target} 加入黑名单 (群 {group_id})")
            await self._call_api(event, "set_group_ban", group_id=int(group_id), user_id=int(target), duration=self.blacklist_mute_duration)
            await self._call_api(event, "send_group_msg", group_id=int(group_id), message=f"已将 {display} 加入黑名单并禁言{self._format_duration(self.blacklist_mute_duration)}")
            return True

        if re.match(r"^(解黑)(?:\s|$)", text, re.IGNORECASE):
            target = extract_target(text, raw_message)
            if not target:
                await self._call_api(event, "send_group_msg", group_id=int(group_id), message="格式：解黑 QQ号")
                return True
            if not await self._is_group_admin(event, group_id, user_id):
                await self._call_api(event, "send_group_msg", group_id=int(group_id), message="只有群管理才能执行此操作")
                return True
            if target not in self.blacklist:
                await self._call_api(event, "send_group_msg", group_id=int(group_id), message=f"[CQ:at,qq={target}] 不在黑名单中")
                return True
            display = await self._get_member_display(event, group_id, target)
            self.blacklist = [x for x in self.blacklist if x != target]
            self._save_blacklist()
            logger.info(f"[群管理命令] {user_id} 将 {target} 移出黑名单 (群 {group_id})")
            await self._call_api(event, "set_group_ban", group_id=int(group_id), user_id=int(target), duration=0)
            await self._call_api(event, "send_group_msg", group_id=int(group_id), message=f"已将 {display} 移出黑名单")
            return True

        if re.match(r"^(黑名单列表)(?:\s|$)", text, re.IGNORECASE):
            if not await self._is_group_admin(event, group_id, user_id):
                await self._call_api(event, "send_group_msg", group_id=int(group_id), message="只有群管理员/群主才能执行此操作")
                return True
            if not self.blacklist:
                await self._call_api(event, "send_group_msg", group_id=int(group_id), message="黑名单为空")
                return True
            msg = "当前黑名单列表：\n" + "\n".join(self.blacklist)
            await self._call_api(event, "send_group_msg", group_id=int(group_id), message=msg)
            return True

        if re.match(r"^(踢出)(?:\s|$)", text, re.IGNORECASE):
            target = extract_target(text, raw_message)
            if not target:
                await self._call_api(event, "send_group_msg", group_id=int(group_id), message="格式：踢出 QQ号")
                return True
            if target == user_id:
                await self._call_api(event, "send_group_msg", group_id=int(group_id), message="不能踢出自己")
                return True
            if not await self._is_group_admin(event, group_id, user_id):
                await self._call_api(event, "send_group_msg", group_id=int(group_id), message="只有群管理员/群主才能执行此操作")
                return True
            display = await self._get_member_display(event, group_id, target)
            await self._call_api(event, "set_group_kick", group_id=int(group_id), user_id=int(target))
            logger.info(f"[群管理命令] {user_id} 踢出 {target} (群 {group_id})")
            await self._call_api(event, "send_group_msg", group_id=int(group_id), message=f"已踢出 {display}")
            return True

        if re.match(r"^(禁言)(?:\s|$)", text, re.IGNORECASE):
            target = extract_target(text, raw_message)
            if not target:
                await self._call_api(event, "send_group_msg", group_id=int(group_id), message="格式：禁言 @用户 秒数 或 禁言 QQ号 秒数")
                return True
            if target == user_id:
                await self._call_api(event, "send_group_msg", group_id=int(group_id), message="不能禁言自己")
                return True
            if not await self._is_group_admin(event, group_id, user_id):
                await self._call_api(event, "send_group_msg", group_id=int(group_id), message="只有群管理员/群主才能执行此操作")
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

        if re.match(r"^(解禁)(?:\s|$)", text, re.IGNORECASE):
            target = extract_target(text, raw_message)
            if not target:
                await self._call_api(event, "send_group_msg", group_id=int(group_id), message="格式：解禁 @用户 或 解禁 QQ号")
                return True
            if not await self._is_group_admin(event, group_id, user_id):
                await self._call_api(event, "send_group_msg", group_id=int(group_id), message="只有群管理员/群主才能执行此操作")
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
        """检查消息是否违规并执行禁言"""
        try:
            # 黑名单检查，黑名单用户发消息直接禁言
            if str(user_id) in self.blacklist:
                logger.info(f"[黑名单] 群 {group_id} 黑名单用户 {user_id} 发送消息，自动禁言 {self.blacklist_mute_duration} 秒")
                await self._call_api(
                    event, "set_group_ban",
                    group_id=int(group_id), user_id=int(user_id), duration=self.blacklist_mute_duration
                )
                if self.mute_recall:
                    raw = event.message_obj.raw_message
                    message_id = self._get_raw_field(raw, "message_id")
                    if message_id:
                        await self._call_api(event, "delete_msg", message_id=int(message_id))
                if self.blacklist_mute_reply:
                    reply = self.blacklist_mute_reply.replace("{user_id}", str(user_id)).replace("{mute_duration}", self._format_duration(self.blacklist_mute_duration))
                    await self._call_api(event, "send_group_msg", group_id=int(group_id), message=reply)
                return True

            if not self.enable_auto_mute:
                return False

            # 禁言白名单检查
            if str(user_id) in self.mute_whitelist:
                return False

            should_mute = False
            reason = ""

            # 关键词正则匹配
            if self.mute_keywords:
                for pattern in self.mute_keywords:
                    try:
                        if re.search(pattern, msg_text):
                            should_mute = True
                            reason = f"关键词匹配: {pattern}"
                            break
                    except re.error as e:
                        logger.warning(f"禁言正则表达式错误 [{pattern}]: {e}")

            # AI 审核（关键词未命中时走 AI）
            if not should_mute and self.mute_ai_review:
                try:
                    llm = self._get_llm()
                    if llm:
                        full_prompt = f"{self.mute_ai_prompt}\n\n{msg_text}"
                        resp = await llm.text_chat(prompt=full_prompt, session_id=f"mute_{group_id}")
                        result_text = self._extract_llm_text(resp)
                        if result_text.strip().lower().startswith("yes"):
                            should_mute = True
                            reason = "AI审核判定违规"
                except Exception as e:
                    logger.warning(f"AI审核调用失败: {e}")

            # 执行禁言
            if should_mute:
                logger.info(f"[自动禁言] 群 {group_id} 用户 {user_id} {reason}，禁言 {self.mute_duration} 秒")
                await self._mute_user(event, group_id, user_id)
                return True

            return False
        except Exception as e:
            logger.error(f"自动禁言处理失败: {e}")
            return False

    async def _mute_user(self, event: AstrMessageEvent, group_id: str, user_id: str):
        """执行禁言操作（禁言 + 撤回 + 回复）"""
        try:
            await self._call_api(
                event, "set_group_ban",
                group_id=int(group_id), user_id=int(user_id), duration=self.mute_duration
            )

            # 撤回违规消息
            if self.mute_recall:
                raw = event.message_obj.raw_message
                message_id = self._get_raw_field(raw, "message_id")
                if message_id:
                    await self._call_api(event, "delete_msg", message_id=int(message_id))

            # 回复提示
            if self.mute_reply:
                reply = self.mute_reply.replace("{user_id}", str(user_id)).replace("{mute_duration}", self._format_duration(self.mute_duration))
                await self._call_api(
                    event, "send_group_msg",
                    group_id=int(group_id),
                    message=reply
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
                    if sub_type == "add" and str(user_id) in self.blacklist:
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
                if message_type != "group":
                    return

                group_id = self._get_raw_field(raw, "group_id")
                user_id = self._get_raw_field(raw, "user_id")
                if not (group_id and user_id):
                    return

                # 群白名单检查
                if not self._is_group_allowed(str(group_id)):
                    return

                raw_message = self._get_raw_field(raw, "message", "")
                msg_text = self._extract_plain_text(raw_message)
                if not msg_text:
                    return

                # 先检查是否是群管理命令
                if await self._handle_admin_command(event, str(group_id), str(user_id), msg_text, raw_message):
                    return

                await self._check_and_mute(event, str(group_id), str(user_id), msg_text)
                return

        except Exception as e:
            logger.error(f"处理事件失败：{e}")

    async def terminate(self):
        """插件被卸载时调用"""
        logger.info("群管理插件已卸载")
