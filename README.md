# DeskFlow

<div align="center">

[![](https://img.shields.io/badge/-简体中文-3b82f6?style=flat)](https://github.com/LisseldeE/DeskFlow/blob/main/README.md) [![](https://img.shields.io/badge/-English-555555?style=flat)](https://github.com/LisseldeE/DeskFlow/blob/main/README_EN.md)

</div>

## 项目简介

DeskFlow 是一个基于 PySide6 的 Windows 桌面快捷工具栏。全局热键 `Ctrl + `` 唤起悬浮胶囊，截图、翻译、标注等日常办公所需，一触即达。

## 项目信息

- **项目名称**: DeskFlow
- **项目作者**: Lisselde_E
- **项目主页**: https://lisseldee.github.io/#7
- **项目仓库**: https://github.com/LisseldeE/DeskFlow

## 使用方法

### 快捷操作
- **全局快捷键**：`Ctrl + `` 一键唤出/隐藏悬浮胶囊面板
- **截屏工具**：快速截取屏幕区域，支持预览、保存、复制
- **标注工具**：对截屏内容进行矩形、自由形状、文字标注
- **设置面板**：语言切换、快捷键查看、开机自启设置

### 胶囊面板
- **悬浮胶囊**：简洁的胶囊形悬浮面板，跟随系统主题（深色/浅色模式自适应）
- **SVG 图标**：清晰的矢量图标，动态悬停颜色变化
- **平滑动画**：弹出/收起带有位置和透明度过渡动画
- **智能隐藏**：点击空白区域或按 ESC 键自动收起
- **置顶显示**：面板始终置顶于所有窗口之上

### 截屏功能
- **区域选择**：支持从任意方向框选截屏区域
- **实时预览**：截屏后即时预览，确认后再保存
- **多种操作**：支持保存为文件、复制到剪贴板

### 标注功能
- **矩形标注**：在截屏上绘制矩形框，显示原始内容无遮罩
- **自由形状**：自由绘制标注区域
- **文字标注**：在截屏上添加文字说明
- **二级工具栏**：标注工具胶囊样式二级栏，支持关闭

### 设置功能
- **语言切换**：中文/英文界面切换，实时生效无需重启
- **快捷键查看**：查看当前全局快捷键绑定
- **开机自启**：设置开机自动启动，状态持久化保存

## 更新日志

详见 [更新日志](https://github.com/LisseldeE/DeskFlow/blob/main/CHANGELOG.md)

## 技术栈

- **Python 3.x**：核心开发语言
- **PySide6**：Qt6 Python 绑定，GUI 框架
- **Win32 API**：全局快捷键注册、系统事件监听
- **SVG**：矢量图标渲染
- **JSON**：配置文件持久化

## 安装与运行

### 系统要求
- Windows 10 或更高版本（64位）
- Python 3.10+
- PySide6 6.x

### 安装依赖

```bash
pip install PySide6
```

### 运行程序

```bash
python DeskFlow.py
```

## 项目结构

```
DeskFlow/
├── DeskFlow.py              # 主入口
├── icon.ico                 # 程序图标
├── README.md                # 中文说明
├── README_EN.md             # 英文说明
├── modules/
│   ├── capsule.py           # 悬浮胶囊面板
│   ├── screenshot.py        # 截屏功能
│   ├── annotation.py        # 标注功能
│   ├── settings.py          # 设置对话框
│   ├── config.py            # 配置文件管理
│   ├── i18n.py              # 国际化支持
│   ├── hotkey.py            # 全局快捷键
│   └── icons.py             # SVG 图标
```

## 配置

配置文件存储在用户目录下的 `DeskFlow/config.json`，包含以下设置：

- `language`：界面语言（zh_CN / en）
- `autostart`：开机自启（true / false）

## 开源声明

本项目采用 MIT 开源协议，详见 [LICENSE](https://github.com/LisseldeE/DeskFlow/blob/main/LICENSE) 文件。

## 反馈

**开发中应用，如有问题或新的创意欢迎和我联系！**

欢迎提交 Issue 和 Pull Request！
