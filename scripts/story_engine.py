import random
from datetime import datetime

CHARACTERS = {
    "Benny": "a tiny brave orange fox with a blue scarf",
    "Luna": "a sleepy white bunny with a soft pink bow",
    "Milo": "a gentle little brown bear with a green backpack",
    "Coco": "a curious gray kitten with bright eyes",
    "Olive": "a wise little owl with round glasses",
}

BEDTIME_OPENINGS = [
    "The moon was glowing softly above the quiet forest.",
    "A gentle rain tapped on the leaves while the animals got ready to sleep.",
    "The stars blinked slowly as the forest became calm and blue.",
]

ADVENTURE_OPENINGS = [
    "A tiny sound came from behind the tall trees.",
    "The little bridge shook, and everyone froze.",
    "A golden feather floated across the path and disappeared into the forest.",
]

LESSONS = [
    "being brave does not mean you are never scared",
    "kindness can make a small heart feel big",
    "friends help each other when the path feels dark",
    "slow steps still move you forward",
    "sharing makes the adventure warmer",
]

COLORS = ["red", "blue", "yellow", "green", "purple", "orange"]
NUMBERS = list(range(1, 11))

def _repeat_to_length(lines, target_words):
    words = " ".join(lines).split()
    if len(words) >= target_words:
        return " ".join(words[:target_words])
    repeated = []
    while len(repeated) < target_words:
        repeated.extend(words)
    return " ".join(repeated[:target_words])

def generate_script(video_type: str, settings: dict) -> dict:
    character_name = random.choice(list(CHARACTERS.keys()))
    character = CHARACTERS[character_name]
    lesson = random.choice(LESSONS)
    duration = settings["duration_seconds"]

    if video_type == "colors":
        color = random.choice(COLORS)
        title = f"Learn {color.title()} With {character_name} | Animal Colors for Kids"
        base_lines = [
            f"Today, {character_name} is looking for the color {color}.",
            f"Can you say {color}? {color}. {color}. {color}.",
            f"{character_name} sees a {color} flower, a {color} balloon, and a {color} star.",
            f"Great job. We learned {color} with our animal friends.",
        ]
    elif video_type == "numbers":
        title = f"Count To 10 With {character_name} | Numbers for Kids"
        count_line = ", ".join(str(n) for n in NUMBERS)
        base_lines = [
            f"{character_name} wants to count forest treasures.",
            f"Let's count slowly together. {count_line}.",
            f"Again. {count_line}.",
            f"Wonderful counting. You did it.",
        ]
    elif video_type == "nursery":
        title = f"{character_name}'s Happy Forest Nursery Loop | Kids Animal Song"
        base_lines = [
            f"Clap, clap, little paws. {character_name} dances on the forest floor.",
            "Step to the left, step to the right, smile with your friends in the morning light.",
            "Again we sing, again we play, happy little animals start the day.",
        ]
    elif video_type == "calming":
        title = f"Calming Animal Music Story With {character_name} | Relaxing Kids Sleep Video"
        base_lines = [
            random.choice(BEDTIME_OPENINGS),
            f"{character_name}, {character}, sat beside a quiet river.",
            "The wind moved slowly. The leaves whispered softly.",
            f"Tonight, {character_name} learned that {lesson}.",
            "Breathe in slowly. Breathe out softly. The forest is safe.",
        ]
    elif video_type == "compilation":
        title = f"30 Minute Tiny Brave Tails Compilation | Animal Stories for Kids"
        base_lines = []
        for i in range(1, 7):
            c = random.choice(list(CHARACTERS.keys()))
            base_lines.extend([
                f"Story {i}. {c} begins a tiny forest adventure.",
                random.choice(ADVENTURE_OPENINGS),
                f"{c} learns that {random.choice(LESSONS)}.",
                "The animals smile, and the forest feels warm again.",
            ])
    elif video_type == "adventure":
        title = f"{character_name}'s Brave Forest Adventure | Kids Animal Story"
        base_lines = [
            random.choice(ADVENTURE_OPENINGS),
            f"{character_name}, {character}, wanted to help.",
            f"The path was not easy, but {character_name} took one small step.",
            f"By the end, {character_name} learned that {lesson}.",
        ]
    else:
        title = f"{character_name}'s Sleepy Forest Bedtime Story | Kids Sleep Story"
        base_lines = [
            random.choice(BEDTIME_OPENINGS),
            f"{character_name}, {character}, heard a tiny cry for help.",
            f"Even though the forest was dark, {character_name} walked gently forward.",
            f"That night, {character_name} learned that {lesson}.",
            "The moon smiled. The animals closed their eyes. The forest went to sleep.",
        ]

    target_words = max(120, int(duration * 1.9))  # slow TTS target
    narration = _repeat_to_length(base_lines, target_words)

    scene_count = max(4, duration // settings["scene_seconds"])
    scenes = []
    for i in range(scene_count):
        scenes.append({
            "scene_number": i + 1,
            "text": random.choice(base_lines),
            "character": character_name,
            "description": character,
            "mood": settings["mood"],
        })

    description = (
        f"{title}\n\n"
        "A gentle Tiny Brave Tails video for kids with soft narration, simple animation, "
        "and a positive lesson.\n\n"
        "#TinyBraveTails #KidsStories #AnimalStories #BedtimeStories"
    )

    tags = ["kids stories", "animal stories", "bedtime story", "learn colors", "nursery", "calming music"]

    return {
        "title": title,
        "description": description,
        "tags": tags,
        "category": settings["category"],
        "narration": narration,
        "scenes": scenes,
        "created_at": datetime.utcnow().isoformat(),
    }
