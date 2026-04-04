#!/bin/bash
# ============================================================
#  Font Switch Reminder — ติดตั้งบน iMac
#  รัน: bash install.sh
# ============================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP="$SCRIPT_DIR/font_reminder.py"
VENV="$SCRIPT_DIR/venv"

echo ""
echo "========================================"
echo "  Font Switch Reminder — ติดตั้ง"
echo "========================================"
echo ""

# 1. สร้าง venv ถ้ายังไม่มี
if [ ! -d "$VENV" ]; then
    echo "📦 สร้าง virtual environment..."
    python3 -m venv "$VENV"
fi

# 2. ติดตั้ง dependencies
echo "📦 ติดตั้ง packages..."
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet rumps pyobjc-framework-Cocoa pyobjc-framework-Quartz
echo "✅ ติดตั้ง packages เสร็จ"

# 3. แจ้งเรื่อง Accessibility
echo ""
echo "========================================"
echo "  ⚠️  ต้องให้สิทธิ์ Accessibility ก่อน"
echo "========================================"
echo ""
echo "  1. เปิด System Settings"
echo "  2. Privacy & Security → Accessibility"
echo "  3. กด + แล้วเพิ่ม Terminal.app"
echo "     (หรือ app ที่ใช้รัน script นี้)"
echo "  4. เปิด toggle ให้เป็นสีเขียว"
echo ""
echo "  ถ้าไม่ให้สิทธิ์ → ตรวจจับ garbage จะไม่ทำงาน"
echo "  (เสียงตอนสลับภาษา + timer จะยังทำงานได้)"
echo ""
read -p "➡️  ให้สิทธิ์แล้ว กด Enter เพื่อเริ่มแอพ..."

# 4. ถามเรื่อง auto-start
echo ""
read -p "➡️  ต้องการให้เริ่มอัตโนมัติทุกครั้งที่เปิดเครื่องไหม? (y/n): " AUTO

if [[ "$AUTO" == "y" || "$AUTO" == "Y" ]]; then
    PLIST="$HOME/Library/LaunchAgents/com.fontswitch.reminder.plist"
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
        <string>$VENV/bin/python3</string>
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
    echo "✅ เพิ่มแล้ว จะเริ่มอัตโนมัติทุกครั้งที่เปิดเครื่อง"
fi

echo ""
echo "🚀 เริ่ม Font Switch Reminder..."
echo "   → ดูที่ menu bar มุมขวาบน (🇺🇸 / 🇹🇭)"
echo "   → กด Ctrl+C เพื่อหยุด"
echo ""
"$VENV/bin/python3" "$APP"
