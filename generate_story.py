import json
import os
import re
from typing import Any, Dict, List

import google.generativeai as genai

from config import MAIN_CHARACTER_BIBLE, MAIN_CHARACTER_NAME, STORY_UNIVERSE, VIDEO_TYPES
from tbt_common import (
    find_column,
    find_optional_column,
    get_all_values,
    get_cell,
    get_sheets_client,
    get_worksheet,
    get_logs_worksheet,
    log,
    open_spreadsheet,
    require_env,
    run_with_retry,
    update_cell,
    update_optional,
    utc_now,
)

CONTENT_SHEET_NAME = "Content"
LOGS_SHEET_NAME = "Logs"
MODEL_NAME = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

SHORT_BEATS = [
    "cold open hook with danger or loneliness",
    "show the small hero's wound or fear",
    "raise the problem and make it personal",
    "moment of doubt, almost giving up",
    "brave choice with emotional sacrifice",
    "warm rescue / connection / relief",
    "quiet lesson that lands softly",
]

LONG_BEATS = [
    "cold open with a painful unresolved question",
    "introduce Toby's private wound without explaining it directly",
    "show the forest problem through action, not narration",
    "Toby meets someone who mirrors his fear",
    "a small failure that costs him emotionally",
    "quiet memory scene that deepens the stakes",
    "first brave attempt, imperfect but sincere",
    "another character misunderstands Toby",
    "Toby chooses patience instead of proving himself",
    "the world becomes harder: rain, night, distance, silence",
    "a vulnerable confession or near-confession",
    "midpoint: Toby discovers what the journey is really about",
    "false comfort: it looks solved but is not",
    "Toby loses something small but meaningful",
    "secondary character makes a selfish choice",
    "Toby refuses bitterness",
    "a slow scene of care: shelter, warmth, food, listening",
    "the main danger returns in a quieter emotional form",
    "Toby almost quits and nobody would blame him",
    "a memory of love becomes a decision",
    "Toby acts without expecting credit",
    "the rescue costs time, pride, or safety",
    "the other character finally sees Toby clearly",
    "resolution begins, but with a scar still present",
    "soft earned wisdom, never preachy",
    "final image: Toby still slow, but no longer stuck",
]


def clean_json_response(text: str) -> str:
    text = (text or "").strip()
    if not text:
        raise ValueError("Gemini returned empty text")
    text = re.sub(r"^```(?:json)?", "", text, flags=re.IGNORECASE).strip()
    text = re.sub(r"```$", "", text).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError(f"Could not find JSON object in Gemini response: {text[:500]}")
    return text[start : end + 1]


def word_count(text: str) -> int:
    return len(re.findall(r"\b[\w']+\b", text or ""))


def clamp_int(value, default, low, high):
    try:
        parsed = int(float(str(value).strip()))
    except Exception:
        parsed = default
    return max(low, min(high, parsed))


def normalize_type(raw: str) -> str:
    value = (raw or "").strip().lower().replace(" ", "_").replace("-", "_")
    aliases = {
        "": "long_story",
        "long": "long_story",
        "long_video": "long_story",
        "story": "long_story",
        "main": "long_story",
        "toby": "toby_collection",
        "collection": "toby_collection",
        "shorts": "short",
    }
    value = aliases.get(value, value)
    return value if value in VIDEO_TYPES else "long_story"


def build_character(main_character: str, animal: str) -> Dict[str, str]:
    name = (main_character or "").strip() or MAIN_CHARACTER_NAME
    animal_lower = (animal or "").lower()
    if name.lower() == "toby" or "turtle" in animal_lower or "سلحف" in animal_lower:
        return {"name": "Toby", "description": MAIN_CHARACTER_BIBLE}
    return {
        "name": name,
        "description": (
            f"{name} is a memorable 2D storybook animal hero connected to {animal or 'the forest'}, "
            "with consistent colors, expressive eyes, one signature accessory, visible emotional restraint, "
            "and a quiet flaw that changes through the story."
        ),
    }


