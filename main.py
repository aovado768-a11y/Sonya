import speech_recognition as sr
import webbrowser
import wikipedia
import datetime
import requests
import os
import tempfile
import time
import random
import pygame
from gtts import gTTS
import re
from typing import Optional
from openai import OpenAI
import threading
import queue
import glob
from difflib import get_close_matches
import pyautogui
import psutil
import platform
import ctypes
import winsound
import hashlib
import string
import winreg
from collections import defaultdict
from pathlib import Path

# ============ ЗАГРУЗКА .ENV ============
try:
    from dotenv import load_dotenv
    load_dotenv()
    DOTENV_AVAILABLE = True
except ImportError:
    DOTENV_AVAILABLE = False
    print("⚠️ python-dotenv не найден. Читаю переменные из системы.")

# ============ НАСТРОЙКИ ============
LANGUAGE = os.getenv("LANGUAGE", "ru-RU")
DEFAULT_CITY = os.getenv("DEFAULT_CITY", "Москва")
USER_NAME = os.getenv("USER_NAME", "Программист")

# ============ AI (Groq) ============
AI_API_KEY = os.getenv("GROQ_API_KEY")
AI_ENDPOINT = os.getenv("AI_BASE_URL", "https://api.groq.com/openai/v1")
AI_MODEL = os.getenv("AI_MODEL", "openai/gpt-oss-120b")

# ============ ПОГОДА ============
WEATHER_API_KEY = os.getenv("WEATHER_API_KEY")

# ============ САЙТЫ ============
KIMOGRAM_URL = os.getenv("KIMOGRAM_URL", "https://kimogram.pro")
VIBECONNECTER_URL = os.getenv("VIBECONNECTER_URL", "https://vibeconnecter.gt.tc")
SPOTIFY_URL = os.getenv("SPOTIFY_URL", "https://open.spotify.com")
GROQ_URL = os.getenv("GROQ_URL", "https://groq.com/")
KIMOWORLD_URL = os.getenv("KIMOWORLD_URL", "https://kimoworld.yhub.net/")

# ============ FIREBASE / KIMOGRAM ============
FIREBASE_DATABASE_URL = os.getenv("FIREBASE_DATABASE_URL")
ADMIN_URL = KIMOGRAM_URL
ADMIN_STATS_WAIT_SECONDS = 5
STATS_LAST_DATE_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "stats_last_date.txt"
)

# ============ ПАПКА С МУЗЫКОЙ ============
MUSIC_FOLDER = os.path.dirname(os.path.abspath(__file__))

# ============ ЗАДЕРЖКИ ============
POST_RESPONSE_DELAY = 0.25
POST_INSTANT_DELAY = 1.5

alarms = []


