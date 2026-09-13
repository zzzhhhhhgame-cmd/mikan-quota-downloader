#!/bin/bash
# 构建 macOS 应用包：把当前 venv + 桌面入口包装成 双击即开的 .app（无需终端）。
# 用法：bash scripts/make_app.sh
set -e
cd "$(dirname "$0")/.."
REPO="$(pwd -P)"
APP_NAME="Anime Downloader"
APP="$REPO/$APP_NAME.app"
PYTHON="$REPO/.venv/bin/python"

[ -x "$PYTHON" ] || { echo "缺少虚拟环境，请先按 USAGE.md §1.2 安装"; exit 1; }
command -v iconutil >/dev/null || { echo "需要 macOS 的 iconutil"; exit 1; }

# 1) 图标
mkdir -p "$REPO/build"
"$PYTHON" "$REPO/scripts/make_icon.py" "$REPO/build/icon.png"
mkdir -p "$REPO/build/icon.iconset"
for s in 16 32 128 256 512; do
  sips -z "$s" "$s" "$REPO/build/icon.png" --out "$REPO/build/icon.iconset/icon_${s}x${s}.png" >/dev/null
  sips -z $((s*2)) $((s*2)) "$REPO/build/icon.png" --out "$REPO/build/icon.iconset/icon_${s}x${s}@2x.png" >/dev/null
done

# 2) 包结构
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
iconutil -c icns "$REPO/build/icon.iconset" -o "$APP/Contents/Resources/AppIcon.icns"

# 3) Info.plist
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>$APP_NAME</string>
  <key>CFBundleDisplayName</key><string>$APP_NAME</string>
  <key>CFBundleIdentifier</key><string>cc.anime.downloader</string>
  <key>CFBundleVersion</key><string>0.2.0</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleExecutable</key><string>launcher</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>LSMinimumSystemVersion</key><string>12.0</string>
</dict>
</plist>
PLIST

# 4) 启动器：切到仓库目录（相对路径的 data/ 才正确），静默后台运行
#    日志放 ~/Library/Logs（不受 TCC 保护；若放 ~/Documents，未授权时启动会静默失败）
cat > "$APP/Contents/MacOS/launcher" <<LAUNCH
#!/bin/bash
REPO="$REPO"
LOGDIR="\$HOME/Library/Logs/AnimeDownloader"
mkdir -p "\$LOGDIR" "\$REPO/data"
cd "\$REPO"
exec "\$REPO/.venv/bin/python" "\$REPO/desktop/app.py" --config "\$REPO/config.yaml" >> "\$LOGDIR/app-gui.log" 2>&1
LAUNCH
chmod +x "$APP/Contents/MacOS/launcher"

# 5) 安装：优先安装到 /Applications（系统设置/启动台/Spotlight 只会显示这一份）
#    并删除仓库目录里的构建副本，避免出现两个入口
if [ -d "/Applications" ] && [ -w "/Applications" ]; then
  rm -rf "/Applications/$APP_NAME.app"
  cp -R "$APP" "/Applications/$APP_NAME.app"
  rm -rf "$APP"
  APP="/Applications/$APP_NAME.app"
  /System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister \
    -f "$APP" 2>/dev/null || true
  echo "✅ 已安装: $APP"
else
  echo "✅ 已构建: $APP（未自动安装到 /Applications）"
fi
echo "   双击即可打开；也可拖到程序坞。更新代码后重新运行本脚本即可。"
