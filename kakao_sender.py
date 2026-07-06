"""
kakao_sender.py — 카카오톡 PC 자동 발송 (단일 파일, 독립 실행)
================================================================

Windows 데스크톱 카카오톡을 win32 API로 제어해서 지정한 채팅방에
메시지를 붙여넣고 전송한다. 다른 파일에 의존하지 않는다.

동작 방식
---------
카카오톡 창을 찾아 포커스 → 채팅 목록에서 방 이름으로 검색 → 방을 열고
→ 메시지를 클립보드로 복사해 입력창에 붙여넣기(Ctrl+V) → Enter로 전송.
(win32gui/win32api 로 창 핸들을 찾고 WM_SETTEXT/마우스/키 이벤트를 보냄)

요구 사항
---------
- Windows 전용 (win32 API 사용)
- 카카오톡 PC 버전 설치 + **로그인 상태**여야 함
- 보낼 채팅방이 미리 존재하고, 넘기는 방 이름이 카카오톡에 보이는 이름과 정확히 일치해야 함
- 설치:  pip install pywin32 pyperclip

빠른 사용
---------
    from kakao_sender import send_kakao_message

    # 즉시 전송 (Enter까지 자동)
    send_kakao_message("나와의 채팅", "안녕하세요", send_now=True)

    # 여러 방에 전송 — 리스트 또는 콤마로 구분한 문자열
    send_kakao_message(["방A", "방B"], "공지입니다", send_now=True)
    send_kakao_message("방A,방B", "공지입니다", send_now=True)

    # 전송하지 않고 입력창에 넣어만 두기 (사용자가 직접 Enter)
    send_kakao_message("나와의 채팅", "초안", send_now=False)

세부 설정 (선택)
----------------
    from kakao_sender import send_kakao_message, KakaoTalkConfig

    config = KakaoTalkConfig(
        executable_path=r"C:\\Program Files\\Kakao\\KakaoTalk\\KakaoTalk.exe",
        chat_open_wait_seconds=3.0,   # 방 열림 대기
        close_after_send=True,        # 전송 후 방 창 닫기
    )
    send_kakao_message("나와의 채팅", "안녕", send_now=True, config=config)

주의
----
- 전송 중 몇 초간 마우스/키보드를 자동 조작하므로 그동안 PC를 건드리지 말 것.
- 방을 못 찾거나 입력창을 못 찾으면 KakaoTalkControlError 를 던진다.
- 클립보드를 사용하므로 전송 순간의 클립보드 내용이 바뀔 수 있다.

공개 API
--------
    send_kakao_message(chat_name, message_, send_now=False, *, config=None)
    KakaoTalkService(config=None)      # .send_message(name, msg) / .open_chat_and_input_message(name, msg)
    KakaoTalkConfig(...)               # 타이밍/경로 등 설정 dataclass
    KakaoTalkControlError               # 제어 실패 예외
"""

from __future__ import annotations

import subprocess
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

DEFAULT_EXECUTABLE_PATHS = (
    Path("C:\\Program Files (x86)\\Kakao\\KakaoTalk\\KakaoTalk.exe"),
    Path("C:\\Program Files\\Kakao\\KakaoTalk\\KakaoTalk.exe"),
)

KAKAO_TITLE = "카카오톡"
TEST_CHAT_NAME = "변대웅"
TEST_MESSAGE = "테스트"


class KakaoTalkControlError(RuntimeError):
    pass


@dataclass(slots=True)
class KakaoTalkConfig:
    executable_path: str | Path | None = None
    startup_timeout_seconds: float = 10.0
    search_wait_seconds: float = 1.0
    chat_open_wait_seconds: float = 3.0
    chat_close_wait_seconds: float = 3.0
    action_delay_seconds: float = 0.5
    image_upload_wait: float = 3.0        # 이미지 전송 후 업로드 완료 대기(순서 뒤바뀜 방지)
    close_after_send: bool = True

    chat_tab_x_offset: int = 28
    chat_tab_y_offset: int = 105


def send_kakao_message(
    chat_name: str | Iterable[str],
    message_: str,
    send_now: bool = False,
    *,
    config: KakaoTalkConfig | None = None,
):
    service = KakaoTalkService(config)
    chat_names = _normalize_chat_names(chat_name)
    result = None

    if not chat_names:
        raise ValueError("chat_name must be a non-empty string or iterable.")

    for target_chat_name in chat_names:
        if send_now:
            service.send_message(target_chat_name, message_)
            result = None
        else:
            result = service.open_chat_and_input_message(target_chat_name, message_)

    return result


