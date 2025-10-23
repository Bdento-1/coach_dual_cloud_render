#!/usr/bin/env python3
from flask import Flask, request, jsonify, Response
import os, time, base64, hashlib, re
from openai import OpenAI

app = Flask(__name__)

# ===== ENVIRONMENT =====
OPENAI_API_KEY   = os.getenv("OPENAI_API_KEY", "")
OPENAI_ORG       = os.getenv("OPENAI_ORG")
OPENAI_TIMEOUT   = int(os.getenv("OPENAI_TIMEOUT", "90"))
OPENAI_RETRIES   = int(os.getenv("OPENAI_MAX_RETRIES", "4"))
WEBHOOK_TOKEN    = os.getenv("WEBHOOK_TOKEN", "kunthan-voice-01")

# TTS configuration (ค่า global เดิม ยังใช้ได้)
TTS_MODEL        = os.getenv("TTS_MODEL", "gpt-4o-mini-tts")
VOICE_DEFAULT    = os.getenv("VOICE", "nova")      # fallback ถ้าไม่ระบุอะไรเลย
VOICE_RATE       = os.getenv("VOICE_RATE", "0.92") # เผื่อ API รุ่นใหม่รองรับ
VOICE_PITCH      = os.getenv("VOICE_PITCH", "-0.5")
VOICE_PAUSE_MS   = os.getenv("VOICE_PAUSE_MS", "400")

# --- สองเสียงไทยพรีเมียม ---
COACH_VOICE      = os.getenv("COACH_VOICE", "nova")
GATE_VOICE       = os.getenv("GATE_VOICE",  "verse")
VOICE_LANGUAGE   = os.getenv("VOICE_LANGUAGE", "th")  # เผื่อ API รองรับ language ในอนาคต

if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY is missing.")

client_kwargs = {"api_key": OPENAI_API_KEY}
if OPENAI_ORG:
    client_kwargs["organization"] = OPENAI_ORG
client = OpenAI(**client_kwargs)

# ===== SYSTEM PROMPT =====
SYSTEM_PROMPT = (
    "คุณคือผู้ช่วยวิเคราะห์เชิงโครงสร้างราคาแบบเป็นกลาง (Gatekeeper v2.3.1 Hybrid-Auto). "
    "ให้สรุปภาษาไทยเท่านั้น, ไม่แนะนำซื้อขาย, และพูดด้วยน้ำเสียงธรรมชาติ "
    "ลงท้ายด้วยประโยค: 'ข้อมูลนี้เพื่อการศึกษาเท่านั้น ไม่ใช่คำแนะนำทางการเงิน.'"
)

# ===== Utilities =====
_digit_map = {'0':'ศูนย์','1':'หนึ่ง','2':'สอง','3':'สาม','4':'สี่','5':'ห้า','6':'หก','7':'เจ็ด','8':'แปด','9':'เก้า','.':'จุด'}
def _num_to_thai(s: str) -> str:
    """แปลงตัวเลขทศนิยมพื้นฐานให้เป็นคำไทยแบบทีละหลัก (เช่น 61.22 → หก หนึ่ง จุด สอง สอง)"""
    return ' '.join(_digit_map.get(ch, ch) for ch in s)

def normalize_thai_numbers(text: str) -> str:
    """แทนที่เลขเป็นคำไทย เพื่อบังคับ TTS อ่านไทย 100%"""
    return re.sub(r'\d+(\.\d+)?', lambda m: _num_to_thai(m.group(0)), text)

def pick_voice(payload: dict) -> str:
    """
    เลือกเสียงอัตโนมัติ:
    - ถ้า payload ระบุ "voice" มา → ใช้ตามนั้น
    - ถ้า role="gate" หรือ event มีคำว่า gate/trap/alert/stop/risk → ใช้ GATE_VOICE
    - นอกนั้นใช้ COACH_VOICE
    """
    explicit = (payload.get("voice") or "").strip().lower()
    if explicit:
        return explicit
    role = (payload.get("role") or "").strip().lower()
    event = str(payload.get("event") or "").lower()
    if role == "gate" or any(k in event for k in ["gate", "trap", "alert", "stop", "risk"]):
        return GATE_VOICE
    return COACH_VOICE

# ===== GPT Function =====
def ask_gpt(messages):
    wait = 0.5
    last = None
    for _ in range(OPENAI_RETRIES):
        try:
            r = client.chat.completions.create(
                model="gpt-5",
                messages=messages,
                timeout=OPENAI_TIMEOUT,
            )
            return r.choices[0].message.content
        except Exception as e:
            last = e
            time.sleep(wait)
            wait *= 2
    raise last

