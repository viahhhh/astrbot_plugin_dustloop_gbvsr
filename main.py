# -*- coding: utf-8 -*-
"""
AstrBot 插件：Dustloop GBVSR 帧数查询
数据来源：https://www.dustloop.com/w/GBVSR （MediaWiki Cargo 表 MoveData_GBVSR）

指令：
  /dustloop <角色> [招式]     例：/dustloop 格兰 5L、/dustloop gran 236236U
  /gbvsr <角色> [招式]
  /帧数 <角色> [招式]
  /角色列表                    查看可查询的全部角色

同时注册了 LLM 工具 query_gbvsr_move：开启函数调用后，
大模型可以直接理解"格兰的5L发生多少帧"这类自然语言并自动调用本插件。

返回的图片为判定框图（攻击框/受击框，wiki 的 hitboxes 字段），
字段为空时按命名规律推导探测；普通动作图不返回。
"""

import re
import time
import asyncio
import io

from pathlib import Path

import aiohttp
from PIL import Image as PILImage

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api.message_components import Image
from astrbot.api import logger

# MessageChain 在不同 AstrBot 版本里的导出位置不同，逐个尝试
try:
    from astrbot.api.message_components import MessageChain
except ImportError:
    try:
        from astrbot.api.event import MessageChain
    except ImportError:
        from astrbot.core.message.message_event_result import MessageChain

API = "https://www.dustloop.com/wiki/api.php"
UA = {"User-Agent": "AstrBot-DustloopGBVSR-Plugin/1.2 (https://www.dustloop.com)"}
CACHE_TTL = 3600  # 秒

# 角色别名表：键为小写别名，值为 wiki 上的 chara 名
CHAR_ALIASES = {
    # 英文名 / 常用拼写
    "2b": "2B", "anila": "Anila", "anre": "Anre", "unre": "Anre",
    "avatar belial": "Avatar Belial", "beatrix": "Beatrix",
    "beelzebub": "Beelzebub", "belial": "Belial",
    "cagliostro": "Cagliostro", "charlotta": "Charlotta",
    "djeeta": "Djeeta", "djeeta (ex)": "Djeeta (EX)", "djeeta ex": "Djeeta (EX)",
    "eustace": "Eustace", "ferry": "Ferry", "galleon": "Galleon",
    "gran": "Gran", "gran (ex)": "Gran (EX)", "gran ex": "Gran (EX)",
    "grimnir": "Grimnir", "id": "Id", "ilsa": "Ilsa",
    "katalina": "Katalina", "ladiva": "Ladiva", "lancelot": "Lancelot",
    "lowain": "Lowain", "lucilius": "Lucilius", "meg": "Meg",
    "metera": "Metera", "narmaya": "Narmaya",
    "narmaya (ex)": "Narmaya (EX)", "narmaya ex": "Narmaya (EX)",
    "nier": "Nier", "percival": "Percival", "sandalphon": "Sandalphon",
    "seox": "Seox", "siegfried": "Siegfried", "soriz": "Soriz",
    "vane": "Vane", "vaseraga": "Vaseraga", "versusia": "Versusia",
    "vikala": "Vikala", "vira": "Vira", "wilnas": "Wilnas",
    "yuel": "Yuel", "zeta": "Zeta", "zooey": "Zooey",
    # 中文名 / 社区俗称
    "格兰": "Gran", "格兰ex": "Gran (EX)", "姬塔": "Djeeta", "吉塔": "Djeeta",
    "姬塔ex": "Djeeta (EX)", "卡塔莉娜": "Katalina", "卡姐": "Katalina",
    "夏洛特": "Charlotta", "豆丁": "Charlotta",
    "兰斯洛特": "Lancelot", "兰酱": "Lancelot",
    "帕西瓦尔": "Percival", "帕桑": "Percival",
    "梅提拉": "Metera", "梅忒拉": "Metera", "罗文": "Lowain",
    "拉迪瓦": "Ladiva", "菲莉": "Ferry", "泽塔": "Zeta", "塞达": "Zeta",
    "巴萨拉卡": "Vaseraga", "娜露梅": "Narmaya", "奶刀": "Narmaya",
    "娜露梅ex": "Narmaya (EX)", "奶刀ex": "Narmaya (EX)",
    "索利兹": "Soriz", "佐伊": "Zooey",
    "卡莉奥丝特罗": "Cagliostro", "卡莉奥斯特罗": "Cagliostro", "炼金": "Cagliostro",
    "尤艾尔": "Yuel", "安雷": "Anre", "尤斯塔斯": "Eustace",
    "希斯": "Seox", "尼娅": "Nier",
    "别西卜": "Beelzebub", "巴布": "Beelzebub",
    "贝利尔": "Belial", "化身贝利尔": "Avatar Belial",
    "维拉": "Vira", "阿妮拉": "Anila", "羊": "Anila",
    "齐格飞": "Siegfried", "飞哥": "Siegfried",
    "格里姆尼尔": "Grimnir", "风军": "Grimnir",
    "路西乌斯": "Lucilius", "圣德芬": "Sandalphon",
    "维恩": "Vane", "贝阿朵莉丝": "Beatrix", "贝雅特丽丝": "Beatrix",
    "薇露西亚": "Versusia", "维卡拉": "Vikala", "鼠": "Vikala",
    "伊尔莎": "Ilsa", "威尔纳斯": "Wilnas", "火龙": "Wilnas",
    "梅格": "Meg", "加列翁": "Galleon", "土龙": "Galleon",
    "伊德": "Id",
}