def emotional_score(data: Dict[str, Any]) -> int:
    script = " ".join(scene.get("narration_en", "") for scene in data.get("scenes", []))
    lower = script.lower()
    signals = [
        "alone", "afraid", "scared", "brave", "trembled", "whispered", "heart", "tears",
        "shivered", "promise", "home", "softly", "never", "still", "warm", "held", "courage",
        "forgive", "waited", "remembered", "loss", "quiet", "scar", "mercy", "hope",
    ]
    score = sum(1 for s in signals if s in lower)
    if data.get("emotional_arc"):
        score += 3
    if len(data.get("scenes", [])) >= 20:
        score += 4
    if word_count(script) >= 1800:
        score += 4
    return score


def build_prompt(topic: str, animal: str, lesson: str, video_type: str, target_minutes: int, scene_count: int, character: Dict[str, str], audience: str) -> str:
    if video_type == "short":
        beats = SHORT_BEATS
        target_words = "95 to 145"
        instruction = "Create a powerful emotional YouTube Short."
    else:
        beats = [LONG_BEATS[i % len(LONG_BEATS)] for i in range(scene_count)]
        # Slow emotional narration is roughly 120-145 wpm. This target is intentionally realistic for GitHub Actions.
        min_words = max(1800, target_minutes * 95)
        max_words = max(2300, target_minutes * 135)
        target_words = f"{min_words} to {max_words}"
        instruction = (
            "Create a long-form emotional animal story for a general audience. "
            "It should feel like a calm cinematic audio story, not a children lesson and not a short."
        )
    beat_text = "\n".join(f"{i+1}. {beat}" for i, beat in enumerate(beats))
    title_rule = "under 70 characters" if video_type == "short" else "under 95 characters, no 'for kids' wording"
    return f"""
You are the showrunner, novelist, visual director, and voice director for Tiny Brave Tails.
The new channel positioning is: emotional animal stories for a general audience, NOT made for kids.

Task: {instruction}
Topic: {topic}
Animal/theme: {animal}
Core lesson/theme: {lesson}
Audience: {audience or 'general audience, adults and older teens who like emotional calm stories'}
Story universe: {STORY_UNIVERSE}
Main character name: {character['name']}
Main character bible: {character['description']}
Target duration: about {target_minutes} minutes
Target narration length: {target_words} English words
Exact scene count: {scene_count}

Hard quality rules:
- This must be a real story with a wound, desire, conflict, choice, cost, and earned release.
- Do not write childish educational narration. No counting, colors lessons, nursery tone, baby words, or "kids" wording.
- Do not say the story is true. No gore, horror, explicit violence, politics, religion, or adult sexual content.
- Keep it monetization-safe and general-audience friendly.
- Toby the turtle must feel consistent: slow, wise, flawed, emotionally restrained, not a mascot.
- Every scene needs a distinct action/location/emotion so the video does not repeat the same visual.
- Use cinematic sensory detail: rain on leaves, lantern light, wet stones, quiet breathing, old shell, distant thunder.
- English narration only. Arabic subtitles must fully translate each scene.
- No direct moral lecture. Let the meaning land through the ending.
- Every image_prompt must include the exact character design and a different visual composition.

Scene beats:
{beat_text}

Return valid JSON only, exactly in this shape:
{{
  "title": "YouTube title {title_rule}",
  "description": "YouTube description for general audience. Include hashtags but do NOT use #kids, #nursery, #cartoonforkids, or made-for-kids wording.",
  "audience": "general audience - not made for kids",
  "video_type": "{video_type}",
  "target_minutes": {target_minutes},
  "emotional_arc": "one sentence describing the feeling journey",
  "character": {{
    "name": "{character['name']}",
    "description": "{character['description']}"
  }},
  "scenes": [
    {{
      "scene_number": 1,
      "beat": "emotional purpose of this scene",
      "emotion": "one of: wonder, lonely, worried, afraid, brave, relieved, peaceful",
      "voice_style": "specific direction for narrator performance",
      "pause_after": 0.45,
      "camera_motion": "one of: slow_zoom_in, slow_zoom_out, gentle_pan_left, gentle_pan_right, tiny_handheld, still_soft",
      "narration_en": "spoken English narration. For long videos, use 2-5 emotionally rich sentences.",
      "subtitle_en": "same meaning, subtitle-safe English",
      "subtitle_ar": "Arabic translation of the narration",
      "image_prompt": "vertical 9:16 warm 2D cinematic storybook frame, exact character design, no text"
    }}
  ]
}}
"""