# ============ ГОЛОСОВОЙ ПОМОЩНИК ============
class FullAssistant:
    def __init__(self):
        pygame.mixer.init()

        self.recognizer = sr.Recognizer()
        self.microphone = None

        self.is_speaking = False

        self.listen_enabled = threading.Event()
        self.listen_enabled.set()

        self.ACTIVE_TIMEOUT = 5
        self.WAKE_WORDS = (
            "соня",
            "сонья",
            "сонька",
            "сонечка",
            "сонюшка",
            "саня",
            "сания",
        )
        self.assistant_active = False
        self.active_deadline = None

        self.command_queue = queue.Queue()

        self.current_music = None
        self.audio_files = list(set(self.scan_audio_files()))

        self.music_mode = False
        self.ai_enabled = True
        self.cleanup_scan_running = False

        self.recognizer.energy_threshold = 4000
        self.recognizer.dynamic_energy_threshold = True
        self.recognizer.pause_threshold = 0.5

        self.client = OpenAI(
            base_url=AI_ENDPOINT,
            api_key=AI_API_KEY or "missing-key"
        )

        if not AI_API_KEY:
            print(
                "⚠️ GROQ_API_KEY не задан. "
                "Соня будет использовать резервные ответы."
            )

        if not WEATHER_API_KEY:
            print("⚠️ WEATHER_API_KEY не задан. Погода работать не будет.")

        if not FIREBASE_DATABASE_URL:
            print("⚠️ FIREBASE_DATABASE_URL не задан. Статистика Kimogram недоступна.")

        print(f"🤖 AI: {AI_MODEL} через {AI_ENDPOINT}")

        self.conversation_history = []

        print(f"🎵 Найдено аудиофайлов: {len(self.audio_files)}")

        for file in self.audio_files:
            print(f"   - {os.path.basename(file)}")

        alarm_thread = threading.Thread(
            target=self.check_alarms,
            daemon=True
        )
        alarm_thread.start()

        music_check_thread = threading.Thread(
            target=self.check_music_end,
            daemon=True
        )
        music_check_thread.start()

    def check_music_end(self):
        while True:
            if (
                    self.music_mode
                    and self.current_music
                    and not self.is_speaking
                    and not pygame.mixer.music.get_busy()
            ):
                self.music_mode = False
                self.ai_enabled = True
                self.assistant_active = True
                self.active_deadline = time.monotonic() + self.ACTIVE_TIMEOUT
                print("\n🎵 Музыка закончилась, слушаю команды ещё 5 секунд")
            time.sleep(0.5)

    # ============ АУДИО ============
    def scan_audio_files(self) -> list:
        extensions = ['*.mp3', '*.wav', '*.ogg', '*.m4a', '*.flac']
        audio_files = []

        for ext in extensions:
            audio_files.extend(glob.glob(os.path.join(MUSIC_FOLDER, ext)))
            audio_files.extend(glob.glob(os.path.join(MUSIC_FOLDER, ext.upper())))

        return sorted(list(set(audio_files)))

    def normalize_name(self, name: str) -> str:
        name = name.strip().lower()
        name = name.replace(" ", "")
        return name

    def find_track_by_name(self, query: str) -> Optional[str]:
        query_lower = query.lower().strip()

        for word in [
            'включи', 'сыграй', 'поставь', 'песню', 'трек',
            'музыку', 'пожалуйста', 'включить', 'вруби'
        ]:
            query_lower = query_lower.replace(word, '')

        query_lower = query_lower.strip()
        query_normalized = self.normalize_name(query_lower)

        if not query_normalized or len(query_normalized) < 2:
            return None

        file_map = {}
        for file in self.audio_files:
            name_without_ext = os.path.splitext(os.path.basename(file))[0].lower()
            normalized = self.normalize_name(name_without_ext)
            file_map[normalized] = file

        if query_normalized in file_map:
            return file_map[query_normalized]

        for norm_name, file in file_map.items():
            if query_normalized in norm_name or norm_name in query_normalized:
                return file

        matches = get_close_matches(
            query_normalized, list(file_map.keys()), n=1, cutoff=0.6
        )
        if matches:
            return file_map.get(matches[0])

        return None

    def play_music(self, filepath: str):
        try:
            if pygame.mixer.music.get_busy():
                pygame.mixer.music.stop()

            pygame.mixer.music.load(filepath)
            pygame.mixer.music.play()

            self.current_music = filepath
            self.music_mode = True
            self.ai_enabled = False
            self.assistant_active = True
            self.active_deadline = None

            print("🎵 Режим музыки: AI отключён, доступны только: стоп, громкость, яркость")
            return True
        except Exception as e:
            print(f"Ошибка: {e}")
            return False

    def stop_music(self):
        pygame.mixer.music.stop()

        self.current_music = None
        self.music_mode = False
        self.ai_enabled = True
        self.assistant_active = True
        self.active_deadline = time.monotonic() + self.ACTIVE_TIMEOUT

        print("🎵 Музыка остановлена, слушаю команды ещё 5 секунд")

    def block_microphone(self, seconds: int):
        self.listen_enabled.clear()
        print(f"🔇 Блокировка прослушивания на {seconds} секунд...")

        def unlock():
            time.sleep(seconds)
            self.listen_enabled.set()
            print("🎤 Микрофон разблокирован")

        threading.Thread(target=unlock, daemon=True).start()

    def is_microphone_blocked(self) -> bool:
        return not self.listen_enabled.is_set()

    def speak_with_block(self, text: str, is_instant: bool = True):
        self.block_microphone(POST_RESPONSE_DELAY)
        self.is_speaking = True
        print(f"\n🤖 Соня: {text}")

        music_was_playing = (
                pygame.mixer.music.get_busy()
                and self.current_music
        )

        if music_was_playing:
            pygame.mixer.music.pause()

        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix='.mp3') as tmp_file:
                tmp_filename = tmp_file.name
                tts = gTTS(text=text, lang='ru', slow=False)
                tts.save(tmp_filename)

            pygame.mixer.music.load(tmp_filename)
            pygame.mixer.music.play()

            while pygame.mixer.music.get_busy():
                time.sleep(0.05)

            pygame.mixer.music.unload()

            try:
                os.unlink(tmp_filename)
            except:
                pass

        except Exception as e:
            print(f"❌ Ошибка: {e}")

        finally:
            if music_was_playing:
                pygame.mixer.music.unpause()
            self.is_speaking = False

    def extract_wake_command(self, command: str) -> Optional[str]:
        normalized = re.sub(r"[^а-яёa-z0-9]+", " ", command.lower()).strip()
        if not normalized:
            return None

        words = normalized.split()

        for index, word in enumerate(words):
            if word in self.WAKE_WORDS:
                remainder = " ".join(words[index + 1:]).strip()
                return remainder

        compact = normalized.replace(" ", "")
        if compact.startswith("соня"):
            return compact[4:].strip()
        if compact.startswith("саня"):
            return compact[4:].strip()

        return None

    # ============ МИКРОФОН ============
    def continuous_listen(self):
        if not self.microphone:
            self.microphone = sr.Microphone()

            with self.microphone as source:
                print("🔧 Калибровка микрофона...")
                self.recognizer.adjust_for_ambient_noise(source, duration=1.5)
                self.recognizer.energy_threshold = max(
                    self.recognizer.energy_threshold, 4000
                )
                print(f"✅ Микрофон готов! Порог: {self.recognizer.energy_threshold}")

        with self.microphone as source:
            while True:
                try:
                    if self.is_microphone_blocked() or self.is_speaking:
                        time.sleep(0.1)
                        continue

                    if (
                            self.assistant_active
                            and not self.music_mode
                            and self.active_deadline is not None
                            and time.monotonic() >= self.active_deadline
                    ):
                        self.assistant_active = False
                        self.active_deadline = None
                        print("\n😴 Режим ожидания: скажите «Соня»")

                    audio = self.recognizer.listen(
                        source, timeout=0.5, phrase_time_limit=5
                    )

                    print("\r⏳ Распознаю...", end="", flush=True)

                    try:
                        command = self.recognizer.recognize_google(
                            audio, language='ru-RU'
                        ).strip().lower()

                        if not command or len(command) <= 1:
                            continue

                        wake_match = self.extract_wake_command(command)

                        if self.music_mode:
                            self.command_queue.put(command)
                            continue

                        if not self.assistant_active:
                            if wake_match is not None:
                                self.assistant_active = True
                                self.active_deadline = time.monotonic() + self.ACTIVE_TIMEOUT
                                print("\n🎤 Соня активирована")
                                if wake_match:
                                    self.command_queue.put(wake_match)
                            continue

                        self.active_deadline = time.monotonic() + self.ACTIVE_TIMEOUT
                        self.command_queue.put(command)

                    except sr.UnknownValueError:
                        pass
                    except sr.RequestError as e:
                        print(f"\r⚠️ Ошибка распознавания: {e}")

                except sr.WaitTimeoutError:
                    continue
                except Exception as e:
                    print(f"\r⚠️ Ошибка: {e}", end="", flush=True)
                    time.sleep(0.2)

    # ============ AI ============
    def ask_phi(self, user_message: str) -> str:
        try:
            self.conversation_history.append({
                "role": "user",
                "content": user_message
            })

            if len(self.conversation_history) > 6:
                self.conversation_history = self.conversation_history[-6:]

            system_prompt = """
Ты — голосовая помощница.
Твоё имя — Соня.
Никогда не представляйся как Вика
или другим именем. Только Соня.
Говори о себе в женском роде.
Отвечай кратко (1-2 предложения),
по-русски, дружелюбно.
"""

            messages = [
                {"role": "system", "content": system_prompt},
                *self.conversation_history
            ]

            response = self.client.chat.completions.create(
                model=AI_MODEL,
                messages=messages,
                temperature=0.7,
                max_tokens=100,
                stream=False
            )

            assistant_response = response.choices[0].message.content

            self.conversation_history.append({
                "role": "assistant",
                "content": assistant_response
            })

            return assistant_response

        except Exception as e:
            error_text = str(e)

            if (
                    "github_models_retirement_brownout" in error_text
                    or "models.github.ai" in error_text
                    or "Error code: 410" in error_text
            ):
                print("❌ GitHub Models закрыт (HTTP 410). Используется резервный ответ.")
            else:
                print(f"❌ Ошибка AI: {e}")

            return self.fallback_response(user_message)

    def fallback_response(self, message: str) -> str:
        responses = {
            'привет': 'Привет!',
            'как дела': 'Отлично!',
            'кто ты': 'Я Соня, твоя помощница',
            'как тебя зовут': 'Меня зовут Соня',
            'твое имя': 'Соня',
            'спасибо': 'Пожалуйста',
        }

        for key, response in responses.items():
            if key in message.lower():
                return response

        return "Не поняла"

    # ============ СТАТИСТИКА ============
    def _firebase_get(self, path: str):
        try:
            url = f"{FIREBASE_DATABASE_URL.rstrip('/')}/{path.strip('/')}.json"
            response = requests.get(
                url,
                timeout=15,
                headers={"User-Agent": "Sonya-VoiceHelper/1.0"}
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            print(f"❌ Firebase ({path}): {e}")
            return None

    def get_kimogram_statistics(self):
        users = self._firebase_get("users") or {}
        promo_codes = self._firebase_get("promoCodes") or {}
        complaints = self._firebase_get("complaints") or {}

        excluded_users = {"admin", "SpamBotKimogram"}
        if isinstance(users, dict):
            total_users = sum(
                1 for username in users
                if username not in excluded_users
            )
        else:
            total_users = 0

        promo_count = len(promo_codes) if isinstance(promo_codes, dict) else 0
        complaints_count = len(complaints) if isinstance(complaints, dict) else 0

        now = datetime.datetime.now()
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_timestamp = int(today.timestamp() * 1000)
        daily_messages = 0

        general = self._firebase_get("messages/general") or {}
        if isinstance(general, dict):
            daily_messages += sum(
                1
                for message in general.values()
                if isinstance(message, dict)
                and isinstance(message.get("timestamp"), (int, float))
                and message.get("timestamp") >= today_timestamp
            )

        for section in ("private", "groups", "channels"):
            section_data = self._firebase_get(f"messages/{section}") or {}
            if not isinstance(section_data, dict):
                continue

            for messages in section_data.values():
                if not isinstance(messages, dict):
                    continue

                daily_messages += sum(
                    1
                    for message in messages.values()
                    if isinstance(message, dict)
                    and isinstance(message.get("timestamp"), (int, float))
                    and message.get("timestamp") >= today_timestamp
                )

        return {
            "promo_codes": promo_count,
            "users": total_users,
            "daily_messages": daily_messages,
            "complaints": complaints_count
        }

    def format_kimogram_statistics(self, stats: dict) -> str:
        return (
            f"Статистика Kimogram. "
            f"Пользователей: {stats['users']}. "
            f"Промокодов: {stats['promo_codes']}. "
            f"Сообщений за сегодня: {stats['daily_messages']}. "
            f"Жалоб: {stats['complaints']}."
        )

    def speak_kimogram_statistics(self):
        print("📊 Получаю статистику Kimogram...")
        stats = self.get_kimogram_statistics()

        if stats is None:
            self.speak_with_block("Не удалось получить статистику Kimogram.")
            return

        self.speak_with_block(self.format_kimogram_statistics(stats))

    def stats_already_spoken_today(self) -> bool:
        today = datetime.date.today().isoformat()
        try:
            with open(STATS_LAST_DATE_FILE, "r", encoding="utf-8") as file:
                return file.read().strip() == today
        except FileNotFoundError:
            return False
        except Exception as e:
            print(f"⚠️ Не удалось проверить дату статистики: {e}")
            return False

    def mark_stats_spoken_today(self):
        today = datetime.date.today().isoformat()
        try:
            with open(STATS_LAST_DATE_FILE, "w", encoding="utf-8") as file:
                file.write(today)
        except Exception as e:
            print(f"⚠️ Не удалось сохранить дату статистики: {e}")

    def tell_kimogram_stats_on_first_start(self):
        if self.stats_already_spoken_today():
            print("📊 Статистика Kimogram за сегодня уже была озвучена.")
            return

        print("📊 Первый запуск за сегодня — открываю Kimogram...")
        try:
            webbrowser.open(ADMIN_URL)
        except Exception as e:
            print(f"⚠️ Не удалось открыть Kimogram: {e}")

        print(f"⏳ Жду {ADMIN_STATS_WAIT_SECONDS} секунд...")
        time.sleep(ADMIN_STATS_WAIT_SECONDS)

        stats = self.get_kimogram_statistics()
        if stats is None:
            self.speak_with_block("Не удалось получить статистику Kimogram.")
            return

        self.mark_stats_spoken_today()
        self.speak_with_block(self.format_kimogram_statistics(stats))

    # ============ ПОГОДА ============
    def get_weather(self, city: str) -> str:
        try:
            url = (
                "http://api.openweathermap.org/data/2.5/weather"
                f"?q={city}"
                f"&units=metric"
                f"&lang=ru"
                f"&appid={WEATHER_API_KEY}"
            )

            response = requests.get(url, timeout=5)

            if response.status_code == 200:
                data = response.json()
                temp = round(data['main']['temp'])
                feels = round(data['main']['feels_like'])
                desc = data['weather'][0]['description']

                return (
                    f"Сейчас {desc}, температура "
                    f"{temp} градусов, ощущается "
                    f"как {feels}"
                )

            return f"Не нашла {city}"

        except:
            return "Ошибка погоды"

    # ============ УПРАВЛЕНИЕ КОМПЬЮТЕРОМ ============
    def control_brightness(self, action: str) -> str:
        try:
            import screen_brightness_control as sbc

            current = sbc.get_brightness()[0]

            if action == "максимум":
                sbc.set_brightness(100)
                return "Яркость на максимум"

            elif action == "минимум":
                sbc.set_brightness(0)
                return "Яркость на минимум"

            elif action == "увеличить":
                new = min(100, current + 20)
                sbc.set_brightness(new)
                return f"Увеличила яркость до {new}"

            elif action == "уменьшить":
                new = max(0, current - 20)
                sbc.set_brightness(new)
                return f"Уменьшила яркость до {new}"

        except Exception as e:
            print(f"❌ Ошибка управления яркостью: {e}")
            return "Не удалось изменить яркость"

        return "Команда не распознана"

    def set_brightness_value(self, value: int) -> str:
        try:
            import screen_brightness_control as sbc

            value = max(0, min(100, int(value)))
            sbc.set_brightness(value)

            if value == 90:
                return "Люмос"
            elif value == 14:
                return "Нокс!"

            return f"Яркость установлена на {value} процентов."

        except Exception as e:
            print(f"❌ Ошибка яркости: {e}")
            return "Не удалось изменить яркость"

    def control_volume(self, action: str) -> str:
        try:
            if action == "максимум":
                for _ in range(50):
                    pyautogui.press('volumeup')
                return "Громкость на максимум"

            elif action == "минимум":
                for _ in range(50):
                    pyautogui.press('volumedown')
                return "Громкость на минимум"

            elif action == "увеличить":
                for _ in range(10):
                    pyautogui.press('volumeup')
                return "Увеличила громкость"

            elif action == "уменьшить":
                for _ in range(10):
                    pyautogui.press('volumedown')
                return "Уменьшила громкость"

            elif action == "выключить":
                pyautogui.press('volumemute')
                return "Звук выключен"

        except:
            return "Не удалось изменить громкость"

        return "Команда не распознана"

    def shutdown_computer(self):
        self.speak_with_block("Выключаю компьютер через 60 секунд")

        def shutdown():
            time.sleep(60)
            os.system("shutdown /s /t 1")

        threading.Thread(target=shutdown, daemon=True).start()

    def restart_computer(self):
        self.speak_with_block("Перезагружаю компьютер через 10 секунд")
        os.system("shutdown /r /t 10")

    def lock_computer(self):
        ctypes.windll.user32.LockWorkStation()
        self.speak_with_block("Компьютер заблокирован")

    def take_screenshot(self):
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"screenshot_{timestamp}.png"

        screenshot = pyautogui.screenshot()
        screenshot.save(filename)

        return f"Скриншот сохранён как {filename}"

    def get_cpu_info(self) -> str:
        cpu_percent = psutil.cpu_percent()
        return f"Загрузка процессора {cpu_percent} процентов"

    # ============ БУДИЛЬНИК ============
    def set_alarm(self, time_str: str) -> str:
        try:
            numbers = re.findall(r'\d+', time_str)

            if len(numbers) >= 2:
                hour = int(numbers[0])
                minute = int(numbers[1])
            elif len(numbers) == 1:
                hour = int(numbers[0])
                minute = 0
            else:
                return "Не понял время"

            if 'вечера' in time_str or 'ночи' in time_str:
                if hour < 12:
                    hour += 12
            elif 'утра' in time_str and hour == 12:
                hour = 0

            now = datetime.datetime.now()
            alarm_time = now.replace(hour=hour, minute=minute, second=0)

            if alarm_time < now:
                alarm_time += datetime.timedelta(days=1)

            alarms.append({
                'time': alarm_time,
                'message': 'Будильник!',
                'active': True
            })

            return f"Будильник установлен на {alarm_time.strftime('%H:%M')}"

        except:
            return "Ошибка установки будильника"

    def check_alarms(self):
        while True:
            now = datetime.datetime.now()

            for alarm in alarms:
                if alarm['active'] and now >= alarm['time']:
                    alarm['active'] = False
                    self.speak_with_block(f"⏰ {alarm['message']}")

                    for _ in range(5):
                        winsound.Beep(1000, 500)
                        time.sleep(0.5)

            time.sleep(1)

    # ============ РАБОЧЕЕ МЕСТО ============
    def prepare_workspace(self):
        webbrowser.open(SPOTIFY_URL)
        webbrowser.open(KIMOWORLD_URL)
        time.sleep(1)
        webbrowser.open(KIMOGRAM_URL)
        time.sleep(1)
        webbrowser.open(GROQ_URL)

        os.system("start notepad.exe")
        os.system("start драйвера на комп.bat")

        return "Рабочее место готово"

    # ============ СПИСОК ТРЕКОВ ============
    def get_track_list_str(self) -> str:
        if self.audio_files:
            names = [
                os.path.splitext(os.path.basename(f))[0]
                for f in self.audio_files
            ]
            return "Доступные треки: " + ", ".join(names)

        return "Нет аудиофайлов"

    # ============ ЧИСТКА КОМПЬЮТЕРА ============
    CLEANUP_EXCLUDED_NAMES = {
        "$recycle.bin",
        "system volume information",
        "windows",
        "program files",
        "program files (x86)",
        "programdata",
        "recovery",
        "msocache",
        "boot",
        "perflogs",
        "appdata",
    }

    def _cleanup_is_excluded(self, path: str) -> bool:
        try:
            path = os.path.normcase(os.path.abspath(path))

            system_roots = [
                os.environ.get("WINDIR", r"C:\Windows"),
                os.environ.get("ProgramFiles", r"C:\Program Files"),
                os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                os.environ.get("ProgramData", r"C:\ProgramData"),
            ]

            for root in system_roots:
                root = os.path.normcase(os.path.abspath(root))
                if path == root or path.startswith(root + os.sep):
                    return True

            parts = {part.lower() for part in Path(path).parts}
            return bool(parts & self.CLEANUP_EXCLUDED_NAMES)

        except Exception:
            return True

    def _cleanup_drives(self) -> list:
        if os.name != "nt":
            return [os.path.abspath(os.sep)]

        drives = []
        for letter in string.ascii_uppercase:
            root = f"{letter}:\\"
            if not os.path.exists(root):
                continue

            try:
                drive_type = ctypes.windll.kernel32.GetDriveTypeW(root)
                if drive_type in (2, 3):
                    drives.append(root)
            except Exception:
                drives.append(root)

        return drives

    def _iter_cleanup_dirs(self):
        for drive in self._cleanup_drives():
            for current, dirs, files in os.walk(
                    drive, topdown=True, followlinks=False
            ):
                safe_dirs = []

                for name in dirs:
                    full = os.path.join(current, name)

                    if self._cleanup_is_excluded(full):
                        continue

                    try:
                        if os.path.islink(full):
                            continue
                    except OSError:
                        continue

                    safe_dirs.append(name)

                dirs[:] = safe_dirs
                yield current, dirs, files

    def find_empty_folders(self) -> list:
        result = []

        for current, dirs, files in self._iter_cleanup_dirs():
            if not dirs and not files:
                result.append(current)

        return result

    def _folder_signature(self, folder: str):
        total_size = 0
        file_count = 0
        dir_count = 0

        try:
            for current, dirs, files in os.walk(
                    folder, topdown=True, followlinks=False
            ):
                dirs[:] = [
                    d for d in dirs
                    if not self._cleanup_is_excluded(os.path.join(current, d))
                    and not os.path.islink(os.path.join(current, d))
                ]

                dir_count += len(dirs)

                for name in files:
                    path = os.path.join(current, name)
                    try:
                        if os.path.islink(path):
                            continue
                        total_size += os.path.getsize(path)
                        file_count += 1
                    except (OSError, PermissionError):
                        continue

            return total_size, file_count, dir_count

        except (OSError, PermissionError):
            return None

    def _hash_file(self, path: str, hasher):
        try:
            with open(path, "rb") as f:
                while True:
                    chunk = f.read(1024 * 1024)
                    if not chunk:
                        break
                    hasher.update(chunk)
            return True
        except (OSError, PermissionError):
            return False

    def _deep_folder_hash(self, folder: str):
        hasher = hashlib.sha256()
        entries = []

        try:
            for current, dirs, files in os.walk(
                    folder, topdown=True, followlinks=False
            ):
                dirs[:] = [
                    d for d in dirs
                    if not self._cleanup_is_excluded(os.path.join(current, d))
                    and not os.path.islink(os.path.join(current, d))
                ]

                rel_dir = os.path.relpath(current, folder)
                if rel_dir != ".":
                    entries.append(("D", rel_dir.replace("\\", "/")))

                for name in files:
                    path = os.path.join(current, name)
                    try:
                        if os.path.islink(path):
                            continue

                        rel = os.path.relpath(path, folder).replace("\\", "/")
                        size = os.path.getsize(path)
                        entries.append(("F", rel, size))
                    except (OSError, PermissionError):
                        continue

            for entry in sorted(entries):
                hasher.update(repr(entry).encode("utf-8", errors="replace"))

            for entry in sorted(
                    (e for e in entries if e[0] == "F"),
                    key=lambda x: x[1]
            ):
                path = os.path.join(folder, entry[1].replace("/", os.sep))

                if not self._hash_file(path, hasher):
                    return None

            return hasher.hexdigest()

        except (OSError, PermissionError):
            return None

    def find_duplicate_folders(self) -> list:
        candidates = defaultdict(list)

        print("📁 Этап 1/2: анализ папок...")

        for current, dirs, files in self._iter_cleanup_dirs():
            if not files and not dirs:
                continue

            signature = self._folder_signature(current)

            if signature and signature[1] > 0:
                candidates[signature].append(current)

        duplicate_groups = []

        print("🔐 Этап 2/2: проверяю содержимое кандидатов...")

        for signature, folders in candidates.items():
            if len(folders) < 2:
                continue

            by_hash = defaultdict(list)

            for folder in folders:
                folder_hash = self._deep_folder_hash(folder)
                if folder_hash:
                    by_hash[folder_hash].append(folder)

            for same_folders in by_hash.values():
                if len(same_folders) > 1:
                    duplicate_groups.append(same_folders)

        return duplicate_groups

    def _registry_uninstall_entries(self):
        entries = []

        locations = [
            (winreg.HKEY_LOCAL_MACHINE,
             r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_LOCAL_MACHINE,
             r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_CURRENT_USER,
             r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        ]

        for hive, subkey in locations:
            try:
                with winreg.OpenKey(hive, subkey, 0, winreg.KEY_READ) as root:
                    count = winreg.QueryInfoKey(root)[0]

                    for i in range(count):
                        try:
                            name = winreg.EnumKey(root, i)

                            with winreg.OpenKey(root, name) as app:
                                display_name = ""
                                install_location = ""

                                try:
                                    display_name = str(
                                        winreg.QueryValueEx(app, "DisplayName")[0]
                                    ).strip()
                                except OSError:
                                    pass

                                try:
                                    install_location = str(
                                        winreg.QueryValueEx(app, "InstallLocation")[0]
                                    ).strip().strip('"')
                                except OSError:
                                    pass

                                if display_name:
                                    entries.append({
                                        "name": display_name,
                                        "location": install_location
                                    })

                        except (OSError, FileNotFoundError):
                            continue

            except (OSError, FileNotFoundError):
                continue

        unique = {}
        for item in entries:
            key = (
                item["name"].lower(),
                os.path.normcase(item["location"])
            )
            unique[key] = item

        return list(unique.values())

    def _application_last_activity(self, location: str):
        if not location or not os.path.isdir(location):
            return None

        newest = None
        checked = 0

        try:
            for current, dirs, files in os.walk(
                    location, topdown=True, followlinks=False
            ):
                dirs[:] = [
                    d for d in dirs
                    if d.lower() not in {
                        "cache", "caches", "temp", "tmp", "logs", "__pycache__"
                    }
                    and not os.path.islink(os.path.join(current, d))
                ]

                for name in files:
                    if not name.lower().endswith(".exe"):
                        continue

                    path = os.path.join(current, name)

                    try:
                        atime = os.path.getatime(path)
                        if newest is None or atime > newest:
                            newest = atime
                        checked += 1
                        if checked >= 150:
                            break
                    except (OSError, PermissionError):
                        continue

                if checked >= 150:
                    break

        except (OSError, PermissionError):
            return None

        return newest

    def find_old_applications(self, days: int = 180) -> list:
        result = []
        cutoff = time.time() - days * 24 * 60 * 60
        seen = set()

        ignored_words = (
            "microsoft visual c++",
            "microsoft .net",
            "windows ",
            "security update",
            "update for",
            "driver",
            "hotfix",
            "runtime",
        )

        for app in self._registry_uninstall_entries():
            name = app["name"]
            location = app["location"]
            low = name.lower()

            if any(word in low for word in ignored_words):
                continue

            key = (low, os.path.normcase(location))

            if key in seen:
                continue

            seen.add(key)

            last_activity = self._application_last_activity(location)

            if last_activity is not None and last_activity < cutoff:
                result.append({
                    "name": name,
                    "location": location,
                    "last_activity": datetime.datetime.fromtimestamp(
                        last_activity
                    ).strftime("%d.%m.%Y")
                })

        result.sort(key=lambda item: item["last_activity"])

        return result

    def _print_cleanup_details(self, empty_folders, duplicate_groups, old_apps):
        print("\n" + "=" * 80)
        print("🧹 ОТЧЁТ ПО КОМАНДЕ «ЧИСТКА»")
        print("=" * 80)

        print(f"\n📂 ПУСТЫЕ ПАПКИ: {len(empty_folders)}")
        for path in empty_folders:
            print(f"   {path}")

        print(f"\n📑 ДУБЛИКАТЫ ПАПОК: {len(duplicate_groups)} групп")
        for index, group in enumerate(duplicate_groups, 1):
            print(f"\n   Группа {index}:")
            for path in group:
                print(f"      {path}")

        print(f"\n🕒 ДАВНО НЕ ЗАПУСКАВШИЕСЯ ПРИЛОЖЕНИЯ: {len(old_apps)}")
        for app in old_apps:
            print(
                f"   {app['name']} | "
                f"последняя активность: {app['last_activity']}"
            )
            print(f"      Путь: {app['location'] or 'не указан'}")

        print("\n⚠️ Ничего автоматически не удалялось.")
        print("=" * 80)

    def run_cleanup_scan(self):
        if getattr(self, "cleanup_scan_running", False):
            self.speak_with_block("Я уже проверяю компьютер.")
            return

        self.cleanup_scan_running = True

        def worker():
            try:
                self.speak_with_block(
                    "Начинаю проверку компьютера. Ничего удалять не буду."
                )

                print("\n🧹 Запущено сканирование компьютера...")

                empty_folders = self.find_empty_folders()
                duplicate_groups = self.find_duplicate_folders()
                old_apps = self.find_old_applications(days=180)

                self._print_cleanup_details(
                    empty_folders, duplicate_groups, old_apps
                )

                self.speak_with_block(
                    "Проверка завершена. "
                    f"Пустых папок найдено {len(empty_folders)}. "
                    f"Групп одинаковых папок {len(duplicate_groups)}. "
                    f"Давно не использовавшихся приложений-кандидатов "
                    f"{len(old_apps)}. "
                    "Все пути выведены в консоль."
                )

            except Exception as e:
                print(f"❌ Ошибка сканирования: {e}")
                self.speak_with_block(
                    "Во время проверки произошла ошибка. "
                    "Подробности выведены в консоль."
                )

            finally:
                self.cleanup_scan_running = False

        threading.Thread(
            target=worker, daemon=True, name="CleanupScanner"
        ).start()

    # ============ ОБРАБОТКА КОМАНД ============
    def process_command(self, command: str):
        print(f"\n🔍 Вы: {command}")

        self.block_microphone(POST_RESPONSE_DELAY)

        if command in ['стоп', 'замолчи', 'хватит', 'останови']:
            if self.is_speaking:
                pygame.mixer.music.stop()
                return True
            elif self.current_music and pygame.mixer.music.get_busy():
                self.stop_music()
                self.speak_with_block("Остановила")
                return True
            return True

        if any(word in command for word in ['пока', 'до свидания', 'выход']):
            self.speak_with_block("Пока, Арсений!")
            return False

        if any(word in command for word in ['люмус', 'люмос', 'люмоса']):
            result = self.set_brightness_value(90)
            self.speak_with_block(result)
            return True

        if 'нокс' in command:
            result = self.set_brightness_value(14)
            self.speak_with_block(result)
            return True

        if 'яркость' in command:
            if 'максимум' in command or 'макс' in command:
                self.speak_with_block(self.control_brightness("максимум"))
            elif 'минимум' in command or 'мин' in command:
                self.speak_with_block(self.control_brightness("минимум"))
            elif 'увеличь' in command:
                self.speak_with_block(self.control_brightness("увеличить"))
            elif 'уменьши' in command:
                self.speak_with_block(self.control_brightness("уменьшить"))
            return True

        if 'громкость' in command or 'звук' in command:
            if 'максимум' in command or 'макс' in command:
                self.speak_with_block(self.control_volume("максимум"))
            elif 'минимум' in command or 'мин' in command:
                self.speak_with_block(self.control_volume("минимум"))
            elif 'увеличь' in command:
                self.speak_with_block(self.control_volume("увеличить"))
            elif 'уменьши' in command:
                self.speak_with_block(self.control_volume("уменьшить"))
            elif 'выключи' in command:
                self.speak_with_block(self.control_volume("выключить"))
            return True

        if 'выключи компьютер' in command:
            self.shutdown_computer()
            return True

        if 'перезагруз' in command:
            self.restart_computer()
            return True

        if 'заблокируй' in command:
            self.lock_computer()
            return True

        if 'скриншот' in command:
            result = self.take_screenshot()
            self.speak_with_block(result)
            return True

        if 'процессор' in command or 'загрузка' in command:
            info = self.get_cpu_info()
            self.speak_with_block(info)
            return True

        if 'подготовь рабочее место' in command:
            result = self.prepare_workspace()
            self.speak_with_block(result)
            return True

        if command.strip() in [
            "чистка", "чистить", "начни чистку",
            "проведи чистку", "проверь компьютер",
        ] or command.strip().startswith("чистка "):
            self.run_cleanup_scan()
            return True

        if self.music_mode:
            print(
                "🎵 Режим музыки: AI отключён. "
                "Доступны только: стоп, громкость, "
                "яркость, выключи компьютер"
            )
            return True

        if any(
                word in command
                for word in [
                    'какие песни', 'список треков', 'что есть',
                    'покажи треки', 'список', 'какие треки'
                ]
        ):
            self.speak_with_block(self.get_track_list_str())
            return True

        music_triggers = ['включи', 'сыграй', 'поставь', 'вруби']

        if any(word in command for word in music_triggers):
            track_name = command

            for word in (
                    music_triggers
                    + ['песню', 'трек', 'музыку', 'мелодию', 'пожалуйста']
            ):
                track_name = track_name.replace(word, '')

            track_name = re.sub(r'\s+', ' ', track_name).strip()

            print(f"🔍 Ищу трек: '{track_name}'")

            if track_name and len(track_name) >= 1:
                found = self.find_track_by_name(track_name)

                if found:
                    name = os.path.splitext(os.path.basename(found))[0]
                    self.speak_with_block(f"Включаю {name}")
                    self.play_music(found)
                    return True
                else:
                    self.speak_with_block(f"Не нашла трек: {track_name}")
                    return True
            else:
                self.speak_with_block("Какую песню включить?")
                return True

        if 'открой' in command:
            if 'кима world' in command or 'кима worl' in command:
                webbrowser.open(KIMOWORLD_URL)
                self.speak_with_block("Открыла KIMOworld")
            elif 'ютуб' in command or 'youtube' in command:
                webbrowser.open("https://youtube.com")
                self.speak_with_block("Открыла YouTube")
            elif 'гугл' in command or 'google' in command:
                webbrowser.open("https://google.com")
                self.speak_with_block("Открыла Google")
            elif 'вк' in command or 'вконтакте' in command:
                webbrowser.open("https://vk.com")
                self.speak_with_block("Открыла ВК")
            elif 'кимограм' in command:
                webbrowser.open(KIMOGRAM_URL)
                self.speak_with_block("Открыла Kimogram")
            elif 'спотифай' in command or 'spotify' in command:
                webbrowser.open(SPOTIFY_URL)
                self.speak_with_block("Открыла Spotify")
            elif 'дим сик' in command or 'deepseek' in command:
                webbrowser.open(GROQ_URL)
                self.speak_with_block("Открыла Groq")
            else:
                self.speak_with_block("Какой сайт открыть?")
            return True

        if (
                "статистика" in command
                or "статистика кимограм" in command
                or "статистика кимограма" in command
        ):
            self.speak_kimogram_statistics()
            return True

        if 'погода' in command:
            city = DEFAULT_CITY
            city_match = re.search(r'в (\w+)', command)

            if city_match:
                city = city_match.group(1)

            weather = self.get_weather(city)
            self.speak_with_block(weather)
            return True

        if any(word in command for word in ['время', 'который час']):
            now = datetime.datetime.now()
            time_str = f"{now.hour} часов {now.minute} минут"
            self.speak_with_block(time_str)
            return True

        if any(word in command for word in ['дата', 'число']):
            now = datetime.datetime.now()
            months = [
                'января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
                'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'
            ]
            date_str = f"Сегодня {now.day} {months[now.month - 1]} {now.year} года"
            self.speak_with_block(date_str)
            return True

        if 'кто такой' in command or 'что такое' in command:
            query = re.sub(r'кто такой|что такое', '', command).strip()

            if query:
                try:
                    wikipedia.set_lang('ru')
                    summary = wikipedia.summary(query, sentences=1)
                    self.speak_with_block(summary[:200])
                except:
                    self.speak_with_block(f"Не нашла информацию про {query}")
            return True

        if 'помощь' in command or 'что ты умеешь' in command:
            help_text = """
Я умею:

🎵 МУЗЫКА:
включи трек,
какие песни есть,
стоп

💻 КОМПЬЮТЕР:
яркость,
громкость,
выключи,
перезагрузи,
заблокируй,
скриншот

✨ МАГИЧЕСКАЯ ЯРКОСТЬ:
Люмос — 90 процентов
Нокс — 14 процентов

⏰ БУДИЛЬНИК:
будильник на 7:30 утра

🌐 САЙТЫ:
открой ютуб,
гугл,
вк,
спотифай,
кимограм

📋 ДРУГОЕ:
погода,
время,
дата,
кто такой
"""
            self.speak_with_block(help_text)
            return True

        if 'привет' in command:
            self.speak_with_block("Привет, программист! Чем помочь?")
            return True

        if any(
                word in command
                for word in ['как тебя зовут', 'твое имя', 'кто ты']
        ):
            self.speak_with_block("Меня зовут Соня!")
            return True

        if self.ai_enabled:
            print("🧠 Думаю через AI...")
            response = self.ask_phi(command)
            self.speak_with_block(response)
        else:
            print("🤖 AI отключён (режим музыки)")

        return True

    # ============ ЗАПУСК ============
    def run(self):
        print("\n" + "=" * 80)
        print("🎵 СОНЯ - ПОЛНЫЙ АССИСТЕНТ")
        print("=" * 80)
        print("🎵 МУЗЫКА: включи трек, какие песни есть")
        print("   ВО ВРЕМЯ МУЗЫКИ AI ОТКЛЮЧАЕТСЯ!")
        print("   Доступны: стоп, громкость, яркость, выключи компьютер")
        print("   После 'стоп' AI снова включается")
        print("=" * 80)
        print("💻 УПРАВЛЕНИЕ: яркость, громкость, выключи, перезагрузи, скриншот")
        print("✨ МАГИЯ: Люмос = 90%, Нокс = 14%")
        print("⏰ БУДИЛЬНИК: будильник на 7:30 утра")
        print("🧹 ЧИСТКА: скажи 'Чистка' для поиска пустых папок, дубликатов и старых приложений")
        print("💬 ДРУГОЕ: погода, время, дата, вопросы")
        print("=" * 80)

        listen_thread = threading.Thread(
            target=self.continuous_listen, daemon=True
        )
        listen_thread.start()

        time.sleep(1)

        self.speak_with_block("Соня запущена!")
        self.tell_kimogram_stats_on_first_start()

        while True:
            try:
                if not self.command_queue.empty():
                    command = self.command_queue.get()

                    should_continue = self.process_command(command)

                    if should_continue is False:
                        break

                    if self.music_mode:
                        self.active_deadline = None
                    else:
                        self.assistant_active = True
                        self.active_deadline = time.monotonic() + self.ACTIVE_TIMEOUT

                time.sleep(0.05)

            except KeyboardInterrupt:
                print("\n\n👋 Выход...")
                self.speak_with_block("Пока!")
                break

            except Exception as e:
                print(f"\n❌ Ошибка: {e}")
                time.sleep(0.1)


def main():
    assistant = FullAssistant()
    assistant.run()


if __name__ == "__main__":
    main()