"""Small explicit experiment registry; optional providers are loaded by jobs."""

EXPERIMENTS = (
    {
        "id": "music-sketch",
        "name": "AI Music Video",
        "route": "/lab/music-sketch",
        "project_route": "/lab/music-sketch",
        "persistence": "project",
        "description": "Discover film moments and shape them into a music sequence.",
        "capabilities": ["search", "audio", "reels", "render"],
        "status": "experimental",
    },
    {
        "id": "visual-rhymes",
        "name": "Match Cuts",
        "route": "/match",
        "project_route": "/lab/visual-rhymes",
        "persistence": "session",
        "description": "Pick any frame; find frames across the library that cut from it seamlessly, and chain them.",
        "capabilities": ["search", "matching", "reels", "reframing"],
        "status": "experimental",
    },
    {
        "id": "transitions",
        "name": "Transitions",
        "route": "/lab/transitions",
        "project_route": "/lab/transitions",
        "persistence": "session",
        "description": "Shape swipes, luma reveals and flashes between your clips, then compare variants.",
        "capabilities": ["transitions", "render"],
        "status": "experimental",
    },
)