def send_kakao_sequence(
    chat_name: str | Iterable[str],
    items: list,
    *,
    config: KakaoTalkConfig | None = None,
):
    """여러 항목(text/image)을 지정 방(들)에 한 번에 순차 전송.
    items = [{"type": "text", "text": "..."} | {"type": "image", "path": "..."}]"""
    service = KakaoTalkService(config)
    chat_names = _normalize_chat_names(chat_name)
    if not chat_names:
        raise ValueError("chat_name must be a non-empty string or iterable.")
    for target_chat_name in chat_names:
        service.send_sequence(target_chat_name, items)


def _normalize_chat_names(chat_name: str | Iterable[str]) -> list[str]:
    if isinstance(chat_name, str):
        names = chat_name.split(",")
    else:
        names = list(chat_name)

    normalized_names = []
    for name in names:
        normalized_name = str(name).strip()
        if not normalized_name:
            continue
        normalized_names.append(normalized_name)

    return normalized_names


class KakaoTalkService:
    def __init__(self, config: KakaoTalkConfig | None = None):
        self.config = config or KakaoTalkConfig()

        try:
            import pyperclip
            import win32api
            import win32con
            import win32gui
        except ModuleNotFoundError as error:
            raise KakaoTalkControlError(
                "pyperclip and pywin32 are required."
            ) from error

        self.pyperclip = pyperclip
        self.win32api = win32api
        self.win32con = win32con
        self.win32gui = win32gui

    def open_chat_and_input_message(self, chat_name: str, message_: str) -> int:
        chat_name = self._require_text(chat_name, "chat_name")
        message_ = self._require_text(message_, "message_")

        main_window = self._ensure_kakaotalk_running()
        time.sleep(self.config.action_delay_seconds)
        self._focus_window(main_window)
        time.sleep(self.config.action_delay_seconds)
        self._go_to_chat_tab(main_window)
        time.sleep(self.config.action_delay_seconds)
        self._open_room(chat_name)
        time.sleep(self.config.action_delay_seconds)

        chat_window = self._find_chat_window(chat_name)
        time.sleep(self.config.action_delay_seconds)
        message_input = self._find_message_input(chat_window)
        time.sleep(self.config.action_delay_seconds)
        self._clear_message_input(message_input)
        self._paste_text(message_)
        time.sleep(self.config.action_delay_seconds)

        return chat_window

    def send_message(self, chat_name: str, message_: str) -> None:
        chat_window = self.open_chat_and_input_message(chat_name, message_)
        message_input = self._find_message_input(chat_window)
        time.sleep(self.config.action_delay_seconds)
        self._click_window(message_input)
        self._press_key(self.win32con.VK_RETURN)

        if self.config.close_after_send:
            time.sleep(self.config.action_delay_seconds)
            self.win32gui.PostMessage(chat_window, self.win32con.WM_CLOSE, 0, 0)
            self._wait_for_window_closed(chat_window)

    def send_sequence(self, chat_name: str, items: list) -> None:
        """한 방에 여러 항목(text/image)을 순차 전송 — 사진 말풍선, 텍스트 말풍선 번갈아."""
        chat_name = self._require_text(chat_name, "chat_name")
        main_window = self._ensure_kakaotalk_running()
        time.sleep(self.config.action_delay_seconds)
        self._focus_window(main_window)
        time.sleep(self.config.action_delay_seconds)
        self._go_to_chat_tab(main_window)
        time.sleep(self.config.action_delay_seconds)
        self._open_room(chat_name)
        time.sleep(self.config.action_delay_seconds)
        chat_window = self._find_chat_window(chat_name)
        time.sleep(self.config.action_delay_seconds)
        message_input = self._find_message_input(chat_window)
        time.sleep(self.config.action_delay_seconds)

        for item in items or []:
            if item.get("type") == "image" and item.get("path"):
                self._clear_message_input(message_input)
                self._paste_image(item["path"])
            else:
                text = (item.get("text") or "").strip()
                if not text:
                    continue
                self._clear_message_input(message_input)
                self._paste_text(text)
            time.sleep(self.config.action_delay_seconds)
            self._click_window(message_input)
            self._press_key(self.win32con.VK_RETURN)
            # 이미지는 업로드 완료까지 대기해야 순서가 안 뒤바뀜(텍스트는 즉시 전송, 이미지는 업로드 지연).
            time.sleep(self.config.image_upload_wait if (item.get("type") == "image")
                       else self.config.action_delay_seconds)

        if self.config.close_after_send:
            time.sleep(self.config.action_delay_seconds)
            self.win32gui.PostMessage(chat_window, self.win32con.WM_CLOSE, 0, 0)
            self._wait_for_window_closed(chat_window)

    def _paste_image(self, image_path: str) -> None:
        """이미지 파일을 클립보드(CF_DIB)에 넣고 붙여넣기 → 입력창에 사진 첨부."""
        import io
        import win32clipboard
        from PIL import Image

        image = Image.open(image_path)
        if image.mode != "RGB":
            image = image.convert("RGB")
        buffer = io.BytesIO()
        image.save(buffer, "BMP")
        data = buffer.getvalue()[14:]          # BMP 파일헤더(14바이트) 제거 → CF_DIB
        buffer.close()

        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32clipboard.CF_DIB, data)
        finally:
            win32clipboard.CloseClipboard()

        self._hotkey(self.win32con.VK_CONTROL, ord("V"))
        time.sleep(self.config.action_delay_seconds * 2)   # 사진 미리보기 로딩 대기

    def _ensure_kakaotalk_running(self) -> int:
        main_window = self._find_main_window()
        if main_window:
            return main_window

        subprocess.Popen([str(self._resolve_executable_path())])
        deadline = time.monotonic() + self.config.startup_timeout_seconds
        while time.monotonic() < deadline:
            main_window = self._find_main_window()
            if main_window:
                return main_window
            time.sleep(0.2)

        raise KakaoTalkControlError("KakaoTalk main window was not found.")

    def _focus_window(self, window: int) -> None:
        self.win32gui.ShowWindow(window, self.win32con.SW_RESTORE)
        self.win32gui.BringWindowToTop(window)
        self._press_key(self.win32con.VK_MENU)
        try:
            self.win32gui.SetForegroundWindow(window)
        except Exception:
            pass
        time.sleep(self.config.action_delay_seconds)

    def _go_to_chat_tab(self, main_window: int) -> None:
        left, top, _, _ = self.win32gui.GetWindowRect(main_window)
        self._click(
            left + self.config.chat_tab_x_offset,
            top + self.config.chat_tab_y_offset,
        )

    def _open_room(self, chat_name: str) -> None:
        search_input = self._find_room_search_input()
        self._set_text(search_input, "")
        time.sleep(self.config.action_delay_seconds)
        self._set_text(search_input, chat_name)
        time.sleep(self.config.search_wait_seconds)
        self._send_return(search_input)
        time.sleep(self.config.chat_open_wait_seconds)
        self._set_text(search_input, "")

    def _wait_for_window_closed(self, window: int) -> None:
        deadline = time.monotonic() + self.config.chat_close_wait_seconds
        while time.monotonic() < deadline:
            if not self.win32gui.IsWindow(window):
                return
            time.sleep(0.1)
        time.sleep(self.config.action_delay_seconds)

    def _find_room_search_input(self) -> int:
        main_window = self._find_main_window()
        if not main_window:
            raise KakaoTalkControlError("KakaoTalk main window was not found.")

        child_window = self.win32gui.FindWindowEx(
            main_window,
            None,
            "EVA_ChildWindow",
            None,
        )
        first_panel = self.win32gui.FindWindowEx(
            child_window,
            None,
            "EVA_Window",
            None,
        )
        search_panel = self.win32gui.FindWindowEx(
            child_window,
            first_panel,
            "EVA_Window",
            None,
        )
        search_input = self.win32gui.FindWindowEx(search_panel, None, "Edit", None)

        if not search_input:
            raise KakaoTalkControlError("KakaoTalk room search input was not found.")

        return search_input

    def _find_message_input(self, chat_window: int) -> int:
        if not chat_window:
            raise KakaoTalkControlError("KakaoTalk chat window was not found.")

        message_input = self.win32gui.FindWindowEx(
            chat_window,
            None,
            "RichEdit50W",
            None,
        )

        if not message_input:
            raise KakaoTalkControlError("KakaoTalk message input was not found.")

        return message_input

    def _find_chat_window(self, chat_name: str) -> int:
        chat_window = self.win32gui.FindWindow(None, chat_name)
        if chat_window:
            return chat_window

        deadline = time.monotonic() + self.config.chat_open_wait_seconds
        while time.monotonic() < deadline:
            chat_window = self.win32gui.FindWindow(None, chat_name)
            if chat_window:
                return chat_window
            time.sleep(0.2)

        raise KakaoTalkControlError(f"KakaoTalk chat window was not opened: {chat_name}")

    def _find_main_window(self) -> int:
        main_window = self.win32gui.FindWindow(None, KAKAO_TITLE)
        if main_window:
            return main_window
        return self.win32gui.FindWindow(None, "KakaoTalk")

    def _resolve_executable_path(self) -> Path:
        if self.config.executable_path:
            executable_path = Path(self.config.executable_path).expanduser()
            if executable_path.exists():
                return executable_path
            raise KakaoTalkControlError(f"KakaoTalk.exe not found: {executable_path}")

        for executable_path in DEFAULT_EXECUTABLE_PATHS:
            if executable_path.exists():
                return executable_path

        raise KakaoTalkControlError("KakaoTalk.exe was not found.")

    def _set_text(self, window: int, text: str) -> None:
        self.win32api.SendMessage(window, self.win32con.WM_SETTEXT, 0, text)

    def _paste_text(self, text: str) -> None:
        previous_text = self._safe_clipboard_text()
        self.pyperclip.copy(text)
        try:
            self._hotkey(self.win32con.VK_CONTROL, ord("V"))
            time.sleep(self.config.action_delay_seconds)
        finally:
            if previous_text is not None:
                self.pyperclip.copy(previous_text)

    def _safe_clipboard_text(self) -> str | None:
        try:
            return self.pyperclip.paste()
        except Exception:
            return None

    def _click_window(self, window: int) -> None:
        left, top, right, bottom = self.win32gui.GetWindowRect(window)
        self._click(left + (right - left) // 2, top + (bottom - top) // 2)

    def _clear_message_input(self, message_input: int) -> None:
        self._click_window(message_input)
        self._hotkey(self.win32con.VK_CONTROL, ord("A"))
        time.sleep(0.05)
        self._press_key(self.win32con.VK_DELETE)
        time.sleep(self.config.action_delay_seconds)

    def _send_return(self, window: int) -> None:
        self.win32api.PostMessage(
            window, self.win32con.WM_KEYDOWN,
            self.win32con.VK_RETURN,
            0,
        )
        time.sleep(0.01)
        self.win32api.PostMessage(
            window, self.win32con.WM_KEYUP,
            self.win32con.VK_RETURN,
            0,
        )

    def _click(self, x: int, y: int) -> None:
        self.win32api.SetCursorPos((x, y))
        time.sleep(0.05)
        self.win32api.mouse_event(self.win32con.MOUSEEVENTF_LEFTDOWN, x, y, 0, 0)
        time.sleep(0.05)
        self.win32api.mouse_event(self.win32con.MOUSEEVENTF_LEFTUP, x, y, 0, 0)
        time.sleep(self.config.action_delay_seconds)

    def _hotkey(self, modifier: int, key: int) -> None:
        self.win32api.keybd_event(modifier, 0, 0, 0)
        try:
            self._press_key(key)
        finally:
            self.win32api.keybd_event(
                modifier,
                0,
                self.win32con.KEYEVENTF_KEYUP,
                0,
            )

    def _press_key(self, key: int) -> None:
        self.win32api.keybd_event(key, 0, 0, 0)
        time.sleep(0.05)
        self.win32api.keybd_event(key, 0, self.win32con.KEYEVENTF_KEYUP, 0)

    def _require_text(self, value: str, field_name: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field_name} must be a non-empty string.")
        return value.strip()


def main():
    # 단독 실행 데모. 실수로 아무 방에나 전송되지 않도록 기본은 "나와의 채팅"이며,
    # 실제로 보내려면 아래 CHAT_NAME 을 본인이 쓸 방 이름으로 바꾸고 실행하세요.
    CHAT_NAME = "나와의 채팅"
    send_kakao_message(CHAT_NAME, TEST_MESSAGE, send_now=True)
    print(f"sent KakaoTalk test message to: {CHAT_NAME}")


if __name__ == "__main__":
    main()