# ===== ANALYSIS FUNCTION =====
def coach_text(symbol, tf, close, volume, hint=None):
    user_prompt = (
        f"วิเคราะห์หุ้น {symbol} ในกรอบเวลา {tf} "
        f"ราคาปิด {close} ปริมาณ {volume}. "
        f"{hint or 'สรุปไทยล้วน 4–6 บรรทัด ไม่แนะนำซื้อขาย'}"
    )
    try:
        txt = ask_gpt(
            [{"role": "system", "content": SYSTEM_PROMPT},
             {"role": "user", "content": user_prompt}]
        )
    except Exception:
        txt = f"สรุปข้อมูล ({tf}) — ปิด {close}, ปริมาณ {volume}. ระบบหลักช้า ใช้ข้อความย่อแทน."

    # ตัดความยาวกัน TTS ล่ม
    if len(txt) > 1200:
        txt = txt[:1200] + "…"

    banned = ("buy", "sell", "entry", "exit", "long", "short", "tp", "sl")
    if any(b in txt.lower() for b in banned):
        txt = "ข้อมูลนี้เพื่อการศึกษาเท่านั้น ไม่ใช่คำแนะนำทางการเงิน."
    return txt

# ===== TTS FUNCTION =====
def tts_generate(text: str, voice: str):
    """สร้างเสียงจากข้อความ (แปลงตัวเลขเป็นไทยก่อน)"""
    try:
        text_th = normalize_thai_numbers(text)
        r = client.audio.speech.create(
            model=TTS_MODEL,
            voice=voice,
            input=text_th,
            # หมายเหตุ: ถ้าเวอร์ชัน API อนุญาต language/rate/pitch ให้เติมได้
            # language=VOICE_LANGUAGE, rate=VOICE_RATE, pitch=VOICE_PITCH
        )
        audio_bytes = r.read()
        return {
            "ok": True,
            "text": text_th,
            "voice": voice,
            "audio_b64": base64.b64encode(audio_bytes).decode("utf-8"),
            "audio_mime": "audio/mpeg",
        }
    except Exception as e:
        return {"ok": False, "text": text, "voice": voice, "error": f"tts_failed: {e}"}

# ===== ROUTES =====
@app.route("/coach_dual", methods=["POST"])
def coach_dual():
    try:
        token = request.args.get("token", "")
        if token != WEBHOOK_TOKEN:
            return jsonify({"ok": False, "error": "unauthorized"}), 403

        data   = request.get_json(force=True) or {}
        symbol = data.get("symbol", "?")
        tf     = data.get("tf", "?")
        close  = data.get("close", "?")
        volume = data.get("volume", "?")
        hint   = data.get("hint")

        safety_id = hashlib.sha256(f"{symbol}{tf}{close}{volume}".encode()).hexdigest()[:16]

        text  = data.get("text") or coach_text(symbol, tf, close, volume, hint)
        voice = pick_voice(data) or VOICE_DEFAULT
        tts   = tts_generate(text, voice)
        tts["safety_id"] = safety_id
        return jsonify(tts), 200
    except Exception as e:
        return jsonify({"ok": False, "error": f"handler_failed: {e}"}), 500

@app.route("/speak", methods=["POST"])
def speak():
    """TTS endpoint (ส่ง mp3 กลับโดยตรง)"""
    try:
        data  = request.get_json(force=True) or {}
        text  = data.get("text", "ระบบพร้อมสำหรับเสียงไทยพรีเมียม")
        voice = pick_voice(data) or VOICE_DEFAULT

        out = tts_generate(text, voice)
        if not out.get("ok"):
            return jsonify({"ok": False, "error": out.get("error","tts_failed")}), 500

        audio_bytes = base64.b64decode(out["audio_b64"])
        return Response(audio_bytes, mimetype="audio/mpeg")
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/healthz")
def healthz():
    return jsonify({
        "ok": True,
        "model": "gpt-5",
        "tts_model": TTS_MODEL,
        "voice_default": VOICE_DEFAULT,
        "coach_voice": COACH_VOICE,
        "gate_voice": GATE_VOICE,
        "lang": VOICE_LANGUAGE,
        "retries": OPENAI_RETRIES,
        "timeout": OPENAI_TIMEOUT
    })

# ===== MAIN =====
if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
