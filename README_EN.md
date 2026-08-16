# DeskFlow

<div align="center">

[![](https://img.shields.io/badge/-简体中文-555555?style=flat)](https://github.com/LisseldeE/DeskFlow/blob/main/README.md) [![](https://img.shields.io/badge/-English-3b82f6?style=flat)](https://github.com/LisseldeE/DeskFlow/blob/main/README_EN.md)

</div>

## Project Introduction

DeskFlow is a PySide6-based Windows desktop quick toolbar. Use the global hotkey `Ctrl + `` to summon a floating capsule, providing quick access to screenshot, translation, annotation, and other daily office tools at your fingertips.

## Project Information

- **Project Name**: DeskFlow
- **Project Author**: Lisselde_E
- **Project Homepage**: https://lisseldee.github.io/#7
- **Project Repository**: https://github.com/LisseldeE/DeskFlow

## Usage

### Quick Actions
- **Global Hotkey**: `Ctrl + `` to show/hide the floating capsule panel
- **Screenshot Tool**: Quickly capture screen regions with preview, save, and copy support
- **Annotation Tool**: Annotate screenshots with rectangles, freeform shapes, and text
- **Settings Panel**: Language switching, hotkey display, and auto-start configuration

### Capsule Panel
- **Floating Capsule**: Clean capsule-shaped floating panel, adapts to system theme (dark/light mode)
- **SVG Icons**: Crisp vector icons with dynamic hover color changes
- **Smooth Animation**: Position and opacity transition animations for show/hide
- **Smart Dismiss**: Click outside or press ESC to auto-hide
- **Always on Top**: Panel stays above all windows

### Screenshot Features
- **Area Selection**: Drag to select any screen region from any direction
- **Live Preview**: Instant preview after capture before saving
- **Multiple Actions**: Save as file or copy to clipboard

### Annotation Features
- **Rectangle Annotation**: Draw rectangles on screenshots, displays original content without overlay
- **Freeform Annotation**: Freehand drawing for annotation areas
- **Text Annotation**: Add text labels to screenshots
- **Secondary Toolbar**: Capsule-style secondary toolbar with close support

### Settings Features
- **Language Switching**: Toggle between Chinese and English, takes effect immediately without restart
- **Hotkey Display**: View current global hotkey binding
- **Auto-start**: Set to launch on system boot, persisted to configuration

## Changelog

See [Changelog](https://github.com/LisseldeE/DeskFlow/blob/main/CHANGELOG.md)

## Tech Stack

- **Python 3.x**: Core development language
- **PySide6**: Qt6 Python bindings, GUI framework
- **Win32 API**: Global hotkey registration, system event monitoring
- **SVG**: Vector icon rendering
- **JSON**: Configuration file persistence

## Installation & Running

### System Requirements
- Windows 10 or later (64-bit)
- Python 3.10+
- PySide6 6.x

### Install Dependencies

```bash
pip install PySide6
```

### Run the Application

```bash
python DeskFlow.py
```

## Project Structure

```
DeskFlow/
├── DeskFlow.py              # Main entry point
├── icon.ico                 # Application icon
├── README.md                # Chinese documentation
├── README_EN.md             # English documentation
├── modules/
│   ├── capsule.py           # Floating capsule panel
│   ├── screenshot.py        # Screenshot functionality
│   ├── annotation.py        # Annotation functionality
│   ├── settings.py          # Settings dialog
│   ├── config.py            # Configuration management
│   ├── i18n.py              # Internationalization
│   ├── hotkey.py            # Global hotkey
│   └── icons.py             # SVG icons
```

## Configuration

Configuration file is stored at `DeskFlow/config.json` under the user home directory, with the following settings:

- `language`: Interface language (zh_CN / en)
- `autostart`: Auto-start on boot (true / false)

## License

This project is licensed under the MIT License. See [LICENSE](https://github.com/LisseldeE/DeskFlow/blob/main/LICENSE) file for details.

## Feedback

**This application is under development. If you have any questions or new ideas, feel free to contact me!**

Issues and Pull Requests are welcome!