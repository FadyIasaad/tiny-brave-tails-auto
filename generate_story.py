import json
import os
import re
from datetime import datetime, timezone

import google.generativeai as genai

from tbt_common import (
    get_sheets_client,
    open_spreadsheet,
    get_worksheet,
    get_all_records,
    get_all_values,
    update_cell,
    find_column,
    find_optional_column,
    log,
    require_env,
    run_with_retry,
)

CONTENT_SHEET_NAME = os.getenv("CONTENT_SHEET_NAME", "Content").strip()
LOGS_SHEET_NAME = os.getenv("LOGS_SHEET_NAME", "Logs").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()

MODEL_CANDIDATES = [
    os.getenv("GEMINI_MODEL", "gemini-1.5-flash").strip(),
    "gemini-1.5-flash-lite",
    "gemini-2.0-flash-exp",
]
MODEL_CANDIDATES = list(dict.fromkeys([m for m in MODEL_CANDIDATES if m]))

REQUIRED_COLUMNS = [
    "id", "topic", "animal", "lesson", "script", "title", "description", "status",
    "video_url", "created_at", "scene_prompts", "image_status", "audio_status",
    "youtube_status", "youtube_video_id", "video_file_path", "error_message",
]

VALID_EMOTIONS = {"curious", "sad", "fear", "brave", "happy", "emotional", "worried", "lonely", "determined", "hopeful", "heartfelt"}
VALID_VOICE_STYLES = {
    "tiny_gentle", "tiny_brave", "soft_warm", "tender_sad", "urgent_fear",
    "deep_brave", "wise_slow", "bright_curious", "hopeful_warm",
    "happy_light", "narrator_emotional"
}


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def ensure_headers(sheet):
    values = get_all_values(sheet)
    if not values:
        update_cell(sheet, 1, 1, REQUIRED_COLUMNS[0])
        for idx, header in enumerate(REQUIRED_COLUMNS, start=1):
            update_cell(sheet, 1, idx, header)
        return REQUIRED_COLUMNS
    headers = [str(h).strip() for h in values[0]]
    missing = [h for h in REQUIRED_COLUMNS if h not in headers]
    if missing:
        raise RuntimeError(
            "Content sheet is missing required columns: " + ", ".join(missing) +
            "\nYour first row must contain: " + ", ".join(REQUIRED_COLUMNS)
        )
    return headers


def find_first_idea(records):
    for row_number, record in enumerate(records, start=2):
        if str(record.get("status", "")).strip().upper() == "IDEA" and str(record.get("topic", "")).strip():
            return row_number, record
    return None, None


