from pathlib import Path

ROOT = Path(__file__).parent
OUTPUT_DIR = ROOT / "output"
AUDIO_DIR = OUTPUT_DIR / "audio"
VISUAL_DIR = OUTPUT_DIR / "visuals"
VIDEO_DIR = OUTPUT_DIR / "videos"
METADATA_DIR = OUTPUT_DIR / "metadata"

DEFAULT_PRIVACY_STATUS = "private"  # Change to "public" only when you trust the system.

VIDEO_TYPES = {
    "bedtime": {
        "category": "Bedtime Stories",
        "duration_seconds": 600,
        "voice": "en-US-AriaNeural",
        "mood": "calm",
        "scene_seconds": 12,
        "title_prefix": "Sleepy Animal Bedtime Story",
    },
    "nursery": {
        "category": "Nursery Loops",
        "duration_seconds": 900,
        "voice": "en-US-AnaNeural",
        "mood": "happy",
        "scene_seconds": 8,
        "title_prefix": "Nursery Animal Loop",
    },
    "colors": {
        "category": "Learn Colors",
        "duration_seconds": 600,
        "voice": "en-US-AnaNeural",
        "mood": "bright",
        "scene_seconds": 7,
        "title_prefix": "Learn Colors With Animals",
    },
    "numbers": {
        "category": "Learn Numbers ABC",
        "duration_seconds": 600,
        "voice": "en-US-AnaNeural",
        "mood": "educational",
        "scene_seconds": 7,
        "title_prefix": "Learn Numbers With Animals",
    },
    "adventure": {
        "category": "Animal Adventures",
        "duration_seconds": 480,
        "voice": "en-US-ChristopherNeural",
        "mood": "adventure",
        "scene_seconds": 10,
        "title_prefix": "Brave Animal Adventure",
    },
    "calming": {
        "category": "Calming Music",
        "duration_seconds": 1800,
        "voice": "en-US-JennyNeural",
        "mood": "soft",
        "scene_seconds": 15,
        "title_prefix": "Calming Animal Music Story",
    },
    "compilation": {
        "category": "Compilations",
        "duration_seconds": 1800,
        "voice": "en-US-AriaNeural",
        "mood": "mixed",
        "scene_seconds": 10,
        "title_prefix": "30 Minute Animal Stories Compilation",
    },
}
