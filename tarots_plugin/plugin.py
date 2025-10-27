from src.plugin_system.base.base_plugin import BasePlugin
from src.plugin_system.apis.plugin_register_api import register_plugin
from src.plugin_system.base.base_action import BaseAction, ActionActivationType
from src.plugin_system.base.base_command import BaseCommand
from src.plugin_system.base.component_types import ComponentInfo
from src.plugin_system.base.config_types import ConfigField
from src.plugin_system.apis import generator_api, config_api, message_api
from src.common.logger import get_logger
from PIL import Image
from typing import Tuple, Dict, Optional, List, Any, Type
from pathlib import Path
import traceback
import tomlkit
import json
import random
import asyncio
import aiohttp
import base64
import toml
import io
import os
import time

logger = get_logger("tarots")

class TarotsAction(BaseAction):
    action_name = "tarots"

    # 一些必要的初始化
    activation_type = ActionActivationType.ALWAYS
    activation_keywords = ["抽一张塔罗牌", "抽张塔罗牌"]
    keyword_case_sensitive = False

     # 模式和并行控制
    parallel_action = False

    action_description = "执行塔罗牌占卜，支持多种抽牌方式" # action描述
    action_parameters = {
        "card_type": "塔罗牌的抽牌范围，必填，只能填一个参数，这里请根据用户的要求填'全部'或'大阿卡纳'或'小阿卡纳'，如果用户的要求并不明确，默认填'全部'",
        "formation": "塔罗牌的抽牌方式，必填，只能填一个参数，这里请根据用户的要求填'单张'或'圣三角'或'时间之流'或'四要素'或'五牌阵'或'吉普赛十字'或'马蹄'或'六芒星'，如果用户的要求并不明确，默认填'单张'",
        "target_message": "提出抽塔罗牌的对方的发言内容，格式必须为：（用户名:发言内容），若不清楚是回复谁的话可以为None"
    }
    action_require = [
        "当消息包含'抽塔罗牌''塔罗牌占卜'等关键词，且用户明确表达了要求你帮忙抽牌的意向时，你看心情调用就行（这意味着你可以拒绝抽塔罗牌，拒绝执行这个动作）。",
        "用户需要明确指定抽牌范围和抽牌类型，如果用户未明确指定抽牌范围则默认为'全部'，未明确指定抽牌类型则默认为'单张'。",
        "请仔细辨别对方到底是不是在让你抽塔罗牌！如果用户只是单独说了'抽卡'，'抽牌'，'占卜'，'算命'等，而且并没有上文内容验证用户是想抽塔罗牌的意思，就不要抽塔罗牌，不要执行这个动作！",
        "在完成一次抽牌后，请仔细确定用户有没有明确要求再抽一次，没有再次要求就不要继续执行这个动作。"
        
    ]

    associated_types = ["image", "text"] #该插件会发送的消息类型
    
    def __init__(self,
    action_data: dict,
    action_reasoning: str,
    cycle_timers: dict,
    thinking_id: str,
    global_config: Optional[dict] = None,
    **kwargs,
    ):
        # 显式调用父类初始化
        super().__init__(
        action_data=action_data,
        action_reasoning=action_reasoning,
        cycle_timers=cycle_timers,
        thinking_id=thinking_id,
        global_config=global_config,
        **kwargs
    )
        # 初始化基本路径
        self.base_dir = Path(__file__).parent.absolute()

        # 扫描并更新可用牌组
        self.config = self._load_config()
        self._update_available_card_sets()

        # 初始化路径
        self.using_cards = self.config["cards"].get("using_cards", 'bilibili')
        if not self.using_cards:
            self.cache_dir = self.base_dir / "tarots_cache" / "default"
        else:
            self.cache_dir = self.base_dir / "tarots_cache" / self.using_cards # 定义图片缓存主文件夹为tarots_cache，后面紧随牌组文件夹名
            self.cache_dir.mkdir(parents=True, exist_ok=True) # 不存在该文件夹就创建

        # 加载卡牌数据
        self.card_map: Dict = {}
        self.formation_map: Dict = {}
        self._load_resources()

    def _load_resources(self):
        """同步加载资源文件(显式指定UTF-8编码)"""
        try:
            if not self.using_cards:
                logger.info("没有加载到任何可用牌组")
                return
            # 加载卡牌数据
            with open(
                self.base_dir / f"tarot_jsons/{self.using_cards}/tarots.json", 
                encoding="utf-8"  
            ) as f:
                self.card_map = json.load(f)
            
            # 加载牌阵配置
            with open(
                self.base_dir / "tarot_jsons/formation.json", 
                encoding="utf-8"  
            ) as f:
                self.formation_map = json.load(f)
                
            logger.info(f"{self.log_prefix} 已加载{self.card_map['_meta']['total_cards']}张卡牌和{len(self.formation_map)}种抽牌方式")
        except UnicodeDecodeError as e:
            logger.error(f"{self.log_prefix} 编码错误: 请确保JSON文件为UTF-8格式 - {str(e)}")
            raise
        except Exception as e:
            logger.error(f"{self.log_prefix} 资源加载失败: {str(e)}")
            raise

    async def execute(self) -> Tuple[bool, str]:
        """实现基类要求的入口方法"""
        try:
            if not self.card_map:
                await self.send_text("没有牌组，无法使用")
                return False, "没有牌组，无法使用"
            logger.info(f"{self.log_prefix} 开始执行塔罗占卜")
            
            # 参数解析
            request_type = self.action_data.get("card_type", "全部") 
            formation_name = self.action_data.get("formation", "单张")
            card_type = self.get_available_card_type(request_type)
            
            # 参数校验
            if card_type not in ["全部", "大阿卡纳", "小阿卡纳"]:
                await self.send_text("不存在这样的抽牌范围")
                return False, "参数错误"
                
            if formation_name not in self.formation_map:
                await self.send_text("不存在这样的抽牌方法")
                return False, "参数错误"
    
            # 获取牌阵配置
            formation = self.formation_map[formation_name] # 根据确定好的抽牌方式名称获取具体牌阵的字典
            cards_num = formation["cards_num"] # 该抽牌方式要抽几张牌
            is_cut = formation["is_cut"] # 该抽牌方式要不要切牌
            represent_list = formation["represent"] # 该抽牌方式所包含的预言方向内容
    
            # 获取有效卡牌范围
            valid_ids = self._get_card_range(card_type)
            if not valid_ids:
                await self.send_text("当前牌堆不对")
                return False, "参数错误"
    
            # 抽牌逻辑
            selected_ids = random.sample(valid_ids, cards_num)
            if is_cut:
                selected_cards = [
                    (cid, random.random() < 0.5)  # 切牌时50%概率逆位
                    for cid in selected_ids
                ]
            else:
                selected_cards = [
                    (cid, False)  # 不切牌时全部正位
                    for cid in selected_ids
                ]
    
            # 结果处理
            result_text = f"【{formation_name}牌阵 - {self.using_cards}牌组】\n"
            failed_images = []  # 记录获取失败的图片
            reply_message = self.action_message
            user_nickname = self.user_nickname

            if not reply_message:
                return False, "未找到相关回复消息，中止塔罗牌抽取"

            for idx, (card_id, is_reverse) in enumerate(selected_cards):
                card_data = self.card_map[card_id]
                card_info = card_data["info"]
                pos_name = represent_list[0][idx] if idx < len(represent_list[0]) else f"位置{idx+1}"
                
                # 轮询发送图片
                img_data = await self._get_card_image(card_id, is_reverse)
                if img_data:
                    b64_data = base64.b64encode(img_data).decode('utf-8')
                    await self.send_custom("image", b64_data, False, True, reply_message)
                else:
                    # 记录失败的图片
                    failed_images.append(f"{card_data['name']}({'逆位' if is_reverse else '正位'})")
                    logger.warning(f"{self.log_prefix} 卡牌图片获取失败: {card_id}")
                
                # 轮询构建文本
                desc = card_info['reverseDescription'] if is_reverse else card_info['description']
                result_text += (
                    f"\n{pos_name} - {'逆位' if is_reverse else '正位'} {card_data['name']}\n"
                    f"{desc[:100]}...\n"
                )
                await asyncio.sleep(0.3)  # 防止消息频率限制

            if failed_images:
                error_msg = f"以下卡牌图片获取失败，占卜中断: {', '.join(failed_images)}"
                await self.send_text(error_msg)
                return False, ""
                
            await asyncio.sleep(1.5) # 权宜之计，给最后一张图片1.5s的发送起跑时间，无可奈何的办法
            
            original_text = self.config["adjustment"].get("enable_original_text", False)
            self_id = config_api.get_global_config("bot.qq_account")

            message_text = ""

            result_status, result_message = await generator_api.rewrite_reply(
                chat_stream=self.chat_stream,
                reply_data={ 
                "raw_reply": result_text,
                "reason": "抽出了塔罗牌结果，请根据其内容为用户进行解牌"
                },
                enable_splitter=False,
                enable_chinese_typo=False
            ) # 让你的麦麦用自己的语言风格阐释结果

            current_time = time.time()
            start_time = current_time - 300
      
            # 获取最近1条麦麦自身记录
            records = message_api.get_messages_by_time_in_chat_for_users(
                self.chat_id, start_time, current_time, [self_id], 1
            )[0]
   
            if original_text:
                await self.send_text(result_text)
                logger.info("原始文本已发送")

            if result_status:
             # 合并所有消息片段
                message_text = result_message.reply_set.reply_data[0].content
    
            # 一次性发送合并的消息
            if message_text:
                await self.send_custom("text", message_text, False, True, records)
                logger.info("合并消息已发送")
            else:
                return False, "消息生成错误，很可能是generator炸了"

            # 记录动作信息
            await self.store_action_info(
                action_build_into_prompt=True,
                action_prompt_display=f"已为{user_nickname}抽取了塔罗牌并成功解牌。",
                action_done=True
                )

            return True, f"已为{user_nickname}抽取了塔罗牌并成功解牌，占卜成功。"
            
        except Exception as e:
            error_msg = traceback.format_exc()
            logger.error(f"{self.log_prefix} 执行失败: {error_msg}")
            await self.send_text(f"占卜失败: {str(e)}")
            return False, "执行错误"
        
    def _get_card_range(self, card_type: str) -> list:
        """获取卡牌范围"""
        if card_type == "大阿卡纳":
            return [str(i) for i in range(22)]
        elif card_type == "小阿卡纳":
            return [str(i) for i in range(22, 78)]
        return [str(i) for i in range(78)] # 既不是大阿卡纳也不是小阿卡纳就返回全部的
    
    async def _get_card_image(self, card_id: str, is_reverse: bool) -> Optional[bytes]:
        """获取卡牌图片（有缓存机制）"""
        try:
            filename = f"{card_id}_norm.png"
            cache_path = self.cache_dir / filename
            # 检查缓存文件是否存在且有效
            if not cache_path.exists() or not self._validate_image_integrity(cache_path):
                if cache_path.exists():
                    logger.warning(f"{self.log_prefix} 发现损坏的缓存文件，准备重新下载: {cache_path}")
                    try:
                        cache_path.unlink()
                    except Exception as e:
                        logger.error(f"{self.log_prefix} 删除损坏文件失败: {str(e)}")
                        return None
                
                # 下载图片，现在返回布尔值
                success = await self._download_image(card_id, cache_path)
                if not success:
                    return None
            
            with open(cache_path, "rb") as f:
                img_data = f.read()
            
            if is_reverse:
                img_data = self._rotate_image(img_data) # 如果是逆位牌，直接把正位牌扭180度
                if not img_data:  # 旋转失败
                    return None

            return img_data

        except Exception as e:
            logger.warning(f"{self.log_prefix} 获取图片失败: {str(e)}")
            return None
        
    def _rotate_image(self, img_data: bytes) -> Optional[bytes]:
        """将图片旋转180度生成逆位图片"""
        try:
            # bytes → PIL Image对象
            image = Image.open(io.BytesIO(img_data))
            
            # 旋转180度（逆时针）
            rotated_image = image.rotate(180)
            
            # PIL Image对象 → bytes
            buffer = io.BytesIO()
            rotated_image.save(buffer, format='PNG')
            return buffer.getvalue()
            
        except Exception as e:
            logger.error(f"{self.log_prefix} 图片旋转失败: {str(e)}")
            # 旋转失败时返回None
            return None
        
    async def _download_image(self, card_id: str, save_path: Path):
        """图片本地缓存"""
        MAX_RETRIES = 3
        RETRY_DELAY = 2  # 初始重试间隔（秒）

        try:
            # 获取卡牌数据
            card_info = self.card_map[card_id]["info"]
            img_path = card_info['imgUrl']
            base_url = self.card_map["_meta"]["base_url"]
            # 获取代理数据
            enable_proxy = self.config["proxy"].get("enable_proxy", False)
            if enable_proxy:
                proxy_url = self.config["proxy"].get("proxy_url", "")
            else:
                proxy_url = None
            
            # 构建完整的下载URL
            full_url = f"{base_url}{img_path}"

            # 下载尝试循环
            for attempt in range(1, MAX_RETRIES + 1):
                try:
                    logger.info(f"[图片下载] 尝试 {attempt}/{MAX_RETRIES} - {card_id} - {full_url}")
                    
                    async with aiohttp.ClientSession() as session:
                        async with session.get(full_url, timeout=15, proxy=proxy_url) as resp:
                            if resp.status == 200:
                                # 确保目录存在
                                save_path.parent.mkdir(parents=True, exist_ok=True)
                                
                                # 写入文件
                                with open(save_path, "wb") as f:
                                    f.write(await resp.read())
                                
                                # 立即进行完整性检测
                                if self._validate_image_integrity(save_path):
                                    logger.info(f"[图片下载] 成功并通过完整性检测 {save_path.name} (尝试 {attempt}次)")
                                    return True
                                else:
                                    # 完整性检测失败，删除文件
                                    logger.warning(f"[图片下载] 完整性检测失败，删除文件: {save_path}")
                                    try:
                                        save_path.unlink()
                                    except Exception as delete_error:
                                        logger.error(f"[图片下载] 删除损坏文件失败: {delete_error}")
                                    
                                    # 如果不是最后一次尝试，继续重试
                                    if attempt < MAX_RETRIES:
                                        logger.info(f"[图片下载] 完整性检测失败，准备重试 (尝试 {attempt+1}/{MAX_RETRIES})")
                                        continue
                                    else:
                                        logger.error(f"[图片下载] 完整性检测失败且已达最大重试次数: {save_path}")
                                        break
                            else:
                                logger.warning(f"[图片下载] 异常状态码 {resp.status} - {full_url}")
                                
                except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                    logger.warning(f"[图片下载] 尝试 {attempt}/{MAX_RETRIES} 失败: {str(e)}")
                    
                # 指数退避等待
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(RETRY_DELAY ** attempt)

            # 最终失败处理
            logger.error(f"[图片下载] 终极失败 {full_url}，已达最大重试次数 {MAX_RETRIES}")
            return False

        except KeyError:
            logger.error(f"[图片下载] 致命错误：卡牌 {card_id} 不存在于card_map中")
            return False
        
        except Exception as e:
            logger.error(f"{self.log_prefix} 图片下载失败: {str(e)}")
            return False

    def _load_config(self) -> Dict[str, Any]:
        """从同级目录的config.toml文件直接加载配置"""
        try:
            # 获取当前文件所在目录
            script_dir = os.path.dirname(os.path.abspath(__file__))
            config_path = os.path.join(script_dir, "config.toml")
            
            # 读取并解析TOML配置文件
            with open(config_path, 'r', encoding='utf-8') as f:
                config_data = toml.load(f)
            
            # 构建配置字典，使用get方法安全访问嵌套值
            config = {
                "permissions": {
                    "admin_users": config_data.get("permissions", {}).get("admin_users", [])
                },
                "proxy": {
                    "enable_proxy": config_data.get("proxy", {}).get("enable_proxy", False),
                    "proxy_url": config_data.get("proxy", {}).get("proxy_url", "")
                },
                "cards": {
                    "using_cards": config_data.get("cards", {}).get("using_cards", 'bilibili'),
                    "use_cards": config_data.get("cards", {}).get("use_cards", ['bilibili','east'])
                },
                "adjustment": {
                    "enable_original_text": config_data.get("adjustment", {}).get("enable_original_text", False)
                }
            }
            return config
        except Exception as e:
            logger.error(f"{self.log_prefix} 加载配置失败: {e}")
            raise

    def _validate_image_integrity(self, file_path: Path) -> bool:
        """检查图片文件完整性"""
        try:
            # 检查文件是否存在
            if not file_path.exists():
                logger.debug(f"{self.log_prefix} 图片文件不存在: {file_path}")
                return False
            
            # 检查文件大小（至少要有内容，不能是0字节）
            if file_path.stat().st_size == 0:
                logger.warning(f"{self.log_prefix} 图片文件为空: {file_path}")
                return False
            
            # 尝试使用PIL打开图片来验证完整性
            try:
                with Image.open(file_path) as img:
                    # 验证图片基本信息
                    if img.size[0] <= 0 or img.size[1] <= 0:
                        logger.warning(f"{self.log_prefix} 图片尺寸异常: {file_path}")
                        return False
                    
                    # 尝试加载图片数据以确保文件没有损坏
                    img.load()
                    logger.debug(f"{self.log_prefix} 图片完整性校验通过: {file_path}")
                    return True
                    
            except (Image.UnidentifiedImageError, OSError, IOError) as e:
                logger.warning(f"{self.log_prefix} 图片损坏或格式错误: {file_path} - {str(e)}")
                return False
                
        except Exception as e:
            logger.error(f"{self.log_prefix} 图片完整性校验异常: {file_path} - {str(e)}")
            return False
        
    def get_available_card_type(self, user_requested_type):
        """获取当前牌组支持的卡牌类型"""
        supported_type = self.card_map.get("_meta", {}).get("card_types", "")
        # 如果牌组支持全部，或者用户请求与牌组支持的一致，就用用户请求的
        if supported_type == '全部' or user_requested_type == supported_type:
            return user_requested_type
        else:
            # 否则用牌组支持的类型
            return supported_type
        
    def _update_available_card_sets(self):
        """更新配置文件中的可用牌组列表"""
        try:
            current_using = self.config["cards"].get("using_cards", "")
            available_sets = self._scan_available_card_sets()

            # 如果当前使用的牌组不存在于可用牌组中
            if not current_using or current_using not in available_sets:
                # 尝试从可用牌组中选择一个有效的
                new_using = available_sets[0] if available_sets else ""
            
                logger.warning(
                    f"当前使用牌组 '{current_using}' 不存在，已自动切换至 '{new_using}'"
                    )
            
                # 更新当前使用牌组
                self.set_card(new_using)

            if available_sets:
                self.set_cards(available_sets)
                logger.info(f"已更新可用牌组配置: {available_sets}")
            else:
                logger.error("未发现任何可用牌组")
                self.set_card("")
                self.set_cards([])
                
            self.config = self._load_config()
        except Exception as e:
            logger.error(f"更新牌组配置失败: {e}")
        
    def _scan_available_card_sets(self) -> List[str]:
        """扫描tarot_jsons文件夹，返回可用牌组列表"""
        try:
            tarot_jsons_dir = self.base_dir / "tarot_jsons"
            available_sets = []
            
            if not tarot_jsons_dir.exists():
                logger.warning(f"tarot_jsons目录不存在: {tarot_jsons_dir}")
                return []
            
            for item in tarot_jsons_dir.iterdir():
                if item.is_dir():
                    tarots_json_path = item / "tarots.json"
                    if tarots_json_path.exists():
                        available_sets.append(item.name)
                        logger.info(f"发现可用牌组: {item.name}")
            
            return available_sets
        except Exception as e:
            logger.error(f"扫描牌组失败: {e}")
            return []
        
    def set_cards(self, cards: List):
        """使用tomlkit修改配置文件，保持注释和格式"""
        try:
            config_path = os.path.join(self.base_dir, "config.toml")
            
            # 使用tomlkit读取，保持格式和注释
            with open(config_path, 'r', encoding='utf-8') as f:
                config_data = tomlkit.load(f)
                config_data["cards"]["use_cards"] = cards
            
            # 使用tomlkit写入，保持格式和注释
            with open(config_path, 'w', encoding='utf-8') as f:
                tomlkit.dump(config_data, f)
                
        except Exception as e:
            logger.error(f"{self.log_prefix} 扫描牌组失败: {e}")
            raise

    def _check_cards(self, cards: str) -> bool:
        """权限检查逻辑"""
        
        use_cards = self.config["cards"].get("use_cards", ['bilibili','east'])
        if not use_cards:
            logger.warning(f"{self.log_prefix} 未配置可使用牌组列表")
            return ""
        return cards in use_cards
    
    def set_card(self, cards: str):
        """使用tomlkit修改配置文件，保持注释和格式"""
        try:
            config_path = os.path.join(self.base_dir, "config.toml")
            
            # 使用tomlkit读取，保持格式和注释
            with open(config_path, 'r', encoding='utf-8') as f:
                config_data = tomlkit.load(f)
                config_data["cards"]["using_cards"] = cards
            
            # 使用tomlkit写入，保持格式和注释
            with open(config_path, 'w', encoding='utf-8') as f:
                tomlkit.dump(config_data, f)
                
        except Exception as e:
            logger.error(f"{self.log_prefix} 更新配置文件失败: {e}")
            raise

