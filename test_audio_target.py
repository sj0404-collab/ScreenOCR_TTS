# -*- coding: utf-8 -*-
"""
Тест авто-определения источника звука (active audio session).

Подменяем список аудиосессий Windows на детерминированный набор и проверяем,
что выбирается игра, а не браузер/плеер/наш python и не «молчащая» сессия.

Запуск: venv311\Scripts\python.exe test_audio_target.py
"""
import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import game_audio_capture as gac


class FakeVolume:
    def __init__(self, vol=0.8):
        self._vol = vol

    def GetMasterVolume(self):
        return self._vol

    def SetMasterVolume(self, v, ctx):
        self._vol = v

    def GetMute(self):
        return 0


class FakeProcess:
    def __init__(self, pid, name):
        self.pid = pid
        self._name = name

    def name(self):
        return self._name


class FakeSession:
    def __init__(self, pid, name, state=1, vol=0.8):
        self.ProcessId = pid
        self._process = FakeProcess(pid, name)
        self.State = state
        self.SimpleAudioVolume = FakeVolume(vol)

    @property
    def Process(self):
        return self._process


def install(sessions, foreground_pid=None):
    gac.game_audio._game_pid = None
    gac.game_audio._game_name = None
    gac.game_audio._get_foreground_pid = lambda: foreground_pid
    gac.AudioUtilities.GetAllSessions = staticmethod(lambda: sessions)


def check(name, condition):
    print(f"  [{'OK' if condition else 'ERR'}] {name}")
    return bool(condition)


def main():
    ok = True

    print("1) Активная сессия игры + фокус на ней")
    install([
        FakeSession(100, "chrome.exe", state=1, vol=0.5),
        FakeSession(200, "GenshinImpact.exe", state=1, vol=0.9),
    ], foreground_pid=200)
    found = gac.game_audio.find_game_process()
    ok &= check("выбран GenshinImpact.exe (фокус)", found and found["pid"] == 200)

    print("2) Фокус на другом окне, но есть игровая сессия")
    install([
        FakeSession(100, "chrome.exe", state=1, vol=0.5),
        FakeSession(300, "StarRail.exe", state=1, vol=0.9),
    ], foreground_pid=999)
    found = gac.game_audio.find_game_process()
    ok &= check("выбран StarRail.exe (игровое имя)", found and found["pid"] == 300)

    print("3) Только браузер и плеер — игру быть не должно")
    install([
        FakeSession(100, "chrome.exe", state=1, vol=0.5),
        FakeSession(110, "vlc.exe", state=1, vol=0.7),
    ], foreground_pid=100)
    found = gac.game_audio.find_game_process()
    ok &= check("ничего не выбрано (только denylist)", found is None)

    print("4) Молчащая сессия (State=Inactive) игнорируется")
    install([
        FakeSession(400, "SomeGame.exe", state=0, vol=0.9),
    ], foreground_pid=None)
    found = gac.game_audio.find_game_process()
    ok &= check("inactive-сессия отброшена", found is None)

    print("5) Незнакомое активное приложение берётся как источник")
    install([
        FakeSession(500, "WeirdAudioApp.exe", state=1, vol=0.4),
    ], foreground_pid=None)
    found = gac.game_audio.find_game_process()
    ok &= check("выбран WeirdAudioApp.exe (единственная активная)",
                found and found["pid"] == 500)

    print("6) Наш собственный python исключён")
    import os
    install([
        FakeSession(os.getpid(), "python.exe", state=1, vol=0.9),
        FakeSession(600, "HonkaiImpact3rd.exe", state=1, vol=0.9),
    ], foreground_pid=os.getpid())
    found = gac.game_audio.find_game_process()
    ok &= check("выбран HonkaiImpact3rd.exe, не self",
                found and found["pid"] == 600)

    print("7) Нулевая громкость — не источник")
    install([
        FakeSession(700, "MutedGame.exe", state=1, vol=0.0),
        FakeSession(800, "LoudGame.exe", state=1, vol=0.9),
    ], foreground_pid=None)
    found = gac.game_audio.find_game_process()
    ok &= check("выбран LoudGame.exe", found and found["pid"] == 800)

    print("\n[OK] ТЕСТ ПРОЙДЕН" if ok else "\n[ERR] ЕСТЬ ОШИБКИ")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
