import random


class QueueManager:
    def __init__(self):
        self.queue = []
        self.current_index = -1
        self.shuffle = False
        self.repeat = False  # repeat current track
        self._history = []  # shuffle history for previous_song

    def add_song(self, path):
        self.queue.append(path)

    def current_song(self):
        if 0 <= self.current_index < len(self.queue):
            return self.queue[self.current_index]
        return None

    def next_song(self):
        if not self.queue:
            return None
        if self.repeat:
            return self.current_song()
        if self.shuffle:
            self._history.append(self.current_index)
            candidates = [i for i in range(len(self.queue)) if i != self.current_index]
            self.current_index = random.choice(candidates) if candidates else self.current_index
        elif self.current_index < len(self.queue) - 1:
            self.current_index += 1
        else:
            return None
        return self.current_song()

    def previous_song(self):
        if not self.queue:
            return None
        if self.repeat:
            return self.current_song()
        if self.shuffle:
            if self._history:
                self.current_index = self._history.pop()
            # else stay on current
        elif self.current_index > 0:
            self.current_index -= 1
        else:
            return None
        return self.current_song()