def normalize_scene(scene: Dict[str, Any], i: int, character_desc: str, video_type: str) -> Dict[str, Any]:
    narration = str(scene.get("narration_en", "")).strip()
    subtitle_en = str(scene.get("subtitle_en", "")).strip() or narration
    subtitle_ar = str(scene.get("subtitle_ar", "")).strip()
    image_prompt = str(scene.get("image_prompt") or scene.get("visual_prompt") or scene.get("prompt") or "").strip()
    beat_default = LONG_BEATS[(i - 1) % len(LONG_BEATS)] if video_type != "short" else SHORT_BEATS[min(i - 1, len(SHORT_BEATS)-1)]
    emotion = str(scene.get("emotion", "peaceful")).strip().lower()
    if emotion not in {"wonder", "lonely", "worried", "afraid", "brave", "relieved", "peaceful"}:
        emotion = "peaceful"
    if not narration:
        narration = f"Toby kept moving through the Moonlit Forest, carrying a quiet fear he had not yet learned how to name."
    if not subtitle_en:
        subtitle_en = narration
    if not subtitle_ar:
        subtitle_ar = "واصل توبي رحلته بهدوء، وهو يحمل خوفًا صغيرًا لم يعرف بعد كيف يسميه."
    if not image_prompt:
        image_prompt = (
            f"vertical 9:16 warm 2D cinematic storybook frame, {character_desc}, "
            f"distinct scene {i}, emotion: {emotion}, beat: {scene.get('beat', beat_default)}, action based on: {narration[:280]}, "
            "soft moonlit forest lighting, expressive eyes, painterly texture, no text, no watermark"
        )
    if character_desc and character_desc[:40].lower() not in image_prompt.lower():
        image_prompt = f"{character_desc}. {image_prompt}"
    return {
        "scene_number": i,
        "beat": str(scene.get("beat", beat_default)).strip(),
        "emotion": emotion,
        "voice_style": str(scene.get("voice_style", "slow warm cinematic narrator, emotionally restrained")).strip(),
        "pause_after": float(scene.get("pause_after", 0.45) or 0.45),
        "camera_motion": str(scene.get("camera_motion", ["slow_zoom_in", "gentle_pan_left", "slow_zoom_out", "gentle_pan_right", "still_soft"][i % 5])).strip(),
        "narration_en": narration,
        "subtitle_en": subtitle_en,
        "subtitle_ar": subtitle_ar,
        "image_prompt": image_prompt,
    }


def fallback_expand_scenes(data: Dict[str, Any], scene_count: int, character: Dict[str, str], video_type: str) -> Dict[str, Any]:
    scenes = data.get("scenes", []) if isinstance(data.get("scenes"), list) else []
    if not scenes:
        scenes = []
    while len(scenes) < scene_count:
        i = len(scenes) + 1
        beat = LONG_BEATS[(i - 1) % len(LONG_BEATS)] if video_type != "short" else SHORT_BEATS[min(i - 1, len(SHORT_BEATS)-1)]
        scenes.append({
            "scene_number": i,
            "beat": beat,
            "emotion": ["lonely", "worried", "afraid", "brave", "relieved", "peaceful"][i % 6],
            "voice_style": "slow, intimate, cinematic, with tiny pauses after emotional words",
            "pause_after": 0.5,
            "camera_motion": ["slow_zoom_in", "gentle_pan_left", "slow_zoom_out", "gentle_pan_right", "still_soft"][i % 5],
            "narration_en": (
                f"Toby moved through another quiet part of the Moonlit Forest, slower than the wind but steadier than his fear. "
                f"The moment asked him for patience, and patience was never easy when the heart wanted to run. "
                f"Still, he listened, breathed, and chose one small brave step."
            ),
            "subtitle_en": "Toby moved slowly through the Moonlit Forest, afraid but still choosing one brave step.",
            "subtitle_ar": "تحرك توبي ببطء داخل الغابة المقمرة، خائفًا لكنه اختار خطوة شجاعة صغيرة.",
            "image_prompt": f"vertical 9:16 warm 2D cinematic storybook frame, {character['description']}, {beat}, moonlit forest, no text",
        })
    data["scenes"] = scenes[:scene_count]
    return data


