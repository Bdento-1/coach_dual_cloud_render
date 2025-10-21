from flask import Flask, request, jsonify
import os, time, base64, hashlib
from openai import OpenAI

app = Flask(__name__)

# ===== ENV =====
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_ORG = os.getenv("OPENAI_ORG")
OPENAI_TIMEOUT = int(os.getenv("OPENAI_TIMEOUT", "70"))
OPENAI_RETRIES = int(os.getenv("OPENAI_MAX_RETRIES", "3"))
WEBHOOK_TOKEN = os.getenv("WEBHOOK_TOKEN", "kunthan-voice-01")

if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY is missing.")

client_kwargs = {"api_key": OPENAI_API_KEY}
if OPENAI_ORG:
    client_kwargs["organization"] = OPENAI_ORG
client = OpenAI(**client_kwargs)

SYSTEM_PROMPT = (
    "You are an analytical trading assistant. "
    "Describe the market structure objectively using neutral tone. "
    "Do not provide buy/sell/hold advice. "
    "Respond in Thai. Add disclaimer: 'ข้อมูลนี้เพื่อการศึกษาเท่านั้น.'"
)

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

def coach_text(symbol, tf, close, volume):
    user = (
        f"วิเคราะห์หุ้น {symbol} บนกรอบเวลา {tf} "
        f"ราคาปิด {close} ปริมาณ {volume}. "
        "อธิบายโครงสร้างราคาและแรงซื้อขายอย่างเป็นกลาง"
    )
    try:
        txt = ask_gpt(
            [{"role": "system", "content": SYSTEM_PROMPT},
             {"role": "user", "content": user}]
        )
    except Exception:
        txt = (
            f"สรุปข้อมูลล่าสุด ({tf}) — ปิด {close}, ปริมาณ {volume}. "
            "ระบบหลักช้า ใช้ข้อความย่อชั่วคราว. ข้อมูลนี้เพื่อการศึกษาเท่านั้น."
        )
    banned = ("buy","sell","entry","exit","long","short","tp","sl")
    if any(b in txt.lower() for b in banned):
        txt = "ข้อมูลนี้เพื่อการศึกษาเท่านั้น ไม่ใช่คำแนะนำทางการเงิน."
    return txt

def tts_alloy(text: str):
    """เรียก TTS และส่งกลับเป็น base64 (mp3)"""
    try:
        r = client.audio.speech.create(
            model=os.getenv("TTS_MODEL", "gpt-4o-mini-tts"),
            voice=os.getenv("VOICE", "alloy"),
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
        # ถ้าเสียงล้ม เหลืออย่างน้อยเป็นข้อความ
        return {"ok": False, "text": text, "error": f"tts_failed: {e}"}

@app.route("/coach_dual", methods=["POST"])
def coach_dual():
    try:
        token = request.args.get("token", "")
        if token != WEBHOOK_TOKEN:
            return jsonify({"ok": False, "error": "unauthorized"}), 403

        data = request.get_json(force=True) or {}
        symbol = data.get("symbol", "?")
        tf     = data.get("tf", "?")
        close  = data.get("close", "?")
        volume = data.get("volume", "?")

        safety_id = hashlib.sha256(
            f"{symbol}{tf}{close}{volume}".encode()
        ).hexdigest()[:16]

        text = data.get("text") or coach_text(symbol, tf, close, volume)
        tts = tts_alloy(text)
        tts["safety_id"] = safety_id
        # ถ้า TTS พัง ให้ส่งเฉพาะข้อความ (ok=false จะได้เห็น error ใน log)
        return jsonify(tts), 200 if tts.get("ok") else 200
    except Exception as e:
        return jsonify({"ok": False, "error": f"handler_failed: {e}"}), 500

@app.route("/healthz")
def healthz():
    return jsonify({
        "ok": True, "model": "gpt-5", "voice": os.getenv("VOICE", "alloy"),
        "retries": OPENAI_RETRIES, "timeout": OPENAI_TIMEOUT
    })

if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
