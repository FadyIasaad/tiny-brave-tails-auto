OCimport json
import os
import re
from typing import Any, Dict, List

import google.generativeai as genai

from config import CINEMATIC_VISUAL_STYLE, DEFAULT_NARRATOR_STYLE, VIDEO_TYPES
from nd_common import (
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

VALID_EMOTIONS = {"dread", "tension", "eerie", "calm", "fear", "relief", "mystery", "anger", "satisfaction"}

HOOK_BEATS = [
    "cold open with one disturbing line, no context yet",
    "fast scene-setting: where, who, what already feels wrong",
    "the detail that confirms something is deeply wrong",
    "the moment it becomes undeniable",
    "the choice or the discovery",
    "final gut-punch line, no comforting resolution",
]

HORROR_BEATS = [
    "cold open with an unanswered, unsettling question",
    "ordinary setting established, something subtly off",
    "a first small wrongness, dismissed as nothing",
    "routine continues but unease quietly grows",
    "a detail noticed that should not exist",
    "an attempt to explain it away rationally",
    "the rational explanation quietly fails",
    "isolation deepens: night, an empty space, no one to call",
    "a sound, motion, or presence is noticed for the first time",
    "checking and finding nothing, which is somehow worse",
    "a memory or piece of backstory hints at why this is happening",
    "the wrongness becomes impossible to dismiss",
    "a false moment of safety",
    "the false safety breaks",
    "direct confrontation or pursuit begins",
    "a choice between fleeing and understanding",
    "a piece of the truth is revealed, raising more questions than it answers",
    "the danger becomes personal and close",
    "a costly decision made under pressure",
    "the full nature of the threat is revealed",
    "a desperate struggle or escape attempt",
    "a moment of near-loss",
    "the cost of surviving: something is taken or permanently changed",
    "a quiet aftermath that does not feel fully resolved",
    "a final unsettling detail, planted for the ending",
    "closing line that lingers, ambiguous rather than comforting",
]

CONFESSION_BEATS = [
    "cold open: the narrator states plainly what was done, no context yet",
    "establish the relationship and how it looked from the outside",
    "the first small sign something was wrong, dismissed at the time",
    "life continues normally despite a quiet, growing doubt",
    "a discovery or confirmation of the betrayal",
    "the narrator's immediate gut-level reaction",
    "the narrator deliberately decides not to react right away",
    "quietly gathering information or proof, unnoticed",
    "a moment of forced public normalcy while privately knowing the truth",
    "a second betrayal or complication is uncovered",
    "the narrator's plan begins to take shape",
    "a test of resolve: almost confronting, holding back",
    "someone else's selfish or oblivious behavior raises the stakes",
    "the narrator prepares the move that will change everything",
    "a moment of real doubt or guilt about what is about to happen",
    "the narrator commits anyway",
    "the confrontation or reveal begins",
    "the other party's reaction: denial, anger, or collapse",
    "consequences ripple outward to everyone else involved",
    "a twist the listener did not see coming",
    "the narrator's own cost for what they did",
    "a quiet moment of clarity, or regret, or both",
    "how things stand now, well after the fact",
    "final line: matter-of-fact, no moral lecture, just the truth",
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
        "": "horror_story",
        "horror": "horror_story",
        "scary": "horror_story",
        "confession": "confession_story",
        "revenge": "confession_story",
        "betrayal": "confession_story",
        "reddit": "confession_story",
        "shorts": "short",
    }
    value = aliases.get(value, value)
    return value if value in VIDEO_TYPES else "horror_story"


def build_story_context(characters: str, narrator_pov: str, setting: str) -> str:
    cast = (characters or "").strip() or "the people involved in this story"
    pov = (narrator_pov or "").strip() or DEFAULT_NARRATOR_STYLE
    place = (setting or "").strip() or "an ordinary, true-to-life modern setting"
    return f"Cast: {cast}. Setting: {place}. Narrator: {pov}."


def emotional_score(data: Dict[str, Any]) -> int:
    script = " ".join(scene.get("narration_en", "") for scene in data.get("scenes", []))
    lower = script.lower()
    signals = [
        "alone", "afraid", "silence", "quiet", "still", "trembled", "whispered", "heart",
        "shadow", "never", "warm", "cold", "promise", "remembered", "waited", "watched",
        "knew", "lied", "found out", "proof", "truth", "finally", "every night", "again",
    ]
    score = sum(1 for s in signals if s in lower)
    if data.get("emotional_arc"):
        score += 3
    if len(data.get("scenes", [])) >= 20:
        score += 4
    if word_count(script) >= 1500:
        score += 4
    return score


def build_prompt(topic: str, characters: str, theme: str, video_type: str, target_minutes: int, scene_count: int, story_context: str, audience: str) -> str:
    if video_type == "short":
        beats = HOOK_BEATS
        target_words = "110 to 170"
        instruction = (
            "Create a sharp, unsettling YouTube Short hook story. It must work as a single complete "
            "moment, not a trailer for a longer story: a fast, real-feeling account with an immediate "
            "hook and a final line that lands hard."
        )
    elif video_type == "confession_story":
        beats = CONFESSION_BEATS
        min_words = max(1500, target_minutes * 95)
        max_words = max(2000, target_minutes * 135)
        target_words = f"{min_words} to {max_words}"
        instruction = (
            "Create a long-form, real-feeling first-person confession story about betrayal, deception, "
            "or quiet revenge, in the voice of someone telling you exactly what happened. Calm, exact, "
            "and emotionally controlled, not melodramatic. No moral lecture at the end."
        )
    else:
        beats = HORROR_BEATS
        min_words = max(1500, target_minutes * 95)
        max_words = max(2000, target_minutes * 135)
        target_words = f"{min_words} to {max_words}"
        instruction = (
            "Create a long-form, slow-burn psychological horror story for a general adult audience. "
            "It should feel like a calm, real-feeling late-night account, not a jump-scare video and "
            "not an over-explained plot. Dread builds through small details, not gore."
        )

    beat_text = "\n".join(f"{i+1}. {beat}" for i, beat in enumerate(beats[:scene_count])) if video_type != "short" else "\n".join(f"{i+1}. {beat}" for i, beat in enumerate(beats))
    title_rule = "under 70 characters, no clickbait ALL CAPS" if video_type == "short" else "under 95 characters, intriguing but not clickbait-spam"

    return f"""
You are the showrunner, novelist, and voice director for the YouTube channel Nightfall Diaries.
Positioning: real-feeling late-night stories for adults — confessions, betrayal/revenge accounts, and
quiet psychological horror — narrated slowly over dark visuals, meant to watch, unwind, or fall asleep to.

Task: {instruction}
Topic / premise: {topic}
Characters involved: {characters}
Core theme / throughline: {theme}
Audience: {audience or 'general adult audience'}
{story_context}
Target duration: about {target_minutes} minutes
Target narration length: {target_words} English words
Exact scene count: {scene_count}

Hard quality rules:
- This must read as a real, plausible first-person or close-third account, not a fairy tale and not a fable.
- No real named public figures, no real identifiable private individuals, no real specific addresses or businesses.
- Restrained, not graphic: build dread or tension through detail, pacing, and implication. No gore, no explicit
  violence, no sexual content, no step-by-step instructions for harming anyone or anything. This must stay
  comfortably general-audience and monetization-safe.
- No moral lecture at the end. Let the story land on its own.
- English narration only.
- Every scene needs a distinct location/action/emotional beat so the video never repeats the same visual.
  The first scene must hook within 2 seconds.
- Use cinematic sensory detail: rain on glass, a porch light, a hallway, a phone screen glow, footsteps, silence.
- Every scene must include exactly 4 visually different shots. Each shot needs its own narration_en and image_prompt.
- Visual identity: dark cinematic stills, moody lighting, restrained and suggestive rather than graphic, faces
  obscured or not shown, atmosphere and objects carry the story rather than detailed recurring character portraits.
- Every image_prompt must describe camera framing, lighting, location, and the exact action/emotion of that shot.
  Generic prompts are forbidden.
- Narration must sound like a real person speaking slowly and carefully, not an essay. Short sentences. Real pauses.
- Each scene's "emotion" must be exactly one of: dread, tension, eerie, calm, fear, relief, mystery, anger, satisfaction.

Scene beats:
{beat_text}

Return valid JSON only, exactly in this shape:
{{
  "title": "YouTube title {title_rule}",
  "description": "YouTube description for a general adult audience. Include a few relevant hashtags.",
  "audience": "general audience",
  "video_type": "{video_type}",
  "target_minutes": {target_minutes},
  "emotional_arc": "one sentence describing the feeling journey",
  "scenes": [
    {{
      "scene_number": 1,
      "beat": "narrative purpose of this scene",
      "emotion": "one of: dread, tension, eerie, calm, fear, relief, mystery, anger, satisfaction",
      "voice_style": "specific direction for narrator performance",
      "pause_after": 0.45,
      "camera_motion": "one of: slow_zoom_in, slow_zoom_out, gentle_pan_left, gentle_pan_right, tiny_handheld, still_soft",
      "narration_en": "full spoken English narration for the scene",
      "subtitle_en": "short English subtitle only",
      "image_prompt": "main scene visual prompt",
      "shots": [
        {{
          "shot_number": 1,
          "emotion": "one of: dread, tension, eerie, calm, fear, relief, mystery, anger, satisfaction",
          "narration_en": "one short sentence for this exact moment",
          "subtitle_en": "short English subtitle only",
          "image_prompt": "vertical 9:16 dark cinematic still for this exact moment, no text",
          "camera_motion": "slow_zoom_in"
        }},
        {{
          "shot_number": 2,
          "emotion": "one of: dread, tension, eerie, calm, fear, relief, mystery, anger, satisfaction",
          "narration_en": "next short sentence for a new visual moment",
          "subtitle_en": "short English subtitle only",
          "image_prompt": "different visual composition for this moment, no text",
          "camera_motion": "gentle_pan_left"
        }},
        {{
          "shot_number": 3,
          "emotion": "one of: dread, tension, eerie, calm, fear, relief, mystery, anger, satisfaction",
          "narration_en": "third short sentence for a close, intimate moment",
          "subtitle_en": "short English subtitle only",
          "image_prompt": "different close, intimate framing for this moment, no text",
          "camera_motion": "tiny_handheld"
        }},
        {{
          "shot_number": 4,
          "emotion": "one of: dread, tension, eerie, calm, fear, relief, mystery, anger, satisfaction",
          "narration_en": "final short sentence for this scene's consequence",
          "subtitle_en": "short English subtitle only",
          "image_prompt": "final consequence frame with cinematic lighting, no text",
          "camera_motion": "slow_zoom_out"
        }}
      ]
    }}
  ]
}}
"""


def split_into_shots(narration: str, image_prompt: str, emotion: str, story_context: str, scene_index: int) -> List[Dict[str, Any]]:
    parts = [x.strip() for x in re.split(r"(?<=[.!?])\s+", narration or "") if x.strip()]
    if len(parts) < 3:
        parts = [
            narration.strip() or "The room was quiet in a way that felt deliberate.",
            "For a moment, the silence felt heavier than it should have.",
            "Somewhere close, something shifted that should not have moved.",
            "Whatever it was, it was not finished yet.",
        ]
    parts = parts[:4]
    shot_styles = [
        "wide establishing shot showing the full location and atmosphere",
        "medium shot showing the exact action or choice in this moment",
        "close, intimate framing showing tension without showing a face",
        "final consequence shot showing what changed and why it matters",
    ]
    motions = ["slow_zoom_in", "gentle_pan_left", "tiny_handheld", "slow_zoom_out"]
    shots = []
    for n, sentence in enumerate(parts, start=1):
        shots.append({
            "shot_number": n,
            "emotion": emotion,
            "narration_en": sentence,
            "subtitle_en": sentence,
            "camera_motion": motions[(n - 1) % len(motions)],
            "image_prompt": (
                f"{story_context} {shot_styles[n-1]}. {image_prompt}. "
                f"Action based on this exact narration: {sentence}. "
                f"{CINEMATIC_VISUAL_STYLE}. No text, no watermark."
            ),
        })
    return shots


def normalize_shot(shot: Dict[str, Any], n: int, scene_narration: str, scene_prompt: str, emotion: str, story_context: str) -> Dict[str, Any]:
    shot_emotion = str(shot.get("emotion", emotion)).strip().lower()
    if shot_emotion not in VALID_EMOTIONS:
        shot_emotion = emotion if emotion in VALID_EMOTIONS else "calm"
    narration = str(shot.get("narration_en", "")).strip() or scene_narration
    subtitle = str(shot.get("subtitle_en", "")).strip() or narration
    prompt = str(shot.get("image_prompt", "")).strip() or scene_prompt
    if story_context and story_context[:30].lower() not in prompt.lower():
        prompt = f"{story_context} {prompt}"
    return {
        "shot_number": n,
        "emotion": shot_emotion,
        "narration_en": narration,
        "subtitle_en": subtitle,
        "image_prompt": prompt,
        "camera_motion": str(shot.get("camera_motion", ["slow_zoom_in", "gentle_pan_left", "slow_zoom_out", "gentle_pan_right", "tiny_handheld"][n % 5])).strip(),
        "pause_after": float(shot.get("pause_after", 0.28) or 0.28),
    }


def normalize_scene(scene: Dict[str, Any], i: int, story_context: str, video_type: str) -> Dict[str, Any]:
    narration = str(scene.get("narration_en", "")).strip()
    subtitle_en = str(scene.get("subtitle_en", "")).strip() or narration
    image_prompt = str(scene.get("image_prompt") or scene.get("visual_prompt") or scene.get("prompt") or "").strip()
    beats = HOOK_BEATS if video_type == "short" else (CONFESSION_BEATS if video_type == "confession_story" else HORROR_BEATS)
    beat_default = beats[min(i - 1, len(beats) - 1)]
    emotion = str(scene.get("emotion", "calm")).strip().lower()
    if emotion not in VALID_EMOTIONS:
        emotion = "calm"
    if not narration:
        narration = "Something about the room was wrong before anyone could say exactly what."
    if not subtitle_en:
        subtitle_en = narration
    if not image_prompt:
        image_prompt = (
            f"vertical 9:16 dark cinematic still, distinct scene {i}, emotion: {emotion}, "
            f"beat: {scene.get('beat', beat_default)}, action based on: {narration[:280]}, "
            "moody practical lighting, restrained composition, no text, no watermark"
        )
    if story_context and story_context[:30].lower() not in image_prompt.lower():
        image_prompt = f"{story_context} {image_prompt}"

    raw_shots = scene.get("shots") if isinstance(scene.get("shots"), list) else []
    if not raw_shots:
        raw_shots = split_into_shots(narration, image_prompt, emotion, story_context, i)
    shots = [normalize_shot(shot, n, narration, image_prompt, emotion, story_context) for n, shot in enumerate(raw_shots[:4], start=1)]

    return {
        "scene_number": i,
        "beat": str(scene.get("beat", beat_default)).strip(),
        "emotion": emotion,
        "voice_style": str(scene.get("voice_style", "calm, controlled, late-night narrator, speaking slowly")).strip(),
        "pause_after": float(scene.get("pause_after", 0.45) or 0.45),
        "camera_motion": str(scene.get("camera_motion", ["slow_zoom_in", "gentle_pan_left", "slow_zoom_out", "gentle_pan_right", "still_soft"][i % 5])).strip(),
        "narration_en": narration,
        "subtitle_en": subtitle_en,
        "image_prompt": image_prompt,
        "shots": shots,
    }


def fallback_expand_scenes(data: Dict[str, Any], scene_count: int, story_context: str, video_type: str) -> Dict[str, Any]:
    scenes = data.get("scenes", []) if isinstance(data.get("scenes"), list) else []
    if not scenes:
        scenes = []
    beats = HOOK_BEATS if video_type == "short" else (CONFESSION_BEATS if video_type == "confession_story" else HORROR_BEATS)
    while len(scenes) < scene_count:
        i = len(scenes) + 1
        beat = beats[min(i - 1, len(beats) - 1)]
        scenes.append({
            "scene_number": i,
            "beat": beat,
            "emotion": ["tension", "eerie", "calm", "dread", "mystery"][i % 5],
            "voice_style": "slow, intimate, controlled, with small real pauses",
            "pause_after": 0.5,
            "camera_motion": ["slow_zoom_in", "gentle_pan_left", "slow_zoom_out", "gentle_pan_right", "still_soft"][i % 5],
            "narration_en": (
                "Nothing about it made sense yet, but something had already changed. "
                "The quiet stretched a little too long to be nothing. "
                "Whatever came next, there was no taking back what had already been noticed."
            ),
            "subtitle_en": "Something had already changed, and the quiet stretched too long to be nothing.",
            "image_prompt": f"vertical 9:16 dark cinematic still, {beat}, moody practical lighting, no text",
        })
    data["scenes"] = scenes[:scene_count]
    return data


def trim_payload_for_cell(payload: Dict[str, Any], max_chars: int = 49000) -> str:
    """Serialize scene_payload and trim if needed to fit Google Sheets 50k char cell limit."""
    serialized = json.dumps(payload, ensure_ascii=False)
    if len(serialized) <= max_chars:
        return serialized
    # Step 1: strip redundant scene-level fields already present in shots
    for scene in payload.get("scenes", []):
        scene.pop("image_prompt", None)
        scene.pop("narration_en", None)
        scene.pop("subtitle_en", None)
    serialized = json.dumps(payload, ensure_ascii=False)
    if len(serialized) <= max_chars:
        return serialized
    # Step 2: truncate shot image_prompts
    for scene in payload.get("scenes", []):
        for shot in scene.get("shots", []):
            if len(shot.get("image_prompt", "")) > 280:
                shot["image_prompt"] = shot["image_prompt"][:280]
    serialized = json.dumps(payload, ensure_ascii=False)
    if len(serialized) <= max_chars:
        return serialized
    # Step 3: truncate shot narration too
    for scene in payload.get("scenes", []):
        for shot in scene.get("shots", []):
            if len(shot.get("narration_en", "")) > 200:
                shot["narration_en"] = shot["narration_en"][:200]
            shot.pop("subtitle_en", None)
    return json.dumps(payload, ensure_ascii=False)


def generate_story_package(topic: str, characters: str, theme: str, video_type="horror_story", target_minutes=18, narrator_pov="", setting="", audience="general audience") -> Dict[str, Any]:
    video_type = normalize_type(video_type)
    settings = VIDEO_TYPES[video_type]
    target_minutes = clamp_int(target_minutes, int(settings.get("duration_minutes", 18)), 1, 60)
    if video_type == "short":
        scene_count = 6
    else:
        scene_count = clamp_int(settings.get("scene_count", 24), 18, 14, 60)
    story_context = build_story_context(characters, narrator_pov, setting)

    genai.configure(api_key=require_env("GEMINI_API_KEY"))
    model = genai.GenerativeModel(MODEL_NAME)

    def call_model():
        response = model.generate_content(
            build_prompt(topic, characters, theme, video_type, target_minutes, scene_count, story_context, audience),
            generation_config={"temperature": 0.88, "top_p": 0.93, "max_output_tokens": 32768},
        )
        return json.loads(clean_json_response(response.text))

    data = run_with_retry("Generating story package", call_model, max_attempts=4)
    if "title" not in data or not data["title"]:
        data["title"] = "A Story From Nightfall Diaries"
    if "description" not in data or not data["description"]:
        data["description"] = "A late-night story for a general adult audience. #nightfalldiaries #truestory #scarystories"
    data["audience"] = "general audience"
    data["video_type"] = video_type
    data["target_minutes"] = target_minutes
    data = fallback_expand_scenes(data, scene_count, story_context, video_type)
    data["scenes"] = [normalize_scene(scene, i, story_context, video_type) for i, scene in enumerate(data["scenes"], start=1)]
    data["script"] = " ".join(scene["narration_en"] for scene in data["scenes"])
    data["emotional_score"] = emotional_score(data)
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
    characters_col = find_column(headers, "characters")
    theme_col = find_column(headers, "theme")
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
    narrator_pov_col = find_optional_column(headers, "narrator_pov")
    setting_col = find_optional_column(headers, "setting")
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
        msg = "No IDEA row found" + (f" for video_type={requested_video_type}" if requested_video_type else "")
        log(logs_sheet, "", "GENERATE_STORY", msg)
        print(msg)
        return

    video_id = get_cell(target_row, id_col)
    video_type = requested_video_type or normalize_type(get_cell(target_row, video_type_col))
    target_minutes = os.getenv("TBT_TARGET_MINUTES", "").strip() or get_cell(target_row, target_minutes_col) or VIDEO_TYPES[video_type].get("duration_minutes", 18)
    narrator_pov = get_cell(target_row, narrator_pov_col)
    setting_value = get_cell(target_row, setting_col)
    audience = get_cell(target_row, audience_col) or "general audience"
    try:
        package = generate_story_package(
            get_cell(target_row, topic_col),
            get_cell(target_row, characters_col),
            get_cell(target_row, theme_col),
            video_type=video_type,
            target_minutes=target_minutes,
            narrator_pov=narrator_pov,
            setting=setting_value,
            audience=audience,
        )
        scene_payload = {
            "emotional_arc": package.get("emotional_arc", ""),
            "emotional_score": package.get("emotional_score", ""),
            "audience": package.get("audience", "general audience"),
            "video_type": package.get("video_type", video_type),
            "target_minutes": package.get("target_minutes", target_minutes),
            "scenes": package["scenes"],
        }
        update_cell(content_sheet, target_row_number, title_col, package["title"])
        update_cell(content_sheet, target_row_number, script_col, package["script"])
        update_cell(content_sheet, target_row_number, description_col, package["description"])
        update_cell(content_sheet, target_row_number, scene_prompts_col, trim_payload_for_cell(scene_payload))
        update_cell(content_sheet, target_row_number, status_col, "GENERATED")
        update_cell(content_sheet, target_row_number, created_at_col, utc_now())
        update_cell(content_sheet, target_row_number, image_status_col, "PENDING")
        update_cell(content_sheet, target_row_number, audio_status_col, "PENDING")
        update_cell(content_sheet, target_row_number, youtube_status_col, "")
        update_cell(content_sheet, target_row_number, youtube_video_id_col, "")
        update_optional(content_sheet, target_row_number, video_type_col, video_type)
        update_optional(content_sheet, target_row_number, target_minutes_col, str(package.get("target_minutes", target_minutes)))
        update_optional(content_sheet, target_row_number, audience_col, "general audience")
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