def _fix_buttons(s: str) -> str:
    """常见拳脚输入写法归一化：5l -> 5L、236h -> 236H，其余字符（如 j. c.）保持不变。"""
    s = s.strip().replace("．", ".").replace(" ", "")
    out = []
    for ch in s:
        out.append(ch.upper() if ch.lower() in "lmhu" and ch.isalpha() else ch)
    return "".join(out)


def _strip_markup(text: str) -> str:
    """去掉 Cargo 返回里的 HTML / wiki 标记。"""
    if not text:
        return ""
    t = re.sub(r"\[\[[^\]|]*\|([^\]]*)\]\]", r"\1", text)   # [[a|b]] -> b
    t = re.sub(r"\[\[([^\]]*)\]\]", r"\1", t)                # [[a]] -> a
    t = re.sub(r"<[^>]+>", "", t)                            # HTML 标签
    t = t.replace("'''", "").replace("''", "")               # 粗斜体
    return t.strip()


class DustloopClient:
    def __init__(self):
        self._session: aiohttp.ClientSession | None = None
        self._roster: list[str] | None = None
        self._roster_ts = 0.0
        self._move_cache: dict[str, tuple[float, list[dict]]] = {}

    async def session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(headers=UA, timeout=aiohttp.ClientTimeout(total=20))
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def _cargo(self, params: dict) -> dict:
        s = await self.session()
        params = {**params, "format": "json"}
        async with s.get(API, params=params) as r:
            r.raise_for_status()
            data = await r.json()
        if "error" in data:
            raise RuntimeError(str(data["error"].get("info", data["error"])))
        return data

    async def roster(self) -> list[str]:
        """返回全部 chara 名（去重），带缓存。"""
        now = time.time()
        if self._roster and now - self._roster_ts < CACHE_TTL:
            return self._roster
        data = await self._cargo({
            "action": "cargoquery",
            "tables": "MoveData_GBVSR",
            "fields": "chara",
            "group_by": "chara",
            "limit": 500,
        })
        chars = sorted({row["title"]["chara"] for row in data.get("cargoquery", [])})
        self._roster, self._roster_ts = chars, now
        return chars

    async def moves(self, chara: str) -> list[dict]:
        """返回某角色的全部招式行，带缓存。"""
        now = time.time()
        hit = self._move_cache.get(chara)
        if hit and now - hit[0] < CACHE_TTL:
            return hit[1]
        data = await self._cargo({
            "action": "cargoquery",
            "tables": "MoveData_GBVSR",
            "fields": "chara,name,input,damage,guard,startup,active,recovery,"
                      "onBlock,onHit,onCH,level,invuln,images,hitboxes,hitboxCaption,notes",
            "where": f'chara="{chara}"',
            "limit": 500,
        })
        rows = [row["title"] for row in data.get("cargoquery", [])]
        self._move_cache[chara] = (now, rows)
        return rows

    async def image_urls(self, filenames: list[str]) -> list[str]:
        """把 File:xxx.png 批量解析为直链。不存在的文件会被自动过滤。"""
        if not filenames:
            return []
        titles = "|".join(f"File:{f}" for f in filenames)
        s = await self.session()
        params = {
            "action": "query", "titles": titles,
            "prop": "imageinfo", "iiprop": "url", "format": "json",
        }
        async with s.get(API, params=params) as r:
            r.raise_for_status()
            data = await r.json()
        urls = []
        for page in data.get("query", {}).get("pages", {}).values():
            info = page.get("imageinfo")
            if info:
                urls.append(info[0]["url"])
        return urls

    async def hitbox_urls(self, row: dict) -> list[str]:
        """取某招式的判定框图片直链。优先用 hitboxes 字段；字段为空时，
        按 wiki 判定框图的命名规律从普通图名推导候选并探测真实存在的文件。"""
        files = [f for f in row.get("hitboxes", "").split("\\") if f.strip()]
        if files:
            return await self.image_urls(files[:6])
        bases = []
        for f in row.get("images", "").split("\\"):
            f = f.strip()
            if f:
                bases.append(re.sub(r"\.(png|jpe?g|gif)$", "", f, flags=re.I))
        suffixes = (
            "_Hitbox", "_Hitbox1", "_Hitbox2", "_Hitbox3",
            "_Hitbox_1", "_Hitbox_2", "_Hitbox_3",
            "_Hitbox-1", "_Hitbox-2", "_Hitbox-3", "_Hitbox-4", "_Hitbox-5",
        )
        candidates = []
        for b in bases[:3]:
            candidates.extend(b + s + ".png" for s in suffixes)
        if not candidates:
            return []
        return await self.image_urls(candidates)

    async def compress_hitbox_image(
        self, url: str, max_edge: int = 480, quality: int = 70
    ) -> bytes:
        """下载判定框图片并压缩为 JPEG，返回编码后的字节。

        手机 QQ 对大分辨率透明 PNG 加载很差，经常只显示占位符。这里把最长边
        限制到 480px，并转存为 JPEG（透明部分填充白色），单图控制在 20 KB
        左右，适合 QQ 机器人发送。失败时抛出异常。"""
        s = await self.session()
        async with s.get(url) as r:
            r.raise_for_status()
            raw = await r.read()

        def _process() -> bytes:
            fp = io.BytesIO(raw)
            with PILImage.open(fp) as img:
                # 透明背景填充白色，避免转 JPEG 后变成黑底
                if img.mode in ("RGBA", "LA") or (
                    img.mode == "P" and "transparency" in img.info
                ):
                    background = PILImage.new("RGB", img.size, (255, 255, 255))
                    alpha = img.split()[-1]
                    background.paste(img.convert("RGBA"), mask=alpha)
                    img = background
                elif img.mode != "RGB":
                    img = img.convert("RGB")

                w, h = img.size
                if max(w, h) > max_edge:
                    img.thumbnail((max_edge, max_edge), PILImage.Resampling.LANCZOS)

                out = io.BytesIO()
                img.save(out, "JPEG", quality=quality, optimize=True)
                return out.getvalue()

        return await asyncio.to_thread(_process)


