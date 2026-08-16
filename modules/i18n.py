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
        "show": "显示",
        "exit": "退出",
        "app_name": "DeskFlow",
        "language": "语言",
        "hotkey": "快捷键",
        "settings_title": "设置",
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
        "show": "Show",
        "exit": "Exit",
        "app_name": "DeskFlow",
        "language": "Language",
        "hotkey": "Hotkey",
        "settings_title": "Settings",
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
    def tr(cls, key):
        return TRANSLATIONS.get(cls.get_language(), {}).get(key, key)