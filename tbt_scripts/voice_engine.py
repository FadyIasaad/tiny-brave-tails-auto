import asyncio
import edge_tts
from tbt_config import AUDIO_DIR

def _ssml_safe_pause_text(text: str) -> str:
    text = text.replace(". ", ". <break time='500ms'/> ")
    text = text.replace("? ", "? <break time='500ms'/> ")
    text = text.replace("! ", "! <break time='500ms'/> ")
    text = text.replace(", ", ", <break time='200ms'/> ")
    return text

async def _save_voice(text: str, voice: str, output_path: str):
    paced = _ssml_safe_pause_text(text)
    # Wrap in SSML
    ssml = f"<speak version='1.0' xmlns='http://www.w3.org/2001/10/synthesis' xml:lang='en-US'><voice name='{voice}'>{paced}</voice></speak>"
    communicate = edge_tts.Communicate(ssml, voice)
    await communicate.save(output_path)

def generate_voice(narration: str, voice: str, video_type: str) -> str:
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    output_path = AUDIO_DIR / f"{video_type}_voice.mp3"
    asyncio.run(_save_voice(narration, voice, str(output_path)))
    return str(output_path)