def generate_story_package(topic: str, animal: str, lesson: str, video_type="long_story", target_minutes=30, main_character="Toby", audience="general audience") -> Dict[str, Any]:
    video_type = normalize_type(video_type)
    settings = VIDEO_TYPES[video_type]
    target_minutes = clamp_int(target_minutes, int(settings.get("duration_minutes", 30)), 1, 60)
    if video_type == "short":
        scene_count = 7
    else:
        # Keep render practical but genuinely long-form. User can increase via sheet.
        scene_count = clamp_int(settings.get("scene_count", 32), 28 if target_minutes >= 30 else 18, 18, 60)
        if target_minutes >= 45:
            scene_count = max(scene_count, 42)
        if target_minutes >= 55:
            scene_count = max(scene_count, 52)
    character = build_character(main_character, animal)

    genai.configure(api_key=require_env("GEMINI_API_KEY"))
    model = genai.GenerativeModel(MODEL_NAME)

    def call_model():
        response = model.generate_content(
            build_prompt(topic, animal, lesson, video_type, target_minutes, scene_count, character, audience),
            generation_config={"temperature": 0.88, "top_p": 0.93, "max_output_tokens": 32768},
        )
        return json.loads(clean_json_response(response.text))

    data = run_with_retry("Generating deep emotional story package", call_model, max_attempts=4)
    if "title" not in data:
        data["title"] = f"Toby's Quiet Journey Through the Moonlit Forest"
    if "description" not in data:
        data["description"] = "A long emotional animal story for a general audience. #animalstory #emotionalstory #bedtimestory #tinybravetails"
    data["audience"] = "general audience - not made for kids"
    data["video_type"] = video_type
    data["target_minutes"] = target_minutes
    data["character"] = data.get("character") if isinstance(data.get("character"), dict) else character
    data["character"]["name"] = character["name"]
    data["character"]["description"] = character["description"]
    data = fallback_expand_scenes(data, scene_count, character, video_type)
    data["scenes"] = [normalize_scene(scene, i, character["description"], video_type) for i, scene in enumerate(data["scenes"], start=1)]
    data["script"] = " ".join(scene["narration_en"] for scene in data["scenes"])
    data["emotional_score"] = emotional_score(data)
    # Do not fail the workflow just because a model under-scored. Log and continue with usable content.
    return data


