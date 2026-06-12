# Runtime settings for Tiny Brave Tails.
# Secrets stay in GitHub Secrets, not in this file.
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output"
AUDIO_DIR = OUTPUT_DIR / "audio"
VISUAL_DIR = OUTPUT_DIR / "visuals"
VIDEO_DIR = OUTPUT_DIR / "videos"
METADATA_DIR = OUTPUT_DIR / "metadata"

DEFAULT_VIDEO_PRIVACY = "private"
DEFAULT_PRIVACY_STATUS = DEFAULT_VIDEO_PRIVACY
DEFAULT_MADE_FOR_KIDS = False
YOUTUBE_CATEGORY_ID = "24"

# Best FREE voice direction: Edge Neural with a soft emotional female narrator.
# For a truly human/premium voice, add ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID in GitHub Secrets.
DEFAULT_EDGE_TTS_VOICE = "en-US-AvaNeural"
DEFAULT_BEDTIME_VOICE = "en-US-AvaNeural"
DEFAULT_LONG_VOICE = "en-US-AvaNeural"

# New channel direction: emotional animal stories for a general audience, not made-for-kids.
CHANNEL_POSITIONING = "Cinematic emotional animal stories for a general audience"
MAIN_CHARACTER_NAME = "Toby"
MAIN_CHARACTER_BIBLE = (
    "Toby is an old emerald-green turtle with warm amber eyes, a small cracked shell mark shaped like a crescent, "
    "a faded blue scarf, tiny careful steps, and a quiet brave heart. He is slow, observant, wounded by old losses, "
    "and never gives direct lessons; he discovers them through pain, patience, and kindness."
)
STORY_UNIVERSE = "The Moonlit Forest"

# Visual direction used by story + video generation.
CINEMATIC_VISUAL_STYLE = (
    "premium animated movie still, cinematic 2D storybook illustration, dramatic composition, "
    "soft volumetric moonlight, emotional eyes, detailed environment, depth of field, warm color grading, "
    "beautiful children-animation film frame, no text, no watermark, vertical 9:16"
)

VIDEO_TYPES = {
    "short": {
        "category": "emotional_short",
        "duration_minutes": 1,
        "scene_count": 6,
        "shots_per_scene": 4,
        "voice": DEFAULT_LONG_VOICE,
        "mood": "cinematic_emotional",
        "made_for_kids": False,
    },
    "bedtime": {
        "category": "long_bedtime_story",
        "duration_minutes": 30,
        "scene_count": 28,
        "shots_per_scene": 4,
        "voice": DEFAULT_LONG_VOICE,
        "mood": "calm_cinematic",
        "made_for_kids": False,
    },
    "long_story": {
        "category": "long_emotional_story",
        "duration_minutes": 30,
        "scene_count": 32,
        "shots_per_scene": 4,
        "voice": DEFAULT_LONG_VOICE,
        "mood": "deep_cinematic_emotional",
        "made_for_kids": False,
    },
    "toby_collection": {
        "category": "toby_collection",
        "duration_minutes": 45,
        "scene_count": 42,
        "shots_per_scene": 4,
        "voice": DEFAULT_LONG_VOICE,
        "mood": "deep_cinematic_collection",
        "made_for_kids": False,
    },
    "adventure": {
        "category": "emotional_adventure_story",
        "duration_minutes": 30,
        "scene_count": 32,
        "shots_per_scene": 4,
        "voice": DEFAULT_LONG_VOICE,
        "mood": "cinematic_adventure",
        "made_for_kids": False,
    },
    "calming": {
        "category": "calming_story",
        "duration_minutes": 30,
        "scene_count": 30,
        "shots_per_scene": 4,
        "voice": DEFAULT_LONG_VOICE,
        "mood": "calm_reflective_cinematic",
        "made_for_kids": False,
    },
}