class TarotsCommand(BaseCommand, TarotsAction):
    command_name = "tarots_command"
    command_description = "塔罗牌命令，目前仅做缓存"
    command_pattern = r"^/tarots\s+(?P<target_type>\w+)(?:\s+(?P<action_value>\w+))?\s*$"
    command_help = "使用方法: /tarots cache - 缓存所有牌面;/tarots switch 牌组名称 - 切换当前使用的牌组"
    command_examples = [
        "/tarots cache - 开始缓存全部牌面",
        "/tarots switch 牌组名称 - 切换当前使用的牌组"
    ]
    enable_command = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 初始化 TarotsAction 的属性
        self.base_dir = Path(__file__).parent.absolute()
        self.config = self._load_config()
        self._update_available_card_sets()
        self.using_cards = self.config["cards"].get("using_cards", 'bilibili')
        if not self.using_cards:
            self.cache_dir = self.base_dir / "tarots_cache" / "default"
        else:
            self.cache_dir = self.base_dir / "tarots_cache" / self.using_cards
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.card_map = {}
        self.formation_map = {}
        self._load_resources()

    async def execute(self) -> Tuple[bool, Optional[str]]:
        try:
            sender = self.message.message_info.user_info

            if not self._check_person_permission(sender.user_id):
                await self.send_text("权限不足，你无权使用此命令")    
                return False,"权限不足，无权使用此命令", True
            
            if not self.card_map:
                await self.send_text("没有牌组，无法使用")
                return False, "没有牌组，无法使用", True
            target_type = self.matched_groups.get("target_type")
            action_value = self.matched_groups.get("action_value")
            support_type = self.get_available_card_type("全部")
            if support_type == '全部':
                check_count=[str(i) for i in range(78)]
            elif support_type == '大阿卡纳':
                check_count=[str(i) for i in range(22)]
            elif support_type == '小阿卡纳':
                check_count=[str(i) for i in range(22,78)]
            else:
                await self.send_text("这不在可用牌组中") 
                return False, "非可用牌组", True
            
            if target_type == "cache" and not action_value:
                
                # 添加进度提示
                await self.send_text("开始缓存全部牌面，请稍候...")
                success_count = 0
                redownload_count = 0  # 记录重新下载的数量
                
                for card in check_count:
                    try:
                        filename = f"{card}_norm.png"
                        cache_path = self.cache_dir / filename

                        # 检查文件是否存在且完整
                        if not cache_path.exists() or not self._validate_image_integrity(cache_path):
                            if cache_path.exists():
                                # 文件存在但损坏，记录重新下载
                                logger.warning(f"{self.log_prefix} 发现损坏的缓存文件，准备重新下载: {cache_path}")
                                redownload_count += 1
                                try:
                                    cache_path.unlink()  # 删除损坏的文件
                                except Exception as e:
                                    logger.error(f"{self.log_prefix} 删除损坏文件失败: {str(e)}")
                                    continue
                            
                            # 下载图片
                            download_success = await self._download_image(card, cache_path)
                            if download_success:
                                success_count += 1
                            else:
                                logger.warning(f"{self.log_prefix} 下载卡牌 {card} 失败")
                        else:
                            # 文件存在且完整
                            success_count += 1

                    except Exception as e:
                        logger.warning(f"{self.log_prefix} 缓存卡牌 {card} 失败: {str(e)}")
                        continue 

                # 构建结果消息
                result_msg = f"缓存完成，成功缓存 {success_count}/{len(check_count)} 张牌面"
                if redownload_count > 0:
                    result_msg += f"，其中重新下载了 {redownload_count} 张损坏的图片"
                
                await self.send_text(result_msg)
                return True, result_msg, True
            
            elif target_type == "switch" and action_value:
                cards = self._check_cards(action_value)
                if cards:
                    self.set_card(action_value)
                    await self.send_text(f"已更换当前牌组为{action_value}")
                    return True, f"成功更换使用牌组至{action_value}", True
                else:
                    await self.send_text(f"{action_value}并不在当前可用牌组里")
                    return False, f"{action_value}并不在当前可用牌组里", True

            else:
                await self.send_text("没有这种参数，只能填cache或者switch哦")
                return False, "没有这种参数", True

        except Exception as e:
            await self.send_text(f"{self.log_prefix} 命令执行错误: {e}")
            logger.error(f"{self.log_prefix} 命令执行错误: {e}")
            return False, f"执行失败: {str(e)}", True
        
    def _check_person_permission(self, user_id: str) -> bool:
        """权限检查逻辑"""
        admin_users = self.config["permissions"].get("admin_users", [])
        if not admin_users:
            logger.warning(f"{self.log_prefix} 未配置管理员用户列表")
            return False
        return user_id in admin_users

