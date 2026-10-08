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
        close_after_send=False,       # ⛔전송 후 방 창 닫지 않음(2026-07-21). 닫으면 다음 발송 때 검색으로 다시 못 열어 전량 실패한다
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


class KakaoPartialSendError(RuntimeError):
    """이미 일부 항목이 전송된 뒤 중단됨 — **재시도 금지**(재시도하면 중복·조각 발송).

    ★KakaoTalkControlError를 상속하지 않는다. 호출측(_kakao_do_send)이 KakaoTalkControlError를
    '아직 미전송 → 안전 재시도'로 분류하기 때문이다. 2026-07-21 실측: 사진 업로드 실패로 중단했더니
    재시도 3회가 매번 앞부분만 보내 김민혁 방에 조각 메시지 3건(5/11·1/11·1/11)이 갔다.
    """
    pass


@dataclass(slots=True)
class KakaoTalkConfig:
    executable_path: str | Path | None = None
    startup_timeout_seconds: float = 10.0
    search_wait_seconds: float = 1.0
    chat_open_wait_seconds: float = 3.0
    chat_close_wait_seconds: float = 3.0
    action_delay_seconds: float = 0.5
    image_upload_wait: float = 8.0        # 사진 전송 후 고정 대기(2026-07-21 3.0에서 상향).
                                          #  화면캡처 감지는 회귀로 제거 — 이 값만으로 순서를 지킨다.
    # 사진 업로드 판정(2026-07-21 실측 기준: 정상 전송은 T1.5초에 완료·이후 안정)
    #  정상이면 1~2초에 감지돼 바로 다음으로 넘어가고, 아래 제한시간을 다 쓰는 건 '무응답=이상'일 때뿐이다.
    image_send_timeout: float = 25.0       # 이 시간 내 완료 안 되면 무한로딩으로 보고 '실패' 확정
    image_stable_seconds: float = 0.9     # 화면이 변한 뒤 이만큼 그대로면 업로드 완료로 판정
    failure_mark_red_px: int = 12         # 실패 ✕ 판정 픽셀 수(2px 샘플링). 실측 전체스캔 실패본123 vs 성공본1
    close_after_send: bool = False   # ⛔닫지 말 것 — 검색으로 방 열기가 동작하지 않아, 닫는 순간 다음 발송이 실패한다(07-21 06:00 전량실패 원인)

    chat_tab_x_offset: int = 28
    chat_tab_y_offset: int = 105

    # ── 채팅 탭 이동 안정화(창 크기 바뀌어도 친구추가 등 오클릭 방지) ──
    use_chat_tab_hotkey: bool = False  # Ctrl+2는 이 카카오톡 버전에서 채팅탭 이동이 아니라 제거(원래 좌표 방식 유지)
    # ⛔좌표 클릭 영구 폐지(2026-07-21). 절대 True로 되돌리지 말 것.
    #  "창 크기가 달라져서 어긋난다"는 진단은 틀렸다 — 주인님 확인 결과 **기존과 동일한 창 크기에서도**
    #  친구추가 창이 열렸다. 즉 좌표(28,105)는 창 크기·정규화와 무관하게 신뢰할 수 없다.
    #  실제 피해: 발송 메시지가 친구추가 팝업에 입력되고, 잘못된 내용이 단톡방에 올라감(수차례 재발).
    #  → 검색창을 핸들로 못 찾으면 **엉뚱한 클릭 대신 명확히 실패**시킨다(오발송 > 미발송).
    chat_tab_use_coord: bool = False
    normalize_width: bool = False       # ★좌표 클릭을 정상경로에서 제거(핸들 방식)해 창 폭 강제변경이 불필요해짐.
                                        #  주인님이 맞춰둔 창 크기를 건드리지 않는다. (좌표 최후폴백을 쓸 때만 의미)
    standard_width: int = 360           # 표준 폭(offset 28,105가 맞는 카카오톡 기본 폭 근처)
    width_tolerance: int = 24           # 표준±24 안이면 정규화 생략(주인님이 살짝 바꾼 건 존중)
    restore_size_after: bool = True     # 발송 후 원래 크기로 복원(주인님이 바꿔둔 크기 유지)
    close_popups_before_send: bool = True   # 발송 전 방해 팝업(친구추가 등) 자동 닫기
    blocking_popup_titles: tuple = ("친구 추가", "친구추가", "새로운 채팅", "대화상대 초대")


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

    def open_chat_and_input_message(self, chat_name: str, message_: str, *, defer_restore: bool = False) -> int:
        chat_name = self._require_text(chat_name, "chat_name")
        message_ = self._require_text(message_, "message_")

        main_window = self._ensure_kakaotalk_running()
        if self.config.close_popups_before_send:
            self._close_blocking_popups()   # 발송 방해 팝업(친구추가 등) 먼저 닫기
        _orig = self._normalize_window(main_window)   # B: 발송 순간 표준 폭으로 고정(좌표 클릭 신뢰성)
        self._pending_restore = (main_window, _orig)   # 전송 후 복원용(send_message가 전송 끝나고 복원)
        try:
            time.sleep(self.config.action_delay_seconds)
            self._focus_window(main_window)
            time.sleep(self.config.action_delay_seconds)
            self._go_to_chat_tab(main_window)
            time.sleep(self.config.action_delay_seconds)
            # ★단계 로그 — 뉴스(단일 메시지) 경로에도 시퀀스와 동일한 수준의 진행 기록을 남긴다.
            #  없으면 실패 시 '✗ 방이름'만 보여 어디서 끊겼는지 알 수 없다(2026-07-21 뉴스 실패 원인 추적 불가).
            print(f"[msg] '{chat_name}' 방 열기…", flush=True)
            self._open_room(chat_name)
            time.sleep(self.config.action_delay_seconds)

            chat_window = self._find_chat_window(chat_name)
            print(f"[msg] '{chat_name}' 방 열림(handle={chat_window})", flush=True)
            time.sleep(self.config.action_delay_seconds)
            message_input = self._find_message_input(chat_window)
            time.sleep(self.config.action_delay_seconds)
            self._clear_message_input(message_input)
            # ★★붙여넣기(Ctrl+V)는 창 핸들이 아니라 '현재 포커스'로 들어간다. 친구추가 등 팝업이
            #  포커스를 갖고 있으면 메시지가 그 팝업에 입력되는 사고가 난다(실제 발생).
            #  → 붙여넣기 직전에 채팅방 입력창을 전경·포커스로 강제하고, 그래도 전경이 채팅방이
            #    아니면 붙여넣지 않고 중단한다(엉뚱한 창 오입력 원천 차단).
            self._focus_window(chat_window)
            self._click_window(message_input)
            time.sleep(self.config.action_delay_seconds)
            if not self._foreground_is(chat_window):
                self._close_blocking_popups()          # 팝업이 뺏었으면 닫고 재시도
                self._focus_window(chat_window)
                self._click_window(message_input)
                time.sleep(self.config.action_delay_seconds)
                if not self._foreground_is(chat_window):
                    raise KakaoTalkControlError(
                        f"입력 대상이 채팅방이 아님(전경 창 불일치) — 오입력 방지로 중단: {chat_name}")
            self._paste_text(message_)
            time.sleep(self.config.action_delay_seconds)
            print(f"[msg] '{chat_name}' 본문 입력 완료({len(message_)}자)", flush=True)

            return chat_window
        except Exception:
            # ★실패 경로에서도 반드시 팝업을 치운다. 안 그러면 이번처럼 친구추가 창이 몇 시간 방치되고,
            #  그 창이 다음 발송의 검색창 탐색을 방해해 '실패 → 팝업 → 또 실패'의 악순환이 된다.
            try:
                self._close_blocking_popups()
            except Exception:
                pass
            raise
        finally:
            # 입력만 하는 경로(send_now=False)는 즉시 복원. 전송까지 가는 send_message는 defer_restore=True라
            # 여기서 복원하지 않고 '전송 완료 후' 복원한다(입력↔전송 사이 창 이동으로 전송이 깨지던 버그 수정).
            if not defer_restore:
                self._restore_window(main_window, _orig)
                self._pending_restore = None

    def send_message(self, chat_name: str, message_: str) -> None:
        # 정규화한 창을 '전송까지' 유지하려고 defer_restore=True로 입력 → 전송 → 그다음 복원.
        #  (복원을 전송 전에 하면 창이 움직여 입력창 포커스를 잃고 Enter가 먹지 않던 버그를 막는다.)
        chat_window = self.open_chat_and_input_message(chat_name, message_, defer_restore=True)
        _pr = getattr(self, "_pending_restore", None)
        try:
            message_input = self._find_message_input(chat_window)
            time.sleep(self.config.action_delay_seconds)
            self._ensure_input_filled(message_input, message_)   # 빈 입력창이면 재붙여넣기(유실→빈 Enter 방지)
            self._click_window(message_input)
            self._press_key(self.win32con.VK_RETURN)
            print(f"[msg] '{chat_name}' 전송 완료", flush=True)

            if self.config.close_after_send:
                time.sleep(self.config.action_delay_seconds)
                self.win32gui.PostMessage(chat_window, self.win32con.WM_CLOSE, 0, 0)
                self._wait_for_window_closed(chat_window)
        finally:
            if _pr:
                self._restore_window(*_pr)   # 전송 완료(성공/실패 무관) 후 원래 크기 복원
                self._pending_restore = None

    def send_sequence(self, chat_name: str, items: list) -> None:
        """한 방에 여러 항목(text/image)을 순차 전송 — 사진 말풍선, 텍스트 말풍선 번갈아."""
        chat_name = self._require_text(chat_name, "chat_name")
        main_window = self._ensure_kakaotalk_running()
        if self.config.close_popups_before_send:
            self._close_blocking_popups()   # 발송 방해 팝업(친구추가 등) 먼저 닫기
        _seq_orig = self._normalize_window(main_window)   # B: 발송 순간 표준 폭으로 고정(좌표 신뢰성)
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

        _total = len(items or [])
        _sent_n = 0
        print(f"[seq] '{chat_name}' 시작 — 항목 {_total}개", flush=True)
        for _idx, item in enumerate(items or [], 1):
            # ★붙여넣기(Ctrl+V)는 '현재 포커스'로 들어간다 → 항목마다 채팅방 입력창 포커스를 강제하고
            #  전경이 채팅방인지 검증한다(팝업이 포커스를 뺏으면 그 팝업에 입력되는 사고 방지).
            #  검증 실패 시 붙여넣지 않고 중단 — 엉뚱한 창 오입력 원천 차단.
            self._focus_window(chat_window)
            self._click_window(message_input)
            time.sleep(self.config.action_delay_seconds)
            if not self._foreground_is(chat_window):
                self._close_blocking_popups()
                self._focus_window(chat_window)
                self._click_window(message_input)
                time.sleep(self.config.action_delay_seconds)
                if not self._foreground_is(chat_window):
                    raise KakaoTalkControlError(
                        f"입력 대상이 채팅방이 아님(전경 창 불일치) — 오입력 방지로 중단: {chat_name}")
            _is_image = item.get("type") == "image" and item.get("path")
            if _is_image:
                self._clear_message_input(message_input)
                self._paste_image(item["path"])
            else:
                text = (item.get("text") or "").strip()
                if not text:
                    continue
                self._clear_message_input(message_input)
                self._paste_text(text)
                self._ensure_input_filled(message_input, text)   # 텍스트 말풍선 유실 방지(Enter 前)
            time.sleep(self.config.action_delay_seconds)
            self._click_window(message_input)
            self._press_key(self.win32con.VK_RETURN)
            # ⛔화면캡처 기반 '업로드 완료 감지'(_chat_signature/_wait_image_sent)는 **제거**했다.
            #  2026-07-21 회귀: 순서 꼬임을 막으려 도입했으나 실제 단톡방에서 감지가 되지 않아
            #    · 8초판  → 감지 실패로 2/11에서 발송 중단(매각예정 2회)
            #    · 25초판 → GDI 호출 3배로 늘자 서버가 0xC0000005로 사망(매각완료 2회)
            #  즉 감지가 되든 안 되든 발송을 망가뜨렸다(실제 단톡방 4전 4패). 사진 없는 뉴스만 정상이었다.
            #  도입 전(고정 대기)에는 서버 사망 0회였으므로 그 상태로 되돌린다.
            #  순서 꼬임 완화를 위해 기존 3초 → image_upload_wait(8초)로 늘려 잡는다.
            #  ※다시 감지를 넣으려면 GDI 캡처가 아닌 방식으로, 테스트 방이 아니라 **실제 단톡방**에서 검증할 것.
            time.sleep(self.config.image_upload_wait if _is_image
                       else self.config.action_delay_seconds)
            _sent_n += 1
            # 시퀀스 발송은 '방 단위 성공/실패'만 남아 어디서 끊겼는지 알 수 없었다(2026-07-21).
            # 항목 단위로 남겨야 '사진은 갔는데 텍스트가 빠짐' 같은 부분전송을 사후에 잡을 수 있다.
            print(f"[seq]   {_idx}/{_total} {item.get('type') or 'text'} 전송", flush=True)
        if _sent_n < _total:
            print(f"[seq] ⚠ '{chat_name}' 부분전송: {_sent_n}/{_total}", flush=True)
        else:
            print(f"[seq] '{chat_name}' 완료 {_sent_n}/{_total}", flush=True)

        if self.config.close_after_send:
            time.sleep(self.config.action_delay_seconds)
            self.win32gui.PostMessage(chat_window, self.win32con.WM_CLOSE, 0, 0)
            self._wait_for_window_closed(chat_window)
        self._restore_window(main_window, _seq_orig)   # 발송 끝 → 원래 크기 복원(주인님 크기 유지)

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
        # 무효 핸들(0/닫힌 창)이면 BringWindowToTop이 '잘못된 창 핸들'로 터져 발송이 통째로 중단된다
        # (2026-07-21 06:00 실측: 3개 방 중 2개가 이 예외로 실패). 원인을 알 수 있는 에러로 바꾼다.
        if not window or not self.win32gui.IsWindow(window):
            raise KakaoTalkControlError(f"창 핸들이 유효하지 않습니다(handle={window}) — 채팅방을 못 찾았을 가능성")
        # ★AttachThreadInput으로 입력 큐를 붙인 뒤 SetForegroundWindow.
        #  Windows는 '다른 프로세스가 전경일 때' SetForegroundWindow/BringWindowToTop을 조용히 무시한다.
        #  그래서 전경 확보에 실패한 채 클릭·키입력이 엉뚱한 창으로 새고 있었다(2026-07-21 실측:
        #  전경 확보 실패 상태에서 Ctrl+V가 검색창에 안 들어가고 좌표 클릭이 친구추가 창을 눌렀다).
        # ★ForegroundLockTimeout을 0으로 낮춰야 SetForegroundWindow가 실제로 먹는다.
        #  이 PC는 이 값이 2147483647(무한대)이라, 낮추지 않으면 전경 확보가 조용히 실패하고
        #  이후 클릭·키입력이 카톡이 아닌 '현재 전경 창'으로 들어간다(= 좌표가 어긋난 것처럼 보임).
        #  2026-07-21 사고: 이 설정을 테스트 스크립트에만 넣고 검증해 "발송 정상"이라 보고했으나,
        #  실제 서버(uvicorn) 경로엔 없어서 admin '지금 발송'이 실패했다. → 여기(공통 경로)로 이식.
        _fg_lock_old = None
        try:
            from ctypes import windll, byref, c_uint
            _SPI_GET, _SPI_SET = 0x2000, 0x2001
            _v = c_uint(0)
            if windll.user32.SystemParametersInfoW(_SPI_GET, 0, byref(_v), 0):
                _fg_lock_old = _v.value
            windll.user32.SystemParametersInfoW(_SPI_SET, 0, 0, 0)
        except Exception:
            _fg_lock_old = None
        try:
            import win32process
            fg = self.win32gui.GetForegroundWindow()
            cur = self.win32api.GetCurrentThreadId()
            t_fg, _ = win32process.GetWindowThreadProcessId(fg) if fg else (0, 0)
            t_tg, _ = win32process.GetWindowThreadProcessId(window)
            attached = [t for t in {t_fg, t_tg} if t]
            for t in attached:
                try:
                    win32process.AttachThreadInput(cur, t, True)
                except Exception:
                    pass
            try:
                self.win32gui.SetWindowPos(
                    window, self.win32con.HWND_TOP, 0, 0, 0, 0,
                    self.win32con.SWP_NOMOVE | self.win32con.SWP_NOSIZE | self.win32con.SWP_SHOWWINDOW)
            except Exception:
                pass
            self.win32gui.ShowWindow(window, self.win32con.SW_RESTORE)
            try:
                self.win32gui.SetForegroundWindow(window)
            except Exception:
                pass
            for t in attached:
                try:
                    win32process.AttachThreadInput(cur, t, False)
                except Exception:
                    pass
        except Exception:
            pass
        finally:
            if _fg_lock_old is not None:          # 시스템 설정이므로 반드시 원복
                try:
                    from ctypes import windll
                    windll.user32.SystemParametersInfoW(0x2001, 0, _fg_lock_old, 0)
                except Exception:
                    pass
        self.win32gui.ShowWindow(window, self.win32con.SW_RESTORE)
        self.win32gui.BringWindowToTop(window)
        self._press_key(self.win32con.VK_MENU)
        try:
            self.win32gui.SetForegroundWindow(window)
        except Exception:
            pass
        time.sleep(self.config.action_delay_seconds)

    def _go_to_chat_tab(self, main_window: int) -> None:
        """채팅 탭 이동 — ★좌표 클릭을 정상경로에서 제거(근본).
        방 검색창은 창 계층(EVA_ChildWindow→EVA_Window) '핸들'로 직접 찾히므로(실측 확인)
        탭 클릭 자체가 불필요하다. 좌표(28,105) 클릭은 창 크기·위치가 조금만 달라져도
        친구추가 등 엉뚱한 버튼을 눌러 뉴스/매각예정 발송이 그 팝업에 입력되는 사고를 냈다.
        → ①검색창이 잡히면 아무것도 안 함 ②단축키(설정 시) ③그래도 안 되면 최후 폴백으로만
          좌표 클릭하되, 클릭 직후 방해 팝업을 즉시 닫는다."""
        # ① 좌표 없이 검색창이 잡히면 탭 이동 불필요 (정상 경로 — 좌표 클릭 안 함)
        try:
            if self._find_room_search_input():
                return
        except Exception:
            pass
        # ② 단축키(이 카카오톡 버전에서 동작하는 경우만)
        if self.config.use_chat_tab_hotkey:
            self._hotkey(self.win32con.VK_CONTROL, ord("2"))
            time.sleep(self.config.action_delay_seconds)
            try:
                if self._find_room_search_input():
                    return
            except Exception:
                pass
        # ③ 최후 폴백: 좌표 클릭(오클릭 위험) → 클릭 후 방해 팝업 즉시 닫기
        if self.config.chat_tab_use_coord:
            left, top, _, _ = self.win32gui.GetWindowRect(main_window)
            self._click(
                left + self.config.chat_tab_x_offset,
                top + self.config.chat_tab_y_offset,
            )
            time.sleep(self.config.action_delay_seconds)
            self._close_blocking_popups()   # 좌표가 어긋나 친구추가 등이 열렸으면 즉시 닫음

    def _normalize_window(self, main_window: int):
        """발송 순간 창을 표준 폭으로 고정(좌표 클릭 신뢰성 확보). 원래 크기 반환(복원용).
        이미 표준 폭 근처면 건드리지 않는다(주인님이 살짝 조절한 크기는 존중)."""
        if not self.config.normalize_width:
            return None
        try:
            l, t, r, b = self.win32gui.GetWindowRect(main_window)
        except Exception:
            return None
        w, h = r - l, b - t
        if abs(w - self.config.standard_width) <= self.config.width_tolerance:
            return None
        try:
            self.win32gui.MoveWindow(main_window, l, t, self.config.standard_width, h, True)
            time.sleep(self.config.action_delay_seconds)
        except Exception:
            return None
        return (l, t, w, h)

    def _restore_window(self, main_window: int, orig) -> None:
        if not orig or not self.config.restore_size_after:
            return
        try:
            self.win32gui.MoveWindow(main_window, orig[0], orig[1], orig[2], orig[3], True)
        except Exception:
            pass

    def _close_blocking_popups(self) -> None:
        """발송을 방해하는 카카오톡 팝업(친구 추가 등)이 떠 있으면 닫는다.
        사용자가 창을 안 닫아둔 경우, 이전 발송이 좌표 어긋나 띄운 친구추가 창이 남은 경우 대비.
        메인 창('카카오톡')과 실제 채팅방 창은 건드리지 않고, 알려진 방해 팝업만 닫는다."""
        for title in self.config.blocking_popup_titles:
            for _ in range(3):   # 같은 제목 팝업이 여러 개 떠 있을 수 있어 반복
                h = self.win32gui.FindWindow(None, title)
                if not h:
                    break
                try:
                    self.win32gui.PostMessage(h, self.win32con.WM_CLOSE, 0, 0)
                    time.sleep(self.config.action_delay_seconds)
                except Exception:
                    break
        # ★제목 매칭만으로는 못 닫는다 — 친구추가 창은 제목표시줄 없는 모달이라 FindWindow가 못 잡는다.
        #  (2026-07-21 실측: 06:00에 열린 친구추가 창이 09:00까지 3시간 방치됨)
        #  → 카카오톡 '프로세스'의 최상위 보이는 창 중 '제목이 빈' 것을 모달로 보고 닫는다.
        #    메인창(제목='카카오톡')과 채팅방 창(제목=방 이름)은 제목이 있으므로 건드리지 않는다.
        try:
            main = self._find_main_window()
            if not main:
                return
            import win32process                                  # pywin32
            _, kakao_pid = win32process.GetWindowThreadProcessId(main)
            victims = []

            def _enum(h, _):
                if h == main or not self.win32gui.IsWindowVisible(h):
                    return True
                try:
                    _, pid = win32process.GetWindowThreadProcessId(h)
                except Exception:
                    return True
                if pid != kakao_pid:
                    return True
                if (self.win32gui.GetWindowText(h) or "").strip():   # 제목 있는 창(채팅방 등)은 보존
                    return True
                victims.append(h)
                return True

            self.win32gui.EnumWindows(_enum, None)
            for h in victims:
                try:
                    self.win32gui.PostMessage(h, self.win32con.WM_CLOSE, 0, 0)
                    time.sleep(self.config.action_delay_seconds)
                except Exception:
                    pass
        except Exception:
            pass

    def _open_room(self, chat_name: str) -> None:
        """방 검색 → 열기. ★반드시 '실제 키 입력'으로 넣어야 한다(2026-07-21 근본원인).

        기존엔 _set_text(=SendMessage WM_SETTEXT)로 검색어를 넣었는데, 이는 컨트롤의 텍스트만
        바꿀 뿐 카카오톡의 '검색 실행' 입력 이벤트를 발생시키지 않는다 → 검색 결과가 비어
        Enter를 눌러도 방이 안 열린다(실측: WM_SETTEXT는 창 0개, 실제 키 입력은 창 열림).
        close_after_send=True라 발송 후 방을 닫으므로 다음 발송은 항상 이 경로를 타고,
        그래서 07-21 06:00 정기발송이 3개 방 전부 'chat window was not opened'로 실패했다.
        (그동안 발송이 됐던 건 주인님이 그 방을 열어둬 FindWindow가 즉시 찾았을 때뿐이다.)
        """
        main_window = self._find_main_window()
        # ★★근본 경로(2026-07-21 실측 확립) — 전역 입력(keybd_event/mouse_event)은 이 환경에서
        #  카카오톡에 전달되지 않는다(Ctrl+V로 검색창에 글자가 안 들어가고, 좌표 클릭도 무시됨).
        #  SendMessage 기반만 동작한다:
        #    ① 검색어  : WM_SETTEXT로 비운 뒤 WM_CHAR를 '한 글자씩' → 카톡이 실제 검색을 실행한다
        #                (WM_SETTEXT만 쓰면 글자는 들어가도 검색이 안 돌아 방이 안 열렸다 = 06:00 실패 원인)
        #    ② 방 열기 : 검색결과 목록(SearchListCtrl)에 WM_LBUTTONDOWN/UP + WM_LBUTTONDBLCLK
        #                (Enter·DOWN+Enter·좌표클릭은 전부 실패. 더블클릭 메시지만 방을 연다)
        # ⛔폴백 경로(구 _open_room: Ctrl+F → 클립보드 붙여넣기 → Enter)는 **영구 제거**했다.
        #  2026-07-21 사고: 메시지 경로가 실패하면 이 폴백으로 넘어갔는데, 여기서 전역 입력이
        #  카톡에 전달되지 않아 엉뚱한 곳으로 새고 **친구추가 창이 열렸다**. 그 창이 전경을 잡으면
        #  OnlineMainView를 못 찾아 채팅 탭 복귀도 실패하고, **다음 방까지 연쇄로 무너졌다**.
        #  → 실패하면 폴백하지 말고 즉시 중단한다(미발송 > 오발송).
        if self._open_room_by_message(main_window, chat_name):
            return
        raise KakaoTalkControlError(
            f"방을 열지 못했습니다(폴백 없이 중단 — 오클릭 방지): {chat_name}")

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

    def _foreground_is(self, window: int) -> bool:
        """전경(키보드 포커스) 창이 대상 창(또는 그 자식/루트)인지 — Ctrl+V 오입력 방지용 검증.
        친구추가 등 팝업이 포커스를 갖고 있으면 False → 호출부가 붙여넣기를 중단한다."""
        try:
            fg = self.win32gui.GetForegroundWindow()
        except Exception:
            return False
        if not fg or not window:
            return False
        if fg == window:
            return True
        try:
            if self.win32gui.GetParent(fg) == window:
                return True
            if self.win32gui.GetAncestor(fg, 2) == window:      # GA_ROOT
                return True
        except Exception:
            pass
        return False

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

    def _chat_tab_visible(self, main_window: int) -> bool:
        """채팅 탭(ChatRoomListView)이 보이는 상태인지. 탭 3개는 같은 자리에 겹쳐 visible만 토글된다."""
        found = {"v": False}

        def _c(h, _):
            if (self.win32gui.GetWindowText(h) or "").startswith("ChatRoomListView"):
                found["v"] = bool(self.win32gui.IsWindowVisible(h))
            return True

        try:
            self.win32gui.EnumChildWindows(main_window, _c, None)
        except Exception:
            pass
        return found["v"]

    def _ensure_chat_tab(self, main_window: int) -> bool:
        """채팅 탭이 아니면 사이드바 '채팅' 아이콘을 눌러 복귀. 성공 여부 반환.

        ★이게 없으면 자동발송이 무의미하다 — 카톡이 친구/더보기 탭에 있으면 채팅방 검색창이
        아예 존재하지 않아 방을 못 열고 발송이 전량 실패한다(2026-07-21 06:00 실패 원인).
        사이드바는 별도 컨트롤이 아니라 OnlineMainView 안에 커스텀 렌더링돼 핸들로 못 누르고,
        전역 mouse_event는 카톡에 전달되지 않는다 → OnlineMainView에 좌표 클릭 '메시지'를 보낸다.
        좌표 실측(창 392x642 기준): 채팅=(48,95) · 더보기=(48,178).
        """
        if self._chat_tab_visible(main_window):
            return True
        omv = {"h": None}

        def _f(h, _):
            if (self.win32gui.GetWindowText(h) or "").startswith("OnlineMainView"):
                omv["h"] = h
            return True

        try:
            self.win32gui.EnumChildWindows(main_window, _f, None)
        except Exception:
            pass
        if not omv["h"]:
            print("[tab] OnlineMainView를 찾지 못함 — 채팅 탭 복귀 불가", flush=True)
            return False
        l0, t0, _, _ = self.win32gui.GetWindowRect(main_window)
        ol, ot, _, _ = self.win32gui.GetWindowRect(omv["h"])
        for wy in (95, 103, 88, 110, 120):        # 실측 95. 창/버전 차이 대비 인접 지점도 시도
            cx, cy = (l0 + 48) - ol, (t0 + wy) - ot
            lp = (cy << 16) | (cx & 0xFFFF)
            try:
                self.win32api.SendMessage(omv["h"], self.win32con.WM_LBUTTONDOWN, 1, lp)
                time.sleep(0.08)
                self.win32api.SendMessage(omv["h"], self.win32con.WM_LBUTTONUP, 0, lp)
            except Exception:
                continue
            time.sleep(1.0)
            if self._chat_tab_visible(main_window):
                print(f"[tab] 채팅 탭 복귀(y={wy})", flush=True)
                return True
        print("[tab] 채팅 탭 복귀 실패 — 발송을 중단합니다(엉뚱한 화면에서 진행 방지)", flush=True)
        return False

    def _open_room_by_message(self, main_window: int, chat_name: str) -> bool:
        """SendMessage만으로 방 검색·열기. 성공하면 True.
        전역 입력이 카카오톡에 전달되지 않는 환경(자동발송/백그라운드)에서 유일하게 동작하는 경로."""
        self._close_blocking_popups()
        if main_window:
            self._focus_window(main_window)
            time.sleep(self.config.action_delay_seconds)
        # ★채팅 탭이 아니면 검색창 자체가 없다 → 먼저 복귀시키고, 실패하면 명확히 중단
        if not self._ensure_chat_tab(main_window):
            raise KakaoTalkControlError("카카오톡이 채팅 탭이 아니며 복귀에 실패했습니다(발송 중단)")
        search_input = self._find_room_search_input()
        if not self.win32gui.IsWindowVisible(search_input):
            self._hotkey(self.win32con.VK_CONTROL, ord("F"))   # 검색창은 기본 숨김 → Ctrl+F로 연다
            time.sleep(self.config.action_delay_seconds)
            search_input = self._find_room_search_input()
        # ① 검색어: 비우고 WM_CHAR 한 글자씩(=카톡이 검색 실행)
        self.win32api.SendMessage(search_input, self.win32con.WM_SETTEXT, 0, "")
        time.sleep(0.3)
        for ch in chat_name:
            self.win32api.SendMessage(search_input, self.win32con.WM_CHAR, ord(ch), 0)
            time.sleep(0.07)
        time.sleep(self.config.search_wait_seconds + 1.5)      # 검색 결과 렌더 대기
        # ★검증은 '완전일치'가 아니라 '접두사 일치'여야 한다.
        #  카카오톡 검색창은 20자까지만 받는다(실측). 방 이름이 그보다 길면 뒤가 잘리는데,
        #  이건 정상이며 앞부분만으로도 검색은 된다. 완전일치를 요구하면 긴 이름의 방이
        #  100% 실패하고 폴백으로 넘어가 친구추가 창이 열렸다(2026-07-21 사고).
        #  실측: '5월 7일(목) 1건 단타로 2천만원 경매 노하우!'(26자) → 검색창엔 20자만 입력됨.
        got = self._read_text(search_input).strip()
        want = chat_name.strip()
        if not got or not want.startswith(got):
            print(f"[room] 검색어 불일치 — 넣은값={want!r} 실제={got!r}", flush=True)
            return False
        if got != want:
            print(f"[room] 검색어가 {len(got)}자로 잘림(카톡 검색창 제한) — 접두사로 검색 진행", flush=True)
        # ② 검색결과 목록 첫 항목을 더블클릭(메시지)
        lists: list = []

        def _c(h, _):
            t = self.win32gui.GetWindowText(h) or ""
            if "SearchListCtrl" in t and self.win32gui.IsWindowVisible(h):
                lists.append(h)
            return True

        self.win32gui.EnumChildWindows(main_window, _c, None)
        if not lists:
            print("[room] 검색결과 목록(SearchListCtrl)이 보이지 않음", flush=True)
            return False
        h = lists[0]
        l, t, r, b = self.win32gui.GetWindowRect(h)
        x = (r - l) // 2
        for oy in (30, 55, 80):                                # 첫 항목 높이가 방마다 조금 달라 3지점 시도
            lp = (oy << 16) | (x & 0xFFFF)
            self.win32api.SendMessage(h, self.win32con.WM_LBUTTONDOWN, 1, lp)
            time.sleep(0.08)
            self.win32api.SendMessage(h, self.win32con.WM_LBUTTONUP, 0, lp)
            time.sleep(0.4)
            self.win32api.SendMessage(h, self.win32con.WM_LBUTTONDBLCLK, 1, lp)
            time.sleep(0.08)
            self.win32api.SendMessage(h, self.win32con.WM_LBUTTONUP, 0, lp)
            time.sleep(self.config.chat_open_wait_seconds)
            if self.win32gui.FindWindow(None, chat_name):
                return True
        return False

    def _chat_signature(self, chat_window: int) -> str:
        """채팅창 '하단 300px'(최근 말풍선 영역)의 화면 지문. 사진 업로드 완료 판정용."""
        import hashlib
        import win32ui
        from ctypes import windll
        from PIL import Image
        l, t, r, b = self.win32gui.GetWindowRect(chat_window)
        w, h = r - l, b - t
        if w <= 0 or h <= 0:
            return ""
        dc = self.win32gui.GetWindowDC(chat_window)
        mfc = win32ui.CreateDCFromHandle(dc)
        save = mfc.CreateCompatibleDC()
        bmp = win32ui.CreateBitmap()
        bmp.CreateCompatibleBitmap(mfc, w, h)
        save.SelectObject(bmp)
        try:
            windll.user32.PrintWindow(chat_window, save.GetSafeHdc(), 2)   # 가려져 있어도 그려짐
            info = bmp.GetInfo()
            img = Image.frombuffer("RGB", (info["bmWidth"], info["bmHeight"]),
                                   bmp.GetBitmapBits(True), "raw", "BGRX", 0, 1)
            img = img.crop((0, max(0, h - 300), w, h)).resize((80, 40))
            return hashlib.md5(img.tobytes()).hexdigest()[:12]
        finally:
            self.win32gui.DeleteObject(bmp.GetHandle())
            save.DeleteDC()
            mfc.DeleteDC()
            self.win32gui.ReleaseDC(chat_window, dc)

    def _wait_image_sent(self, chat_window: int, before_sig: str) -> bool:
        """사진 전송 완료 대기 — 채팅창 하단이 '변한 뒤 안정'되면 완료로 본다.

        기존엔 image_upload_wait(3초) 고정 대기였다. 인터넷이 느리면 업로드가 안 끝난 채
        다음 항목을 보내 사진이 누락되거나 순서가 뒤엉켰다(주인님 실측 제보, 2026-07-21).
        실측: 전송 직후 하단 지문이 바뀌고(말풍선 등장) 그 뒤로는 계속 동일 → 이 패턴으로 판정.
        """
        # ⛔실패표시(↻·✕) 픽셀 감지는 쓰지 않는다(2026-07-21).
        #   실측: 실패 캡처의 ✕는 '그 전송'의 실패 표시가 아니라 더 이전 실패의 잔재였고
        #   (전송전 36 → 전송후 36, 증분 0), 절대값으로 보면 전송 전 화면에서도 289·36이 검출돼
        #   성공을 실패로 오판했다. 오탐이 미탐보다 위험하므로 판정에서 제외한다.
        #   실패는 아래 '8초 내 미안정 → 무한로딩' 규칙으로 잡는다. (_count_failure_red는 진단용으로만 유지)
        import time as _t
        deadline = _t.monotonic() + self.config.image_send_timeout
        prev = before_sig
        stable_since = None
        while _t.monotonic() < deadline:
            _t.sleep(0.3)
            try:
                cur = self._chat_signature(chat_window)
            except Exception:
                continue
            if not cur:
                continue
            # ② 화면이 '변한 뒤 일정 시간 그대로'면 로딩 종료로 본다.
            #    첫 변화 시점(로딩 말풍선 등장)을 완료로 보면 안 된다 — 실측상 T0.5는 로딩 시작이고
            #    실제 완료는 T1.5였다. 그 차이 때문에 다음 텍스트가 사진보다 먼저 도착해 순서가 엉켰다.
            if cur != prev:
                prev, stable_since = cur, None
                continue
            if cur == before_sig:                  # 아직 전송 전 화면 그대로 → 계속 대기
                continue
            if stable_since is None:
                stable_since = _t.monotonic()
            elif _t.monotonic() - stable_since >= self.config.image_stable_seconds:
                return True                        # 변화 후 안정 유지 = 업로드 완료
        # ③ 제한시간 내내 안정되지 않음 = 무한로딩 → 실패로 확정(대용량이 아닌데 늦는 건 이상)
        print(f"[img] {self.config.image_send_timeout:.0f}초 내 업로드 완료 안 됨(무한로딩) — 실패", flush=True)
        return False

    def _count_failure_red(self, chat_window: int) -> int:
        """채팅창 하단의 '전송 실패 표시(↻·✕)' 계열 픽셀 수. **진단·로그 전용**.

        ⛔업로드 성공/실패 판정에는 쓰지 말 것(2026-07-21 실측):
          - 절대값 판정 → 전송 '전' 화면에서도 289·36픽셀이 나와 성공을 실패로 오판
          - 증분 판정 → 실패 케이스의 증분이 0이라(36→36) 실패를 못 잡음
          실패 캡처의 ✕는 '그 전송'이 아니라 더 이전 실패의 잔재였다.
        """
        import win32ui
        from ctypes import windll
        from PIL import Image
        try:
            l, t, r, b = self.win32gui.GetWindowRect(chat_window)
            w, h = r - l, b - t
            if w <= 0 or h <= 0:
                return -1
            dc = self.win32gui.GetWindowDC(chat_window)
            mfc = win32ui.CreateDCFromHandle(dc)
            save = mfc.CreateCompatibleDC()
            bmp = win32ui.CreateBitmap()
            bmp.CreateCompatibleBitmap(mfc, w, h)
            save.SelectObject(bmp)
            try:
                windll.user32.PrintWindow(chat_window, save.GetSafeHdc(), 2)
                info = bmp.GetInfo()
                img = Image.frombuffer("RGB", (info["bmWidth"], info["bmHeight"]),
                                       bmp.GetBitmapBits(True), "raw", "BGRX", 0, 1)
                img = img.crop((0, max(0, h - 360), w, max(1, h - 70)))
            finally:
                self.win32gui.DeleteObject(bmp.GetHandle())
                save.DeleteDC()
                mfc.DeleteDC()
                self.win32gui.ReleaseDC(chat_window, dc)
            px = img.load()
            iw, ih = img.size
            n = 0
            # 색 기준은 실측값(2026-07-21): 실패 ✕는 순수 빨강이 아니라 '주황빛 빨강'이다
            #   실패본 상위색 (240,128,96)·(240,80,48)·(240,128,112) → G가 128까지 올라간다.
            #   처음 쓴 G<90 기준으로는 하나도 안 걸렸다(실패본에서도 검출 0).
            for y in range(0, ih, 2):              # 2px 간격 샘플링(속도)
                for x in range(0, iw, 2):
                    R, G, B = px[x, y]
                    if R > 120 and R - G > 40 and R - B > 40:
                        n += 1
            return n
        except Exception:
            return -1                               # 검사 불가 → 호출측이 판정에 쓰지 않음

    def _read_text(self, window: int) -> str:
        """다른 프로세스의 Edit 내용 읽기 — GetWindowText는 빈 문자열을 반환하므로 WM_GETTEXT를 쓴다.
        (2026-07-21 실측: 같은 검색창에 대해 GetWindowText='' / WM_GETTEXT='노진혁 투폰')"""
        try:
            n = self.win32gui.SendMessage(window, self.win32con.WM_GETTEXTLENGTH, 0, 0)
            if n <= 0:
                return ""
            buf = self.win32gui.PyMakeBuffer((n + 1) * 2)
            self.win32gui.SendMessage(window, self.win32con.WM_GETTEXT, n + 1, buf)
            return buf[: n * 2].tobytes().decode("utf-16-le", "ignore")
        except Exception:
            return ""

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

    def _ensure_input_filled(self, message_input: int, expected_text: str, tries: int = 3) -> bool:
        """Enter(전송) 前에 입력창에 내용이 실제로 들어갔는지 확인 — 비어 있으면(클립보드 경합 등으로
        붙여넣기가 유실된 경우) 다시 붙여넣는다. 오늘 리치빌더처럼 '창은 열렸는데 내용 유실 → 빈 Enter'로
        메시지가 조용히 안 가던 것을 막는다.
        ⚠️ 여기는 아직 Enter를 누르기 前 단계라, 몇 번을 재붙여넣어도 중복 발송이 원천적으로 불가능하다."""
        expected = (expected_text or "").strip()
        if not expected:
            return True
        for _ in range(max(1, tries)):
            try:
                length = self.win32gui.SendMessage(
                    message_input, self.win32con.WM_GETTEXTLENGTH, 0, 0)
            except Exception:
                return True   # 길이 확인 자체가 실패하면, 오검출로 재붙여넣다 꼬이지 않게 그냥 진행
            if length and int(length) > 0:
                return True   # 내용 있음 → 정상, Enter 진행
            # 입력창이 비어있음 → 아직 전송 안 했으니 다시 붙여넣기(중복 위험 없음)
            self._clear_message_input(message_input)
            self._paste_text(expected)
            time.sleep(self.config.action_delay_seconds)
        return False

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
