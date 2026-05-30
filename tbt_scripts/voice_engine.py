import asyncio
import edge_tts
from tbt_config import AUDIO_DIR

def _ssml_safe_pause_text(text: str) -> str:
    # Improved pacing: add natural pauses after punctuation for better quality.
    text = text.replace(". ", ". <break time='500ms'/> ")
    text = text.replace("? ", "? <break time='500ms'/> ")
    text = text.replace("! ", "! <break time='500ms'/> ")
    text = text.replace(", ", ", <break time='200ms'/> ")
    return text

async def _save_voice(text: str, voice: str, output_path: str):
    paced = _ssml_safe_pause_text(text)
    # Wrap in SSML to make breaks work and prevent tags from being read aloud
    ssml = f"<speak version='1.0' xmlns='http://www.w3.org/2001/10/synthesis' xml:lang='en-US'><voice name='{voice}'>{paced}</voice></speak>"
    max_retries = 3
    for attempt in range(max_retries):
        try:
            communicate = edge_tts.Communicate(ssml, voice)
            await communicate.save(output_path)
            return
        except Exception as e:
            if attempt == max_retries - 1:
                raise e
            wait = (attempt + 1) * 2
            print(f"Voice generation failed (attempt {attempt+1}): {e}. Retrying in {wait}s...")
            await asyncio.sleep(wait)

def generate_voice(narration: str, voice: str, video_type: str) -> str:
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    output_path = AUDIO_DIR / f"{video_type}_voice.mp3"
    asyncio.run(_save_voice(narration, voice, str(output_path)))
    return str(output_path)