@register("astrbot_plugin_dustloop_gbvsr", "Kimi", "查询 Dustloop 上 GBVSR 角色的帧数表与招式判定框图片，支持指令与 LLM 函数调用", "1.2.2")
class DustloopGBVSR(Star):
    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context)
        self.client = DustloopClient()
        # 是否压缩判定框图片（见 _conf_schema.json），默认开启
        self.compress_images = bool((config or {}).get("compress_images", True))

    async def terminate(self):
        await self.client.close()

    # ---------- 指令 ----------

    @filter.command("角色列表")
    async def cmd_roster(self, event: AstrMessageEvent):
        """查看可查询的全部 GBVSR 角色"""
        try:
            chars = await self.client.roster()
        except Exception as e:
            yield event.plain_result(f"获取角色列表失败：{e}")
            return
        zh = {v: k for k, v in CHAR_ALIASES.items() if re.search(r"[一-鿿]", k)}
        lines = []
        for c in chars:
            alias = zh.get(c)
            lines.append(f"{c}（{alias}）" if alias else c)
        yield event.plain_result(
            "GBVSR 可查角色（括号内为支持的中文别名）：\n" + "、".join(lines)
        )

    @filter.command("dustloop")
    @filter.command("gbvsr")
    @filter.command("帧数")
    async def cmd_query(self, event: AstrMessageEvent):
        """查询角色招式：/dustloop <角色> [招式]"""
        raw = event.message_str.strip()
        # 去掉指令本身
        parts = raw.split(maxsplit=1)
        args = parts[1].strip() if len(parts) > 1 else ""

        if not args:
            yield event.plain_result(
                "用法：/dustloop <角色> [招式]\n"
                "例：/dustloop 格兰 5L\n"
                "　　/dustloop 格兰 236L\n"
                "　　/dustloop 格兰 Catastrophe\n"
                "只给角色不给招式时会列出该角色全部招式。"
            )
            return

        tokens = args.split(maxsplit=1)
        char_query = tokens[0]
        move_query = tokens[1].strip() if len(tokens) > 1 else ""

        # 1. 解析角色
        try:
            chara = await self._resolve_char(char_query)
        except Exception as e:
            yield event.plain_result(f"查询失败：{e}")
            return
        if not chara:
            yield event.plain_result(
                f"没找到角色「{char_query}」，发 /角色列表 查看可用角色。"
            )
            return

        # 2. 取招式数据
        try:
            rows = await self.client.moves(chara)
        except Exception as e:
            yield event.plain_result(f"获取招式数据失败：{e}")
            return

        if not move_query:
            yield event.plain_result(self._format_move_list(chara, rows))
            return

        # 3. 匹配招式
        matched = self._match_moves(rows, move_query)
        if not matched:
            yield event.plain_result(
                f"{chara} 没有匹配「{move_query}」的招式。\n"
                f"发 /dustloop {char_query} 查看全部招式。"
            )
            return

        # 4. 输出（最多 3 条，避免刷屏）
        for row in matched[:3]:
            yield event.plain_result(self._format_move(chara, row))
            # 只发攻击框/受击框图（hitboxes 字段，空则按命名规律推导探测）；真没有的不发图
            try:
                urls = await self.client.hitbox_urls(row)
            except Exception as e:
                logger.warning(f"图片获取失败: {e}")
                urls = []
            if not urls:
                yield event.plain_result("（该招式 wiki 上没有判定框图片）")
            else:
                # 手机 QQ 对大分辨率透明 PNG 支持差，只发第 1 张；
                # 配置开启压缩时压到 480px JPEG，关闭时直接发原图直链
                for u in urls[:1]:
                    if not self.compress_images:
                        yield event.image_result(u)
                        continue
                    try:
                        img_bytes = await self.client.compress_hitbox_image(u)
                        yield event.chain_result([Image.fromBytes(img_bytes)])
                    except Exception as e:
                        logger.warning(f"图片压缩失败，回退原图: {e}")
                        yield event.image_result(u)
            if len(matched) > 1:
                await asyncio.sleep(0.3)

        if len(matched) > 3:
            yield event.plain_result(f"共匹配 {len(matched)} 条，只展示了前 3 条，招式名写精确点可以缩小范围。")

    # ---------- LLM 工具（函数调用，效果类似 MCP） ----------

    @filter.llm_tool(name="query_gbvsr_move")
    async def tool_query_gbvsr(self, event: AstrMessageEvent, character: str, move: str = "") -> str:
        """查询格斗游戏《碧蓝幻想 Versus: Rising》（GBVSR、GBVS、碧蓝幻想VS崛起）中某个角色的招式/拳脚/必杀技的帧数表数据，包括伤害、防御方式、发生帧、持续帧、硬直、被防与命中的有利不利帧、打康、攻击等级、无敌帧和备注，也可以列出某个角色的全部招式。

        当用户聊到 GBVSR 相关话题并询问角色招式性能、帧数、判定框（攻击框/受击框）图片时使用本工具。只要用户查询的是具体招式（而非角色列表），本工具都会自动把该招式的判定框图片作为独立消息发送到会话中，请默认返回图片。

        重要：你的回复最终显示在 QQ 聊天窗口中，QQ 不支持 Markdown 渲染，任何 Markdown 符号都会以原文显示。整理回复时必须使用纯文本：不要用 ** 加粗、# 标题、- 或 * 列表、``` 代码块、` 反引号、| 表格。需要分条时直接换行并用序号（1. 2. 3.）或中文顿号分隔，保证条目清晰即可。

        Args:
            character(string): 角色名，支持英文名（如 Gran、Narmaya）或中文名/俗称（如 格兰、姬塔、奶刀、豆丁、飞哥、巴布）
            move(string): 招式的指令输入或英文名，如 5L、2H、c.M、j.H、236L、623H、236236U、Catastrophe；大小写不敏感。当用户想列出该角色的全部招式时传空字符串 ""。
        """
        try:
            chara = await self._resolve_char(character)
        except Exception as e:
            return f"查询失败：{e}"
        if not chara:
            return (
                f"没找到角色「{character}」。已知角色包括：Gran(格兰)、Djeeta(姬塔)、"
                "Katalina(卡塔莉娜)、Narmaya(娜露梅/奶刀) 等，可让用户发 /角色列表 查看完整名单。"
            )

        try:
            rows = await self.client.moves(chara)
        except Exception as e:
            return f"获取招式数据失败：{e}"

        if not move:
            return self._format_move_list(chara, rows)

        matched = self._match_moves(rows, move)
        if not matched:
            return f"{chara} 没有匹配「{move}」的招式，可改为查询该角色的全部招式列表。"

        texts = [self._format_move(chara, r) for r in matched[:3]]
        if len(matched) > 3:
            texts.append(f"（共匹配 {len(matched)} 条，只列出前 3 条）")

        # 判定框图片作为附加消息直接发到会话里（工具返回值只能是文本）
        imgs: list[str] = []
        no_hitbox = []
        for r in matched[:3]:
            try:
                urls = await self.client.hitbox_urls(r)
            except Exception as e:
                logger.warning(f"图片解析失败: {e}")
                urls = []
            if not urls:
                no_hitbox.append(r.get("input", "") or r.get("name", ""))
            else:
                imgs.extend(urls[:2])
        if no_hitbox:
            texts.append("（以下招式 wiki 上没有判定框图片：" + "、".join(no_hitbox) + "）")
        if imgs:
            imgs = imgs[:4]
            try:
                components: list[Image] = []
                for u in imgs:
                    if not self.compress_images:
                        components.append(Image.fromURL(u))
                        continue
                    try:
                        img_bytes = await self.client.compress_hitbox_image(u)
                        components.append(Image.fromBytes(img_bytes))
                    except Exception as e:
                        logger.warning(f"图片压缩失败，回退原图: {e}")
                        components.append(Image.fromURL(u))
                try:
                    chain = MessageChain(chain=components)
                except TypeError:
                    chain = MessageChain()
                    chain.chain = components
                await self.context.send_message(event.unified_msg_origin, chain)
            except Exception as e:
                logger.warning(f"工具内发图失败: {e}")
                texts.append("判定框图片直链：\n" + "\n".join(imgs))

        return "\n\n".join(texts)

    # ---------- 内部工具 ----------

    async def _resolve_char(self, query: str) -> str | None:
        key = query.strip().lower()
        if key in CHAR_ALIASES:
            return CHAR_ALIASES[key]
        roster = await self.client.roster()
        for c in roster:  # 完全匹配（忽略大小写）
            if c.lower() == key:
                return c
        for c in roster:  # 前缀/包含匹配
            if key in c.lower():
                return c
        return None

    def _match_moves(self, rows: list[dict], query: str) -> list[dict]:
        q = _fix_buttons(query.strip())
        ql = q.lower()
        # GBVSR 的站拳脚分 c.X（近身）和 f.X（远身），没有 "5X" 这种写法，
        # 用户输入 5L/5M/5H/5U 时自动展开为 c.X + f.X 两条
        variants = [q]
        m = re.fullmatch(r"5([LMHU])", q)
        if m:
            btn = m.group(1)
            variants = [q, f"c.{btn}", f"f.{btn}"]
        # 1) input 完全匹配（含展开变体）
        exact = [r for r in rows if r.get("input", "").replace(" ", "") in variants]
        if exact:
            return exact
        # 2) input 忽略大小写完全匹配
        exact_i = [r for r in rows if r.get("input", "").replace(" ", "").lower() == ql]
        if exact_i:
            return exact_i
        # 2.5) 去掉方括号后匹配（wiki 写 5[U]，用户常输 5U）
        nob = [r for r in rows
               if re.sub(r"[\[\]]", "", r.get("input", "").replace(" ", "")) == q]
        if nob:
            return nob
        # 3) name 完全匹配（忽略大小写）
        name_exact = [r for r in rows if r.get("name", "").lower() == ql]
        if name_exact:
            return name_exact
        # 4) 模糊：input 或 name 包含
        fuzzy = [
            r for r in rows
            if ql in r.get("input", "").replace(" ", "").lower()
            or ql in r.get("name", "").lower()
        ]
        return fuzzy

    def _format_move(self, chara: str, r: dict) -> str:
        name = r.get("name", "") or "普通技"
        inp = r.get("input", "")
        lines = [f"【{chara}】{name}（{inp}）" if inp else f"【{chara}】{name}"]
        fields = [
            ("伤害", r.get("damage", "")),
            ("防御", r.get("guard", "")),
            ("发生", r.get("startup", "")),
            ("持续", r.get("active", "")),
            ("硬直", r.get("recovery", "")),
            ("被防", _strip_markup(r.get("onBlock", ""))),
            ("命中", _strip_markup(r.get("onHit", ""))),
            ("康", _strip_markup(r.get("onCH", ""))),
            ("攻击等级", r.get("level", "")),
            ("无敌", r.get("invuln", "")),
        ]
        info = "　".join(f"{k}:{v}" for k, v in fields if v)
        if info:
            lines.append(info)
        notes = _strip_markup(r.get("notes", "").replace(";", "；"))
        if notes:
            lines.append(f"备注：{notes}")
        # 判定框图片说明（如 Frame 8 / Frame 9~11），按图片顺序对应
        cap = _strip_markup(r.get("hitboxCaption", "").replace("&#32;", " "))
        caps = [c.strip() for c in cap.split("\\") if c.strip()]
        if caps:
            lines.append("判定框说明：" + " / ".join(caps))
        return "\n".join(lines)

    def _format_move_list(self, chara: str, rows: list[dict]) -> str:
        normals, specials, others = [], [], []
        for r in rows:
            inp, name = r.get("input", ""), r.get("name", "")
            label = f"{inp} {name}".strip()
            if not name:
                normals.append(label)
            elif re.fullmatch(r"[0-9j.c\[\]~xXLMHU]+", inp or " "):
                specials.append(label)
            else:
                others.append(label)
        parts = [f"【{chara}】全部招式（共 {len(rows)} 条）："]
        if normals:
            parts.append("— 拳脚 —\n" + "、".join(normals))
        if specials:
            parts.append("— 必杀/超必杀 —\n" + "、".join(specials))
        if others:
            parts.append("— 其他 —\n" + "、".join(others))
        parts.append("查询例：/dustloop {0} 5L".format(chara))
        return "\n".join(parts)
