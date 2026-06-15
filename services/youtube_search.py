import yt_dlp


class YouTubeSearch:

    def search(self, query, limit=10):

        ydl_opts = {
            "quiet": True,
            "extract_flat": True,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            results = ydl.extract_info(
                f"ytsearch{limit}:{query}",
                download=False
            )

        videos = []

        for entry in results["entries"]:
            videos.append({
                "title": entry["title"],
                "id": entry["id"],
                "url": f"https://youtube.com/watch?v={entry['id']}"
            })

        return videos