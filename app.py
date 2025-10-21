from openai import OpenAI
import os
from flask import Flask, request, jsonify

app = Flask(__name__)

# ==== ENV ====
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_ORG = os.getenv("OPENAI_ORG")  # อาจไม่มีได้
OPENAI_HTTP_TIMEOUT = int(os.getenv("OPENAI_HTTP_TIMEOUT", "70"))
OPENAI_MAX_RETRIES = int(os.getenv("OPENAI_MAX_RETRIES", "3"))
WEBHOOK_TOKEN = os.getenv("WEBHOOK_TOKEN", "kunthan-voice-01")

if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY is missing. Set it in Render Environment.")

# สร้าง client แบบปลอดภัย
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
    last_err = None
    for i in range(OPENAI_MAX_RETRIES):
        try:
            resp = client.chat.completions.create(
                model="gpt-5",
                messages=messages,
                timeout=OPENAI_HTTP_TIMEOUT,  # สำคัญ
            )
            return resp.choices[0].message.content
        except Exception as e:
            last_err = e
            time.sleep(wait)
            wait *= 2
    raise last_err

def coach_text(symbol, tf, close, volume):
    user_prompt = (
        f"วิเคราะห์หุ้น {symbol} บนกรอบเวลา {tf} "
        f"ราคาปิด {close} ปริมาณ {volume}. "
        "อธิบายโครงสร้างราคาและแรงซื้อขายอย่างเป็นกลาง"
    )
    try:
        text = ask_gpt([
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt}
        ])
    except Exception as e:
        # Fallback เมื่อ GPT ช้า/ล่ม
        text = (
            f"ระบบวิเคราะห์ช้าชั่วคราว: {symbol} {tf} ราคาปิด {close}. "
            "ข้อมูลนี้เพื่อการศึกษาเท่านั้น."
        )
    # กันถ้อยคำที่สื่อคำแนะนำทางการเงิน
    banned = ("buy","sell","entry","exit","long","short","tp","sl")
    if any(b in text.lower() for b in banned):
        text = "ข้อมูลนี้เพื่อการศึกษาเท่านั้น ไม่ใช่คำแนะนำทางการเงิน."
    return text

def tts_alloy(text):
    # ใช้เสียง alloy (OpenAI TTS) เป็นดีฟอลต์บน Render
    try:
        r = client.audio.speech.create(
            model="gpt-4o-mini-tts",
            voice="alloy",
            input=text,
        )
        audio_bytes = r.read()
        audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")
        return {"ok": True, "audio_b64": audio_b64, "text": text, "audio_mime": "audio/mpeg"}
    except Exception as e:
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
        result = tts_alloy(text)
        result["safety_id"] = safety_id
        return jsonify(result), 200 if result.get("ok") else 500

    except Exception as e:
        return jsonify({"ok": False, "error": f"handler_failed: {e}"}), 500

@app.route("/healthz")
def healthz():
    return jsonify({
        "ok": True,
        "model": "gpt-5",
        "voice": "alloy",
        "timeout": OPENAI_HTTP_TIMEOUT,
        "retries": OPENAI_MAX_RETRIES
    })

if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
