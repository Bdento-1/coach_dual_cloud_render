from flask import Flask, request, jsonify
import os, time, base64, hashlib
from openai import OpenAI

app = Flask(__name__)

# ===== ENVIRONMENT =====
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_ORG = os.getenv("OPENAI_ORG")
OPENAI_TIMEOUT = int(os.getenv("OPENAI_TIMEOUT", "90"))
OPENAI_RETRIES = int(os.getenv("OPENAI_MAX_RETRIES", "4"))
WEBHOOK_TOKEN = os.getenv("WEBHOOK_TOKEN", "kunthan-voice-01")

# TTS configuration
TTS_MODEL = os.getenv("TTS_MODEL", "gpt-4o-mini-tts")
VOICE = os.getenv("VOICE", "nova")
VOICE_RATE = os.getenv("VOICE_RATE", "0.88")
VOICE_PITCH = os.getenv("VOICE_PITCH", "-1")
VOICE_PAUSE_MS = os.getenv("VOICE_PAUSE_MS", "400")

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

    # 🔒 ตัดความยาวไม่เกิน 1200 ตัวอักษร (กัน TTS ล่ม)
    if len(txt) > 1200:
        txt = txt[:1200] + "…"

    banned = ("buy", "sell", "entry", "exit", "long", "short", "tp", "sl")
    if any(b in txt.lower() for b in banned):
        txt = "ข้อมูลนี้เพื่อการศึกษาเท่านั้น ไม่ใช่คำแนะนำทางการเงิน."
    return txt

# ===== TTS FUNCTION =====
def tts_generate(text):
    """สร้างเสียงจากข้อความ"""
    try:
        r = client.audio.speech.create(
            model=TTS_MODEL,
            voice=VOICE,
            input=text,
        )
        audio_bytes = r.read()
        return {
            "ok": True,
            "text": text,
            "audio_b64": base64.b64encode(audio_bytes).decode("utf-8"),
            "audio_mime": "audio/mpeg",
        }
    except Exception as e:
        return {"ok": False, "text": text, "error": f"tts_failed: {e}"}

# ===== ROUTES =====
@app.route("/coach_dual", methods=["POST"])
def coach_dual():
    try:
        token = request.args.get("token", "")
        if token != WEBHOOK_TOKEN:
            return jsonify({"ok": False, "error": "unauthorized"}), 403

        data = request.get_json(force=True) or {}
        symbol = data.get("symbol", "?")
        tf = data.get("tf", "?")
        close = data.get("close", "?")
        volume = data.get("volume", "?")
        hint = data.get("hint")

        safety_id = hashlib.sha256(f"{symbol}{tf}{close}{volume}".encode()).hexdigest()[:16]

        text = data.get("text") or coach_text(symbol, tf, close, volume, hint)
        tts = tts_generate(text)
        tts["safety_id"] = safety_id
        tts["voice"] = VOICE
        return jsonify(tts), 200
    except Exception as e:
        return jsonify({"ok": False, "error": f"handler_failed: {e}"}), 500

@app.route("/healthz")
def healthz():
    return jsonify({
        "ok": True,
        "model": "gpt-5",
        "tts_model": TTS_MODEL,
        "voice": VOICE,
        "retries": OPENAI_RETRIES,
        "timeout": OPENAI_TIMEOUT
    })
@app.route("/speak", methods=["POST"])
def speak():
    """TTS endpoint ทดสอบเสียง nova"""
    try:
        data = request.get_json(force=True) or {}
        text = data.get("text", "ทดสอบเสียง nova ผ่านโดเมนถาวร")
        voice = data.get("voice", VOICE)

        r = client.audio.speech.create(
            model=TTS_MODEL,
            voice=voice,
            input=text,
        )
        audio_bytes = r.read()

        # ส่งกลับเป็นเสียง mp3 โดยตรง
        return Response(audio_bytes, mimetype="audio/mpeg")
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

# ===== MAIN =====
if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
