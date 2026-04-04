#!/bin/bash
# ============================================================
#  Font Switch Reminder — ติดตั้งและรันบน iMac
#  วิธีใช้: เปิด Terminal แล้วรัน  bash install.sh
# ============================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="python3"
APP="$SCRIPT_DIR/font_reminder.py"
PLIST="$HOME/Library/LaunchAgents/com.fontswitch.reminder.plist"

echo ""
echo "📦 ติดตั้ง rumps..."
$PYTHON -m pip install --quiet --upgrade rumps

echo ""
echo "🧪 ทดสอบ import..."
$PYTHON -c "import rumps; print('✅ rumps พร้อมใช้งาน')"

echo ""
echo "🚀 รัน Font Switch Reminder..."
echo "   → ดูที่ menu bar มุมขวาบน (🇺🇸 หรือ 🇹🇭)"
echo "   → กด Ctrl+C เพื่อหยุด"
echo ""

# ถาม user ว่าต้องการเพิ่มใน Login Items หรือไม่
read -p "➡️  ต้องการให้เริ่มอัตโนมัติทุกครั้งที่เปิดเครื่องไหม? (y/n): " AUTO_START

if [[ "$AUTO_START" == "y" || "$AUTO_START" == "Y" ]]; then
    echo ""
    echo "📌 เพิ่ม LaunchAgent..."
    cat > "$PLIST" << PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.fontswitch.reminder</string>
    <key>ProgramArguments</key>
    <array>
        <string>$(which python3)</string>
        <string>$APP</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>$HOME/Library/Logs/font_reminder.log</string>
    <key>StandardErrorPath</key>
    <string>$HOME/Library/Logs/font_reminder.log</string>
</dict>
</plist>
PLIST_EOF

    launchctl load "$PLIST" 2>/dev/null || true
    echo "✅ เพิ่มแล้ว! จะเริ่มอัตโนมัติทุกครั้งที่เปิดเครื่อง"
    echo "   หากต้องการถอดออก: launchctl unload $PLIST && rm $PLIST"
fi

echo ""
$PYTHON "$APP"
