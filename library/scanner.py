import os


class MusicLibrary:
    def __init__(self):
        self.albums = {}

    def scan_folder(self, folder_path):
        self.albums = {}

        for root, dirs, files in os.walk(folder_path):
            try:
                songs = [
                    os.path.join(root, f)
                    for f in sorted(files)
                    if f.lower().endswith((".mp3", ".wav", ".flac", ".m4a", ".aiff", ".aif", ".alac", ".ogg", ".opus", ".wma", ".ape"))
                ]
                if songs:
                    self.albums[os.path.basename(root)] = songs
            except OSError as e:
                print("Scan error:", e)