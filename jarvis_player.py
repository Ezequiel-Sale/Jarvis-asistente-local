import logging
import threading
import wave
from pathlib import Path

import pyaudio

from jarvis_theme import CYAN, ERROR, MUTED, TEXT


class AudioPlayerMixin:
        def _play_audio(self, path: str) -> None:
            if not self.audio_enabled:
                return
            audio_path = Path(path)
            if not audio_path.is_file():
                self._add_message("assistant", f"No encuentro el archivo de audio: {audio_path.name}")
                return

            self._stop_player(wait=True)
            if self.player_thread is not None and self.player_thread.is_alive():
                self._set_status("NO SE PUDO DETENER EL AUDIO ANTERIOR", ERROR)
                return

            try:
                with wave.open(str(audio_path), "rb") as audio_file:
                    self.player_duration = audio_file.getnframes() / audio_file.getframerate()
                    audio_file.getnchannels()
                    audio_file.getsampwidth()
            except (OSError, wave.Error, ZeroDivisionError) as error:
                logging.exception("No se pudo leer el WAV para reproducción.")
                self._set_status("ARCHIVO DE AUDIO INVÁLIDO", ERROR)
                self._add_message("assistant", f"No pude abrir ese WAV: {error}")
                return

            self.player_token += 1
            token = self.player_token
            self.player_path = str(audio_path)
            self.player_stop = threading.Event()
            self.player_resume = threading.Event()
            self.player_resume.set()
            self.player_paused = False
            self.player_track.configure(text=audio_path.name, fg=TEXT)
            self.player_time.configure(text=f"00:00 / {self._format_time(self.player_duration)}")
            self.player_progress.configure(value=0)
            self.player_toggle.configure(text="Ⅱ", state="normal")
            self.player_stop_button.configure(state="normal")
            self._set_status("REPRODUCIENDO AUDIO", CYAN)
            self.player_thread = threading.Thread(
                target=self._player_worker,
                args=(token, str(audio_path), self.player_stop, self.player_resume),
                daemon=True,
            )
            self.player_thread.start()

        def _player_worker(
            self,
            token: int,
            path: str,
            stop_event: threading.Event,
            resume_event: threading.Event,
        ) -> None:
            audio = None
            stream = None
            try:
                audio = pyaudio.PyAudio()
                with wave.open(path, "rb") as audio_file:
                    channels = audio_file.getnchannels()
                    sample_width = audio_file.getsampwidth()
                    rate = audio_file.getframerate()
                    total_frames = audio_file.getnframes()
                    stream = audio.open(
                        format=audio.get_format_from_width(sample_width),
                        channels=channels,
                        rate=rate,
                        output=True,
                    )
                    frames_played = 0
                    while not stop_event.is_set():
                        if not resume_event.is_set():
                            if stream.is_active():
                                stream.stop_stream()
                            resume_event.wait(timeout=0.1)
                            continue
                        if not stream.is_active():
                            stream.start_stream()

                        chunk = audio_file.readframes(1024)
                        if not chunk:
                            break
                        stream.write(chunk)
                        frames_played += len(chunk) // (sample_width * channels)
                        elapsed = frames_played / rate
                        self.events.put(
                            ("player_progress", token, elapsed, total_frames / rate)
                        )
            except Exception as error:
                logging.exception("Falló la reproducción interna del WAV.")
                self.events.put(("player_error", token, str(error)))
            finally:
                if stream is not None:
                    try:
                        if stream.is_active():
                            stream.stop_stream()
                    finally:
                        stream.close()
                if audio is not None:
                    audio.terminate()
                self.events.put(("player_finished", token))

        def _toggle_player(self) -> None:
            if self.player_resume is None or self.player_stop is None:
                return
            if self.player_paused:
                self.player_resume.set()
                self.player_paused = False
                self.player_toggle.configure(text="Ⅱ")
                self._set_status("REPRODUCIENDO AUDIO", CYAN)
            else:
                self.player_resume.clear()
                self.player_paused = True
                self.player_toggle.configure(text="▶")
                self._set_status("AUDIO EN PAUSA", MUTED)

        def _toggle_audio_enabled(self) -> None:
            self._execute_audio_toggle(
                not self.audio_enabled,
                "Botón: silenciar audio" if self.audio_enabled else "Botón: activar audio",
            )

        def _set_audio_enabled(self, enabled: bool) -> None:
            self.audio_enabled = enabled
            self.audio_toggle_button.configure(
                text="AUDIO ON" if enabled else "AUDIO OFF",
                fg=CYAN if enabled else MUTED,
            )
            if not enabled:
                self.player_paused = False
                self._stop_player()
                self.player_toggle.configure(text="▶", state="disabled")
                self.player_stop_button.configure(state="disabled")

        def _stop_player(self, wait: bool = False) -> None:
            if self.player_stop is not None:
                self.player_stop.set()
            if self.player_resume is not None:
                self.player_resume.set()
            if wait and self.player_thread is not None:
                self.player_thread.join(timeout=1)

        def _format_time(self, seconds: float) -> str:
            total_seconds = max(0, int(seconds))
            return f"{total_seconds // 60:02d}:{total_seconds % 60:02d}"
