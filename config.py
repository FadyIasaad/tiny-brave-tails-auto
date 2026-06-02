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
DEFAULT_EDGE_TTS_VOICE = "en-US-JennyNeural"
DEFAULT_BEDTIME_VOICE = "en-US-AriaNeural"
DEFAULT_MADE_FOR_KIDS = False
YOUTUBE_CATEGORY_ID = "24"  # Entertainment. Avoids forcing the channel into Kids/Education positioning.

# New channel direction: emotional animal stories for a general audience, not made-for-kids.
CHANNEL_POSITIONING = "Emotional animal stories for a general audience"
MAIN_CHARACTER_NAME = "Toby"
MAIN_CHARACTER_BIBLE = (
    "Toby is an old emerald-green turtle with warm amber eyes, a small cracked shell mark shaped like a crescent, "
    "a faded blue scarf, tiny careful steps, and a quiet brave heart. He is slow, observant, wounded by old losses, "
    "and never gives direct lessons; he discovers them through pain, patience, and kindness."
)
STORY_UNIVERSE = "The Moonlit Forest"

VIDEO_TYPES = {
    "short": {
        "category": "emotional_short",
        "duration_minutes": 1,
        "scene_count": 7,
        "voice": "en-US-AriaNeural",
        "mood": "emotional",
        "made_for_kids": False,
    },
    "bedtime": {
        "category": "long_bedtime_story",
        "duration_minutes": 30,
        "scene_count": 28,
        "voice": "en-US-AriaNeural",
        "mood": "calm_deep",
        "made_for_kids": False,
    },
    "long_story": {
        "category": "long_emotional_story",
        "duration_minutes": 30,
        "scene_count": 32,
        "voice": "en-US-AriaNeural",
        "mood": "deep_emotional",
        "made_for_kids": False,
    },
    "toby_collection": {
        "category": "toby_collection",
        "duration_minutes": 45,
        "scene_count": 42,
        "voice": "en-US-AriaNeural",
        "mood": "deep_emotional_collection",
        "made_for_kids": False,
    },

    "adventure": {
        "category": "emotional_adventure_story",
        "duration_minutes": 30,
        "scene_count": 32,
        "voice": "en-US-AriaNeural",
        "mood": "deep_adventure",
        "made_for_kids": False,
    },
    "calming": {
        "category": "calming_story",
        "duration_minutes": 30,
        "scene_count": 30,
        "voice": "en-US-AriaNeural",
        "mood": "calm_reflective",
        "made_for_kids": False,
    },
}