def main():
    client = get_sheets_client()
    spreadsheet = open_spreadsheet(client)
    content_sheet = get_worksheet(spreadsheet, CONTENT_SHEET_NAME)
    logs_sheet = get_logs_worksheet(spreadsheet)
    values = get_all_values(content_sheet)
    if not values:
        raise ValueError("Content sheet is empty.")
    headers = values[0]
    id_col = find_column(headers, "id")
    topic_col = find_column(headers, "topic")
    animal_col = find_column(headers, "animal")
    lesson_col = find_column(headers, "lesson")
    script_col = find_column(headers, "script")
    title_col = find_column(headers, "title")
    description_col = find_column(headers, "description")
    status_col = find_column(headers, "status")
    created_at_col = find_column(headers, "created_at")
    scene_prompts_col = find_column(headers, "scene_prompts")
    image_status_col = find_column(headers, "image_status")
    audio_status_col = find_column(headers, "audio_status")
    youtube_status_col = find_column(headers, "youtube_status")
    youtube_video_id_col = find_column(headers, "youtube_video_id")
    video_type_col = find_optional_column(headers, "video_type")
    target_minutes_col = find_optional_column(headers, "target_minutes")
    main_character_col = find_optional_column(headers, "main_character")
    story_universe_col = find_optional_column(headers, "story_universe")
    audience_col = find_optional_column(headers, "audience")
    made_for_kids_col = find_optional_column(headers, "made_for_kids")
    error_message_col = find_optional_column(headers, "error_message")

    requested_video_type = normalize_type(os.getenv("TBT_VIDEO_TYPE", "") or os.getenv("VIDEO_TYPE", "")) if (os.getenv("TBT_VIDEO_TYPE") or os.getenv("VIDEO_TYPE")) else ""

    target_row_number = None
    target_row = None
    for index, row in enumerate(values[1:], start=2):
        row_status = get_cell(row, status_col).upper()
        row_type = normalize_type(get_cell(row, video_type_col))
        if row_status == "IDEA" and (not requested_video_type or row_type == requested_video_type):
            target_row_number = index
            target_row = row
            break
    if target_row_number is None:
        msg = f"No IDEA row found" + (f" for video_type={requested_video_type}" if requested_video_type else "")
        log(logs_sheet, "", "GENERATE_STORY", msg)
        print(msg)
        return

    video_id = get_cell(target_row, id_col)
    video_type = requested_video_type or normalize_type(get_cell(target_row, video_type_col))
    target_minutes = os.getenv("TBT_TARGET_MINUTES", "").strip() or get_cell(target_row, target_minutes_col) or VIDEO_TYPES[video_type].get("duration_minutes", 30)
    main_character = get_cell(target_row, main_character_col) or MAIN_CHARACTER_NAME
    audience = get_cell(target_row, audience_col) or "general audience - not made for kids"
    try:
        package = generate_story_package(
            get_cell(target_row, topic_col),
            get_cell(target_row, animal_col),
            get_cell(target_row, lesson_col),
            video_type=video_type,
            target_minutes=target_minutes,
            main_character=main_character,
            audience=audience,
        )
        scene_payload = {
            "character": package["character"],
            "emotional_arc": package.get("emotional_arc", ""),
            "emotional_score": package.get("emotional_score", ""),
            "audience": package.get("audience", "general audience - not made for kids"),
            "video_type": package.get("video_type", video_type),
            "target_minutes": package.get("target_minutes", target_minutes),
            "story_universe": get_cell(target_row, story_universe_col) or STORY_UNIVERSE,
            "scenes": package["scenes"],
        }
        update_cell(content_sheet, target_row_number, title_col, package["title"])
        update_cell(content_sheet, target_row_number, script_col, package["script"])
        update_cell(content_sheet, target_row_number, description_col, package["description"])
        update_cell(content_sheet, target_row_number, scene_prompts_col, json.dumps(scene_payload, ensure_ascii=False))
        update_cell(content_sheet, target_row_number, status_col, "GENERATED")
        update_cell(content_sheet, target_row_number, created_at_col, utc_now())
        update_cell(content_sheet, target_row_number, image_status_col, "PENDING")
        update_cell(content_sheet, target_row_number, audio_status_col, "PENDING")
        update_cell(content_sheet, target_row_number, youtube_status_col, "")
        update_cell(content_sheet, target_row_number, youtube_video_id_col, "")
        update_optional(content_sheet, target_row_number, video_type_col, video_type)
        update_optional(content_sheet, target_row_number, target_minutes_col, str(package.get("target_minutes", target_minutes)))
        update_optional(content_sheet, target_row_number, main_character_col, package["character"]["name"])
        update_optional(content_sheet, target_row_number, story_universe_col, STORY_UNIVERSE)
        update_optional(content_sheet, target_row_number, audience_col, "general audience - not made for kids")
        update_optional(content_sheet, target_row_number, made_for_kids_col, "FALSE")
        update_optional(content_sheet, target_row_number, error_message_col, "")
        log(logs_sheet, video_id, "GENERATE_STORY", f"Generated {video_type} story: {package['title']} | scenes={len(package['scenes'])} | words={word_count(package['script'])} | score={package['emotional_score']}")
        print(f"Generated story: {package['title']}")
        print(f"Scenes: {len(package['scenes'])} | Words: {word_count(package['script'])} | Type: {video_type}")
    except Exception as exc:
        update_optional(content_sheet, target_row_number, error_message_col, str(exc)[:1500])
        log(logs_sheet, video_id, "GENERATE_STORY_ERROR", str(exc))
        raise


if __name__ == "__main__":
    main()
