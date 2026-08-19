import locale
from modules.config import Config

TRANSLATIONS = {
    "zh_CN": {
        "screenshot": "截屏",
        "annotation": "标注",
        "settings": "设置",
        "translate": "翻译",
        "close": "关闭",
        "save": "保存",
        "copy": "复制",
        "save_as": "另存为",
        "png_files": "PNG 图片",
        "screenshot_preview": "截屏预览",
        "rectangle": "矩形",
        "freeform": "自由形状",
        "text": "文字框",
        "delete": "删除",
        "color": "颜色",
        "show": "显示",
        "exit": "退出",
        "app_name": "DeskFlow",
        "language": "语言",
        "hotkey": "快捷键",
        "settings_title": "设置",
        "autostart": "开机自启",
        # Clipboard feature
        "clipboard": "剪切板",
        "clipboard_off": "局域网剪切板已关闭",
        "clipboard_on": "局域网剪切板已开启",
        "clipboard_empty": "暂无剪切板内容",
        "clipboard_clear_all": "清空全部",
        "clipboard_clear_confirm": "确定清空所有剪切板历史？",
        "clipboard_no_room": "未配置房间号",
        "clipboard_local": "本机",
        "clipboard_remote": "远程",
        "clipboard_peers": "在线设备",
        "clipboard_status_disconnected": "未连接",
        "clipboard_status_connecting": "连接中…",
        "clipboard_status_scanning": "扫描房间中…",
        "clipboard_status_hosting": "已作为主机",
        "clipboard_status_joined": "已加入房间",
        "clipboard_status_not_found": "未找到房间，已创建",
        "clipboard_status_failed": "连接失败",
        "clipboard_room_config_title": "房间配置",
        "clipboard_room_label": "房间号",
        "clipboard_room_hint": "输入 6 位数字",
        "clipboard_room_invalid": "房间号必须为 6 位数字",
        "clipboard_change_room": "修改房间号",
        "clipboard_enable": "开启剪切板",
        "clipboard_disable": "关闭剪切板",
        "clipboard_copied": "已复制",
        # Translate feature
        "translate_loading": "识别翻译中…",
        "translate_retry": "重试",
        "translate_no_text": "未识别到文字",
        "translate_failed": "翻译失败",
        "copied": "已复制",
        "translate_target_lang": "目标语言",
        "translate_source_hint": "识别到的文字将翻译为所选目标语言。翻译使用在线接口，需要网络连接。",
        # Settings sidebar
        "settings_general": "常规",
        "settings_translate": "翻译",
        "settings_system": "系统",
        "settings_about": "关于",
        # About / check for update
        "ok": "确定",
        "about_version_label": "版本",
        "about_author": "作者",
        "about_check_update": "检查更新",
        "about_no_tags": "未找到版本信息",
        "about_parse_error": "无法解析当前版本号",
        "about_new_version": "发现新版本 {version}，是否前往下载？",
        "about_latest": "已是最新版本",
        "about_yes": "是",
        "about_no": "否",
        "about_network_error": "网络连接失败：{error}",
        "about_check_failed": "检查更新失败：{error}",
    },
    "en": {
        "screenshot": "Screenshot",
        "annotation": "Annotate",
        "settings": "Settings",
        "translate": "Translate",
        "close": "Close",
        "save": "Save",
        "copy": "Copy",
        "save_as": "Save As",
        "png_files": "PNG Images",
        "screenshot_preview": "Screenshot Preview",
        "rectangle": "Rectangle",
        "freeform": "Freeform",
        "text": "Text",
        "delete": "Delete",
        "color": "Color",
        "show": "Show",
        "exit": "Exit",
        "app_name": "DeskFlow",
        "language": "Language",
        "hotkey": "Hotkey",
        "settings_title": "Settings",
        "autostart": "Auto-start",
        # Clipboard feature
        "clipboard": "Clipboard",
        "clipboard_off": "LAN clipboard disabled",
        "clipboard_on": "LAN clipboard enabled",
        "clipboard_empty": "No clipboard content",
        "clipboard_clear_all": "Clear all",
        "clipboard_clear_confirm": "Clear all clipboard history?",
        "clipboard_no_room": "No room configured",
        "clipboard_local": "Local",
        "clipboard_remote": "Remote",
        "clipboard_peers": "Peers",
        "clipboard_status_disconnected": "Disconnected",
        "clipboard_status_connecting": "Connecting…",
        "clipboard_status_scanning": "Scanning for room…",
        "clipboard_status_hosting": "Hosting",
        "clipboard_status_joined": "Joined room",
        "clipboard_status_not_found": "Room not found, created",
        "clipboard_status_failed": "Connection failed",
        "clipboard_room_config_title": "Room Configuration",
        "clipboard_room_label": "Room code",
        "clipboard_room_hint": "Enter 6 digits",
        "clipboard_room_invalid": "Room code must be 6 digits",
        "clipboard_change_room": "Change room code",
        "clipboard_enable": "Enable clipboard",
        "clipboard_disable": "Disable clipboard",
        "clipboard_copied": "Copied",
        # Translate feature
        "translate_loading": "Recognizing & translating…",
        "translate_retry": "Retry",
        "translate_no_text": "No text recognized",
        "translate_failed": "Translation failed",
        "copied": "Copied",
        "translate_target_lang": "Target language",
        "translate_source_hint": "Recognized text is translated to the chosen target language. Translation uses an online service and requires a network connection.",
        # Settings sidebar
        "settings_general": "General",
        "settings_translate": "Translate",
        "settings_system": "System",
        "settings_about": "About",
        # About / check for update
        "ok": "OK",
        "about_version_label": "Version",
        "about_author": "Author",
        "about_check_update": "Check for Updates",
        "about_no_tags": "No version tags found",
        "about_parse_error": "Cannot parse current version",
        "about_new_version": "New version {version} found. Download?",
        "about_latest": "Already the latest version",
        "about_yes": "Yes",
        "about_no": "No",
        "about_network_error": "Network error: {error}",
        "about_check_failed": "Update check failed: {error}",
    }
}


class I18n:
    _language = None

    @classmethod
    def get_language(cls):
        if cls._language is None:
            config = Config()
            saved = config.get("language")
            if saved:
                cls._language = saved
            else:
                try:
                    lang = locale.getdefaultlocale()[0]
                    cls._language = "zh_CN" if lang and lang.startswith("zh") else "en"
                except Exception:
                    cls._language = "en"
                config.set("language", cls._language)
        return cls._language

    @classmethod
    def set_language(cls, lang):
        cls._language = lang
        Config().set("language", lang)

    @classmethod
    def tr(cls, key, **kwargs):
        text = TRANSLATIONS.get(cls.get_language(), {}).get(key, key)
        if kwargs:
            try:
                return text.format(**kwargs)
            except (KeyError, IndexError):
                return text
        return text