def clean_json_text(text):
    if not text:
        raise RuntimeError("Gemini returned empty text.")
    cleaned = text.strip()
    # Remove markdown code blocks if present
    if "```" in cleaned:
        # Match from the first { to the last } inside code blocks or just anywhere
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            cleaned = match.group(0)
        else:
            cleaned = re.sub(r"^```json\s*", "", cleaned, flags=re.IGNORECASE)
            cleaned = re.sub(r"^```\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


def parse_json_response(text):
    """
    Parse Gemini JSON safely with improved extraction.
    """
    cleaned = clean_json_text(text)

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # Try finding the first { and last } if normal parsing fails
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError as e:
                raise RuntimeError(
                    f"Malformed JSON from Gemini even after extraction. "
                    f"JSON error: {e}. Preview: {cleaned[:500]}"
                )

        raise RuntimeError(
            "Could not find valid JSON object in Gemini response. "
            f"Preview: {cleaned[:500]}"
        )


def split_sentences(script):
    parts = re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", script).strip())
    parts = [p.strip() for p in parts if p.strip()]
    return parts or [script.strip()]


def infer_voice_style(animal, emotion):
    animal_text = str(animal or "").lower()
    emotion = str(emotion or "emotional").lower()

    small_animals = ("puppy", "kitten", "rabbit", "bunny", "bird", "sparrow", "mouse", "squirrel", "duckling", "cub", "chick")
    warm_animals = ("dog", "cat", "dolphin", "fox", "deer", "panda", "koala")
    deep_animals = ("lion", "wolf", "horse", "bear", "tiger", "eagle")
    wise_animals = ("elephant", "whale", "turtle", "owl")

    if any(word in animal_text for word in small_animals):
        if emotion in {"brave", "determined"}:
            return "tiny_brave"
        if emotion in {"curious", "happy"}:
            return "bright_curious"
        if emotion in {"sad", "lonely", "heartfelt"}:
            return "tender_sad"
        return "tiny_gentle"

    if any(word in animal_text for word in wise_animals):
        return "wise_slow"
    if any(word in animal_text for word in deep_animals):
        if emotion in {"sad", "lonely", "heartfelt"}:
            return "soft_warm"
        return "deep_brave"
    if any(word in animal_text for word in warm_animals):
        if emotion in {"sad", "lonely", "heartfelt"}:
            return "tender_sad"
        if emotion in {"brave", "determined"}:
            return "deep_brave"
        return "soft_warm"

    return {
        "curious": "bright_curious",
        "sad": "tender_sad",
        "fear": "urgent_fear",
        "brave": "deep_brave",
        "happy": "happy_light",
        "worried": "urgent_fear",
        "lonely": "tender_sad",
        "determined": "deep_brave",
        "hopeful": "hopeful_warm",
        "heartfelt": "soft_warm",
    }.get(emotion, "narrator_emotional")


def fallback_scene_payload(title, script, animal, lesson):
    sentences = split_sentences(script)
    scene_count = min(7, max(5, len(sentences)))
    chunks = []
    for i in range(scene_count):
        if i < len(sentences):
            chunks.append(sentences[i])
        else:
            chunks.append(sentences[-1])

    animal_clean = animal or "small animal"
    name = "Pip"
    character_desc = (
        f"A consistent cute {animal_clean} named {name}, expressive eyes, soft rounded 2D storybook style, "
        "same character in every scene, warm family-friendly design."
    )
    scenes = []
    emotion_order = ["curious", "worried", "lonely", "brave", "determined", "hopeful", "heartfelt"]
    for idx, narration in enumerate(chunks, start=1):
        emotion = emotion_order[min(idx - 1, len(emotion_order) - 1)]
        scenes.append({
            "scene_number": idx,
            "narration_en": narration,
            "subtitle_en": narration,
            "emotion": emotion,
            "voice_style": infer_voice_style(animal_clean, emotion),
            "image_prompt": (
                f"{character_desc} Scene {idx}: {narration}. Emotional cinematic lighting, "
                "vertical 9:16, no text, no watermark, no logo."
            ),
        })
    return {
        "character": {"name": name, "animal_type": animal_clean, "description": character_desc},
        "hook_text": chunks[0][:80],
        "comment_prompt": "What would you do?",
        "scenes": scenes,
    }


def normalize_scene_payload(raw_payload, title, script, animal, lesson):
    if isinstance(raw_payload, str) and raw_payload.strip():
        try:
            raw_payload = json.loads(raw_payload)
        except Exception:
            raw_payload = None

    if not isinstance(raw_payload, dict):
        return fallback_scene_payload(title, script, animal, lesson)

    payload = raw_payload
    if "scenes" not in payload or not isinstance(payload.get("scenes"), list) or len(payload.get("scenes", [])) < 3:
        return fallback_scene_payload(title, script, animal, lesson)

    character = payload.get("character") if isinstance(payload.get("character"), dict) else {}
    if not character.get("description"):
        character["description"] = f"A consistent cute {animal or 'animal'} character in warm 2D storybook style."
    if not character.get("name"):
        character["name"] = "Pip"
    if not character.get("animal_type"):
        character["animal_type"] = animal or "animal"

    scenes = []
    for idx, scene in enumerate(payload.get("scenes", []), start=1):
        if not isinstance(scene, dict):
            continue
        narration = str(scene.get("narration_en") or scene.get("subtitle_en") or "").strip()
        if not narration:
            continue
        emotion = str(scene.get("emotion") or "emotional").strip().lower()
        if emotion not in VALID_EMOTIONS:
            emotion = "emotional"
        voice_style = str(scene.get("voice_style") or "").strip().lower()
        if voice_style not in VALID_VOICE_STYLES:
            voice_style = infer_voice_style(character.get("animal_type") or animal, emotion)
        image_prompt = str(scene.get("image_prompt") or "").strip()
        if not image_prompt:
            image_prompt = f"{character['description']} Scene: {narration}. Vertical 9:16, no text, no watermark."
        scenes.append({
            "scene_number": len(scenes) + 1,
            "narration_en": narration,
            "subtitle_en": str(scene.get("subtitle_en") or narration).strip(),
            "emotion": emotion,
            "voice_style": voice_style,
            "image_prompt": image_prompt,
        })

    if len(scenes) < 3:
        return fallback_scene_payload(title, script, animal, lesson)

    return {
        "character": character,
        "hook_text": str(payload.get("hook_text") or scenes[0]["narration_en"]).strip()[:90],
        "comment_prompt": str(payload.get("comment_prompt") or "What would you do?").strip()[:80],
        "scenes": scenes[:8],
    }


def build_prompt(record):
    topic = str(record.get("topic", "")).strip()
    animal = str(record.get("animal", "")).strip()
    lesson = str(record.get("lesson", "")).strip()
    return f"""
You write for a YouTube Shorts channel called Tiny Brave Tails.

Channel promise:
Short emotional animal stories with simple life lessons for a global English-speaking audience.

Hard rules:
- English only.
- Family-friendly, not made only for kids.
- No gore, no violence, no religion, no politics.
- Do not claim the story is true.
- No copyrighted characters.
- Keep one consistent animal character across all scenes.
- Match the voice style to the animal character and scene emotion.
- Make the first sentence a strong hook.
- The story must feel emotional, cinematic, and simple.

Voice style options:
- tiny_gentle: tiny animals, soft innocent moments
- tiny_brave: tiny animals doing brave actions
- soft_warm: loyal pets and heartfelt narration
- tender_sad: lonely or sad scenes
- urgent_fear: danger, fear, worry, without violence
- deep_brave: larger heroic animals or brave turning points
- wise_slow: elephants, turtles, whales, owls, reflective moments
- bright_curious: curious or playful discovery scenes
- hopeful_warm: hopeful recovery scenes
- happy_light: happy ending scenes
- narrator_emotional: neutral emotional narration

Input idea:
Topic: {topic}
Animal: {animal}
Life lesson: {lesson}

Return valid JSON only. No markdown. No comments. No text before or after JSON.
All strings must be properly escaped. Do not leave any string unfinished.

Required JSON schema:
{{
  "title": "Clickable YouTube Shorts title under 70 characters",
  "script": "Full voiceover script, 90 to 135 words, English only",
  "description": "Short description plus hashtags including #shorts #animalstory #emotionalstory #lifelessons #tinybravetails",
  "scene_prompts": {{
    "character": {{
      "name": "Short original animal name",
      "animal_type": "{animal}",
      "description": "Detailed consistent character description for image generation"
    }},
    "hook_text": "Short hook text shown in the first scene",
    "comment_prompt": "Short engagement question",
    "scenes": [
      {{
        "scene_number": 1,
        "narration_en": "One short narration sentence or two",
        "subtitle_en": "Same or shorter subtitle text",
        "emotion": "curious",
        "voice_style": "bright_curious",
        "image_prompt": "Vertical 9:16 2D storybook image prompt, no text, no watermark"
      }}
    ]
  }}
}}

Create 5 to 7 scenes. Each scene narration must be short enough for voiceover.
""".strip()


def validate_story(data, record):
    if not isinstance(data, dict):
        raise RuntimeError("Gemini response is not a JSON object.")
    title = str(data.get("title", "")).strip()
    script = str(data.get("script", "")).strip()
    description = str(data.get("description", "")).strip()
    if not title:
        raise RuntimeError("Gemini response missing title.")
    if not script:
        raise RuntimeError("Gemini response missing script.")
    if not description:
        raise RuntimeError("Gemini response missing description.")
    wc = len(script.split())
    if wc < 55:
        raise RuntimeError(f"Generated script is too short: {wc} words.")
    if wc > 180:
        raise RuntimeError(f"Generated script is too long: {wc} words.")
    if "#shorts" not in description.lower():
        description += "\n\n#shorts #animalstory #emotionalstory #lifelessons #tinybravetails"
    scene_payload = normalize_scene_payload(
        data.get("scene_prompts"), title, script,
        str(record.get("animal", "")).strip(), str(record.get("lesson", "")).strip()
    )
    return {
        "title": title[:95],
        "script": script,
        "description": description[:4900],
        "scene_prompts": json.dumps(scene_payload, ensure_ascii=False),
    }


def configure_gemini():
    if not GEMINI_API_KEY:
        raise RuntimeError("Missing required environment variable: GEMINI_API_KEY")
    genai.configure(api_key=GEMINI_API_KEY)


def make_gemini_model(model_name):
    # Prefer strict JSON output. If this SDK/model rejects response_mime_type, fall back safely.
    base_config = {
        "temperature": 0.65,
        "top_p": 0.9,
        "top_k": 40,
        "max_output_tokens": 4096,
        "response_mime_type": "application/json",
    }
    try:
        return genai.GenerativeModel(model_name, generation_config=base_config)
    except TypeError:
        base_config.pop("response_mime_type", None)
        return genai.GenerativeModel(model_name, generation_config=base_config)


def call_model(model_name, prompt):
    model = make_gemini_model(model_name)
    try:
        response = model.generate_content(prompt)
    except TypeError:
        # Older SDK/model combination may reject JSON mime at request time.
        model = genai.GenerativeModel(
            model_name,
            generation_config={
                "temperature": 0.65,
                "top_p": 0.9,
                "top_k": 40,
                "max_output_tokens": 4096,
            },
        )
        response = model.generate_content(prompt)

    text = getattr(response, "text", None)
    if not text:
        try:
            text = response.candidates[0].content.parts[0].text
        except Exception as exc:
            raise RuntimeError(f"Could not read Gemini response text: {response}") from exc
    return text


def generate_story(record):
    configure_gemini()
    prompt = build_prompt(record)
    errors = []

    for model_name in MODEL_CANDIDATES:
        print(f"Trying Gemini model: {model_name}")

        def one_complete_generation():
            text = call_model(model_name, prompt)
            data = parse_json_response(text)
            return validate_story(data, record)

        try:
            story = run_with_retry(
                f"Generating and validating story with {model_name}",
                one_complete_generation,
                max_attempts=4,
                max_wait_seconds=45,
            )
            print(f"Gemini model used: {model_name}")
            return story, model_name

        except Exception as exc:
            message = str(exc)
            errors.append(f"{model_name}: {message}")
            if "404" in message or "not found" in message.lower() or "not supported" in message.lower():
                print(f"Model {model_name} is unavailable. Trying next model...")
                continue
            # If a model repeatedly returns malformed JSON, try the next model instead of killing the whole run.
            if any(s in message.lower() for s in ["malformed json", "invalid json", "incomplete json", "unterminated string", "could not find complete json"]):
                print(f"Model {model_name} kept returning bad JSON. Trying next model...")
                continue
            raise

    raise RuntimeError("All Gemini models failed. " + " | ".join(errors))


def set_row_success(sheet, row_number, headers, story, model_used):
    col = {name: idx + 1 for idx, name in enumerate(headers)}
    updates = {
        "script": story["script"],
        "title": story["title"],
        "description": story["description"],
        "scene_prompts": story["scene_prompts"],
        "status": "GENERATED",
        "image_status": "PENDING",
        "audio_status": "",
        "youtube_status": "",
        "youtube_video_id": "",
        "video_url": "",
        "video_file_path": "",
        "error_message": "",
        "created_at": now_iso(),
    }
    for name, value in updates.items():
        if name in col:
            update_cell(sheet, row_number, col[name], value)


def set_row_failed(sheet, row_number, headers, error):
    col = {name: idx + 1 for idx, name in enumerate(headers)}
    if "status" in col:
        update_cell(sheet, row_number, col["status"], "FAILED")
    if "error_message" in col:
        update_cell(sheet, row_number, col["error_message"], str(error)[:500])
    if "created_at" in col:
        update_cell(sheet, row_number, col["created_at"], now_iso())


def main():
    print("Starting Tiny Brave Tails story generator...")
    require_env("GOOGLE_SHEET_ID")
    require_env("GOOGLE_SERVICE_ACCOUNT_JSON")
    require_env("GEMINI_API_KEY")

    client = get_sheets_client()
    spreadsheet = open_spreadsheet(client)
    content_sheet = get_worksheet(spreadsheet, CONTENT_SHEET_NAME)
    try:
        logs_sheet = get_worksheet(spreadsheet, LOGS_SHEET_NAME)
    except Exception:
        logs_sheet = None

    headers = ensure_headers(content_sheet)
    records = get_all_records(content_sheet)
    row_number, record = find_first_idea(records)
    if not record:
        log(logs_sheet, "", "GENERATE_STORY", "No IDEA rows found.")
        print("No IDEA rows found.")
        return

    video_id = str(record.get("id", "")).strip()
    print(f"Found IDEA row {row_number}. ID={video_id}. Topic={record.get('topic')}. Animal={record.get('animal')}")
    try:
        story, model_used = generate_story(record)
        set_row_success(content_sheet, row_number, headers, story, model_used)
        log(logs_sheet, video_id, "GENERATE_STORY", f"Generated story using {model_used}: {story['title']}")
        print(f"Row {row_number} updated to GENERATED. Title: {story['title']}")
    except Exception as exc:
        print(f"Generation failed for row {row_number}: {exc}")
        set_row_failed(content_sheet, row_number, headers, exc)
        log(logs_sheet, video_id, "FAILED_STORY", str(exc)[:1000])
        raise


if __name__ == "__main__":
    main()