@register_plugin
class TarotsPlugin(BasePlugin):
    """塔罗牌插件
    - 支持多种牌阵抽取
    - 支持区分大小阿卡纳抽取
    - 会在本地逐步缓存牌面图片
    - 拥有一键缓存所有牌面的指令
    - 完整的错误处理
    - 日志记录和监控
    """

    # 插件基本信息
    plugin_name = "tarots_plugin"
    enable_plugin = True
    config_file_name = "config.toml"
    dependencies = []
    python_dependencies = []

    # 配置节描述
    config_section_descriptions = {
        "plugin": "插件基本配置",
        "components": "组件启用控制",
        "proxy": "代理设置（支持热重载）",
        "cards": "牌组相关设置（支持热重载）",
        "adjustment": "功能微调向（支持热重载）",
        "permissions": "管理者用户配置（支持热重载）",
        "logging": "日志记录配置",
    }

    # 配置Schema定义
    config_schema = {
        "plugin": {
            "config_version": ConfigField(type=str, default="1.4.6", description="插件配置文件版本号"),
            "enabled": ConfigField(type=bool, default=True, description="是否启用插件"),
        },
        "components": {
            "enable_tarots": ConfigField(type=bool, default=True, description="是否启用塔罗牌插件抽牌功能"),
            "enable_tarots_command": ConfigField(type=bool, default=True, description="是否启用塔罗牌指令功能")
        },
        "proxy":{
            "enable_proxy": ConfigField(type=bool, default=False, description="是否启用代理功能"),
            "proxy_url": ConfigField(type=str, default="", description="请在双引号中填入你要使用的代理地址")
        },
        "cards":{
            "using_cards": ConfigField(type=str, default='bilibili', description="塔罗牌插件使用哪套牌组"),
            "use_cards": ConfigField(type=List, default=['bilibili','east'], description="塔罗牌插件可用的牌组，目前默认有'bilibili'，'east'两套默认牌组可选")
        },
        "adjustment":{
            "enable_original_text": ConfigField(type=bool, default=False, description="是否启用塔罗牌原始文本，开启该功能可以额外发出初始的解牌文本")
        },
        "permissions": {
            "admin_users": ConfigField(type=List, default=["123456789"], description="请写入被许可用户的QQ号，记得用英文单引号包裹并使用逗号分隔。这个配置会决定谁被允许使用塔罗牌指令，注意，这个选项支持热重载（你可以不重启麦麦，改动会即刻生效）"),
        },
        "logging": {
            "level": ConfigField(
                type=str, default="INFO", description="日志级别", choices=["DEBUG", "INFO", "WARNING", "ERROR"]
            ),
            "prefix": ConfigField(type=str, default="[Tarots]", description="日志前缀"),
        },
    }

    def get_plugin_components(self) -> List[Tuple[ComponentInfo, Type]]:
        """返回插件包含的组件列表"""

        components = []

        if self.get_config("components.enable_tarots", True):
            components.append((TarotsAction.get_action_info(), TarotsAction))

        if self.get_config("components.enable_tarots_command", True):
            components.append((TarotsCommand.get_command_info(), TarotsCommand))

        return components