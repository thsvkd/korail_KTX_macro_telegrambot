"""
텔레그램 봇 메시지 템플릿 관리

모든 봇 메시지를 중앙에서 관리하여 유지보수성을 향상시킵니다.
"""

from typing import ClassVar


class Messages:
    """봇 메시지 템플릿 클래스"""

    # ========== 명령어 메뉴 ==========
    # Telegram 의 메뉴 버튼에 표시되는 목록. 명령어를 외워서 타이핑하는 대신
    # 골라서 쓰게 만드는 용도다.
    #
    # 관리자 명령어는 일부러 빼두었다. 메뉴는 모든 사용자에게 똑같이 보이므로,
    # 여기에 넣는 순간 /flushredis 가 있다는 사실을 전원에게 광고하게 된다.
    BOT_COMMANDS: ClassVar[list[dict[str, str]]] = [
        {"command": "start", "description": "🎫 예약 시작"},
        {"command": "status", "description": "📊 예약 상태 확인"},
        {"command": "cancel", "description": "🚫 진행 중인 예약 취소"},
        {"command": "help", "description": "❓ 도움말"},
    ]

    # ========== 시작 및 안내 메시지 ==========
    WELCOME = """🚄 근삼 코레일 봇을 이용해 주셔서 감사합니다.

본 프로그램은 매진 열차 자동 예약을 위한 서비스입니다.
예약 완료 시 결제는 10분 이내에 직접 진행해주셔야 합니다.

📋 예약 정보 입력 순서
━━━━━━━━━━━━━━━━
  1. 코레일 로그인 정보
  2. 출발 희망일
  3. 출발역
  4. 도착역
━━━━━━━━━━━━━━━━

계속 진행하시려면 "예" 또는 "Y"를 입력해주세요.
"""

    WELCOME_PRECONFIGURED = """🚄 근삼 코레일 봇을 이용해 주셔서 감사합니다.

본 프로그램은 매진 열차 자동 예약을 위한 서비스입니다.
예약 완료 시 결제는 10분 이내에 직접 진행해주셔야 합니다.

🔑 코레일 계정이 서버에 설정되어 있어 로그인 정보는 묻지 않습니다.

📋 예약 정보 입력 순서
━━━━━━━━━━━━━━━━
  1. 출발 희망일
  2. 출발역
  3. 도착역
━━━━━━━━━━━━━━━━

계속 진행하시려면 "예" 또는 "Y"를 입력해주세요.
"""

    HELP = """📌 사용 가능한 명령어

🎫 예약 관련
  /start - 예약 시작
  /cancel - 진행 중인 예약 취소

ℹ️ 정보 확인
  /status - 예약 상태 확인
  /help - 도움말 보기

🔧 관리자 명령어 (인증 필요)
  /subscribe - 알림 구독
  /allusers - 전체 사용자 확인
  /cancelall - 전체 예약 취소
  /broadcast [메시지] - 공지사항 전송
  /flushredis - Redis 메모리 초기화 (⚠️ 위험)
  /debug_on - 상세 디버그 로그 활성화
  /debug_off - 디버그 로그 비활성화

💡 결제 알림은 예약 성공 후 아무 메시지나 입력하면 중단됩니다.
"""

    # ========== 로그인 관련 메시지 ==========
    REQUEST_PHONE = """📱 코레일 로그인 정보 입력을 시작합니다.

현재 휴대폰 번호 로그인만 지원됩니다.
(코레일 회원번호나 이메일로는 로그인할 수 없습니다)

휴대전화번호를 입력해 주세요.
예시: 010-1234-5678 또는 01012345678

💡 하이픈(-)은 있어도 없어도 됩니다.
💡 취소를 원하시면 /cancel을 입력하세요.
"""

    REQUEST_PASSWORD = """✅ 휴대폰 번호 입력 완료

🔒 코레일 계정 비밀번호를 입력해주세요.
(코레일톡·홈페이지 로그인에 쓰는 그 비밀번호입니다)

⚠️ 입력한 비밀번호는 이 대화 기록에 남습니다.
   입력 후 해당 메시지를 삭제하시는 것을 권합니다.
"""

    LOGIN_SUCCESS = """✅ 로그인 성공!

📅 출발 희망일을 8자리로 입력해주세요.
예시: 20250425 (2025년 4월 25일)
"""

    LOGIN_SUCCESS_PRECONFIGURED = """✅ 서버에 설정된 코레일 계정으로 로그인했습니다. ({username})

📅 출발 희망일을 8자리로 입력해주세요.
예시: 20250425 (2025년 4월 25일)
"""

    PRECONFIGURED_LOGIN_FAILED = """⚠️ 서버에 설정된 코레일 계정으로 로그인하지 못했습니다.

비밀번호가 변경되었을 수 있습니다. 직접 입력하는 방식으로 진행합니다.

📱 휴대전화번호를 입력해 주세요.
예시: 010-1234-5678 또는 01012345678

💡 하이픈(-)은 있어도 없어도 됩니다.
💡 취소를 원하시면 /cancel을 입력하세요.
"""

    LOGIN_FAILED_RETRY = """❌ 로그인 실패

입력하신 정보:
━━━━━━━━━━━━━━
아이디: {username}
비밀번호: 보안상 비공개
━━━━━━━━━━━━━━

다음 중 하나를 선택해주세요:
  • Y 또는 예 → 계정정보 다시 입력
  • N 또는 아니오 → 작업 취소
  • 비밀번호만 다시 입력 → 같은 아이디로 재시도

⚠️ 주의: 5회 이상 로그인 실패 시 코레일 홈페이지에서 비밀번호를 재설정해야 합니다.
"""

    # ========== 예약 정보 입력 메시지 ==========
    REQUEST_DATE = """✅ 출발일 입력 완료

🚉 출발역을 입력해주세요.
예시: 광명, 서울, 부산 등

💡 역 이름만 입력 ('역' 제외)
📍 역 목록: http://www.letskorail.com/ebizprd/stationKtxList.do
"""

    REQUEST_SRC_STATION = """✅ 출발역 입력 완료

🏁 도착역을 입력해주세요.
예시: 광주송정, 대전, 동대구 등

💡 역 이름만 입력 ('역' 제외)
📍 역 목록: http://www.letskorail.com/ebizprd/stationKtxList.do
"""

    REQUEST_DST_STATION = """✅ 도착역 입력 완료

🕐 검색 시작 시각을 입력해주세요.

형식: HHMM (24시간 기준, 4자리)
예시: 1305 (오후 1시 5분 이후 열차 검색)
"""

    REQUEST_DEP_TIME = """✅ 검색 시작 시각 입력 완료

🕐 검색 종료 시각을 입력해주세요.

형식: HHMM (24시간 기준, 4자리)
예시: 1800 (오후 6시까지의 열차만 검색)

💡 시간 제한 없이 검색하려면 2400 입력 (권장)
"""

    REQUEST_TRAIN_TYPE = """✅ 시간 입력 완료

🚄 열차 종류를 선택해주세요.

1️⃣ KTX / KTX-산천만 예약
2️⃣ 모든 열차 포함

숫자를 입력하세요: 1 또는 2
"""

    REQUEST_SEAT_TYPE = """✅ 열차 종류 선택 완료

💺 좌석 종류를 선택해주세요.

1️⃣ 일반실 우선
2️⃣ 일반실만
3️⃣ 특실 우선
4️⃣ 특실만

숫자를 입력하세요: 1, 2, 3, 4
"""

    REQUEST_PASSENGER_COUNT = """✅ 좌석 종류 선택 완료

👥 탑승 인원수를 입력해주세요.

💡 1~9명까지 선택 가능합니다.
(현재는 성인 인원수만 지원합니다)

예) 2명이 탑승하는 경우: 2
"""

    REQUEST_SEAT_STRATEGY = """✅ 인원수 입력 완료 (총 {count}명)

🪑 좌석 배치 방식을 선택해 주십시오.

━━━━━━━━━━━━━━━━━━━━
1️⃣ 연속 좌석 (권장)
   • 같이 앉을 수 있도록 연속된 좌석 예약
   • 연속된 좌석이 없으면 예약 실패

2️⃣ 랜덤 배치
   • 한 자리씩 개별적으로 예약
   • 좌석이 떨어져 있을 수 있음
   • 예약 성공률이 더 높음
━━━━━━━━━━━━━━━━━━━━

숫자를 입력하세요: 1 또는 2
"""

    SELECT_TRAINS = """✅ 좌석 배치 선택 완료

🚄 감시할 열차를 선택해주세요.

📍 {srcLocate} → {dstLocate}
📅 {depDate}  🕐 {depTime}~{maxDepTime}
━━━━━━━━━━━━━━━━━━━━
조회된 열차 {count}개{truncated}

원하는 열차를 눌러 선택하세요. 여러 개 고를 수 있습니다.
선택한 열차에 취소표가 나오면 바로 잡습니다.

💡 특정 열차를 고집할 이유가 없다면 **시간대 전체 감시**가
   성공률이 훨씬 높습니다.
━━━━━━━━━━━━━━━━━━━━

⌨️ 열차번호를 직접 입력해도 됩니다 (예: 101 105)"""

    SELECT_TRAINS_TRUNCATED = "\n⚠️ 너무 많아 앞의 {shown}개만 표시합니다"

    TRAIN_LIST_EMPTY = """⚠️ 해당 시간대에 운행하는 열차가 없습니다.

조건에 맞는 열차를 찾지 못했습니다. 날짜나 시간대를 바꿔
다시 시도해보세요.

시간대 전체를 감시하는 방식으로 계속할 수도 있습니다."""

    TRAIN_LIST_FAILED = """⚠️ 열차 목록을 불러오지 못했습니다.

코레일 응답을 받지 못했습니다. 열차를 골라서 감시하는 대신
시간대 전체를 감시하는 방식으로 진행합니다.

(전체 감시는 원래 이 봇의 기본 동작이며 성공률이 더 높습니다)"""

    TRAIN_SELECT_UNKNOWN = """❌ '{value}' 은(는) 목록에 없는 열차번호입니다.

목록의 버튼을 누르거나, 표시된 열차번호를 입력해주세요."""

    CONFIRM_RESERVATION = """✅ 모든 정보 입력 완료!

📋 예약 정보 확인
━━━━━━━━━━━━━━━━━━━━
📅 출발일: {depDate}
🚉 출발역: {srcLocate}
🏁 도착역: {dstLocate}
🕐 검색 시작: {depTime}
⏰ 검색 종료: {maxDepTime}
🚄 열차: {trainTypeShow}
💺 좌석: {specialInfoShow}
👥 인원: {passengerCount}명
🪑 배치: {seatStrategy}
🎯 감시: {trainWatch}
━━━━━━━━━━━━━━━━━━━━

• Y 또는 예 → 예약 시작
• N 또는 아니오 → 작업 취소

⏱ 예약 완료까지 시간이 걸릴 수 있습니다.
"""

    RESERVATION_STARTED = """🎯 예약 검색을 시작합니다!

🔍 매진된 자리에 공석이 생길 때까지 계속 확인합니다.
✅ 예약 성공 시 즉시 알려드립니다!

💡 진행 중인 예약을 취소하려면 /cancel을 입력하세요.
"""

    ALREADY_RUNNING = """⚠️ 이미 예약이 진행 중입니다.

📋 진행 중인 예약 정보
━━━━━━━━━━━━━━━━━━━━
📅 출발일: {depDate}
🚉 출발역: {srcLocate}
🏁 도착역: {dstLocate}
🕐 검색 시작: {depTime}
🚄 열차: {trainTypeShow}
💺 좌석: {specialInfoShow}
━━━━━━━━━━━━━━━━━━━━

💡 예약을 취소하려면 /cancel을 입력하세요.
"""

    # ========== 에러 메시지 ==========
    ERROR_GENERIC = "⚠️ 오류가 발생했습니다.\n/cancel 또는 /start로 다시 시작해주세요."
    ERROR_INVALID_COMMAND = "❌ 알 수 없는 명령어입니다.\n/help로 사용 가능한 명령어를 확인하세요."
    ERROR_NO_PROGRESS = "ℹ️ 진행 중인 예약이 없습니다.\n/start를 입력하여 예약을 시작하세요."
    ERROR_PHONE_FORMAT = "❌ 전화번호 형식이 올바르지 않습니다.\n하이픈(-)을 포함하여 다시 입력해주세요.\n예시: 010-1234-5678"
    ERROR_DATE_FORMAT = """❌ 날짜 형식이 올바르지 않습니다.

8자리 숫자로 입력해주세요.
예시: 20250425 (2025년 4월 25일)

⚠️ 과거 날짜는 입력할 수 없습니다.
"""
    ERROR_TIME_FORMAT = "❌ 시간 형식이 올바르지 않습니다.\nHHMM 형식 4자리로 입력해주세요.\n예시: 1430 (오후 2시 30분)"
    ERROR_TRAIN_TYPE_INVALID = "❌ 1 또는 2를 입력해주세요."
    ERROR_SEAT_TYPE_INVALID = "❌ 1, 2, 3, 4 중 하나를 입력해주세요."
    ERROR_PASSENGER_COUNT_NOT_DIGIT = "❌ 숫자를 입력해주세요. (1~9)"
    ERROR_PASSENGER_COUNT_RANGE = "❌ 1~9명 사이의 인원수를 입력해주세요."
    ERROR_SEAT_STRATEGY_INVALID = "❌ 1 또는 2를 입력해주세요."
    ERROR_CONFIRM_INVALID = """❌ 올바른 응답을 입력해주세요.

• Y 또는 예 → 예약 시작
• N 또는 아니오 → 작업 취소
"""
    ERROR_ADMIN_ENV = "⚠️ 서버 환경변수가 설정되지 않았습니다."
    ERROR_ADMIN_LOGIN = "⚠️ 관리자 계정 로그인에 실패했습니다."
    ERROR_RESERVATION_START_FAILED = "❌ 예약 프로세스 시작에 실패했습니다.\n다시 시도해주세요."
    # Self-hosted deployments are the norm for this bot, so this must not
    # point users at anyone else's paid service.
    ERROR_NOT_SUBSCRIBER = """🚫 사용 권한이 없습니다.

입력하신 번호가 이 봇의 허용 목록(ALLOW_LIST)에 없습니다.

이 봇을 직접 운영하신다면 .env 의 ALLOW_LIST 에 번호를 추가하거나,
제한 없이 열려면 값을 비워두세요.

그 외에는 봇 운영자에게 문의해주세요.
"""
    # ========== 취소 및 완료 메시지 ==========
    CANCELLED = "✅ 예약이 취소되었습니다."
    CANCELLED_BY_USER = "🚫 예약을 취소합니다."
    CANCEL_START_CONFIRMATION = "🚫 예매 진행을 취소합니다."

    PAYMENT_VERIFIED = """✅ 결제가 확인되었습니다

코레일에서 예약이 결제 완료 처리된 것을 확인했습니다.
더 이상 알림을 보내지 않습니다.

즐거운 여행 되세요! 🚄"""

    PAYMENT_EXPIRED_VERIFIED = """❌ 결제 기한이 지나 예약이 취소되었습니다

코레일에 확인한 결과 결제가 완료되지 않았습니다.
좌석은 다시 풀렸습니다.

다시 잡으시려면 /start 로 검색을 시작하세요."""

    PAYMENT_REMINDER_STOPPED = """✅ 결제 리마인더가 중단되었습니다.

결제를 완료하셨다면 즐거운 여행 되세요! 🚄
아직 결제하지 않으셨다면 서둘러 결제를 완료해주세요.
"""

    PAYMENT_REMINDER_TIMEOUT = """⏰ 결제 리마인더 종료

예약 후 10분이 경과하여 리마인더가 자동 종료되었습니다.
결제를 완료하지 않으셨다면 예약이 취소되었을 수 있습니다.

💡 코레일 사이트에서 예약 상태를 확인해주세요.
"""

    # ========== 재시작 복구 메시지 ==========
    RESERVATION_RESUMED = """🔄 검색을 다시 시작했습니다

서버가 재시작되어 검색이 잠시 멈췄지만, 자동으로 이어서 진행합니다.

📋 {srcLocate} → {dstLocate}
📅 {depDate}

취소표가 나오면 바로 알려드립니다."""

    RESERVATION_INTERRUPTED = """⚠️ 검색이 중단되었습니다

서버가 재시작되면서 진행 중이던 검색이 멈췄습니다.

📋 {srcLocate} → {dstLocate}
📅 {depDate}

/start 를 입력해 다시 시작해주세요."""

    RESERVATION_INTERRUPTED_PARTIAL = """⚠️ 검색이 중단되었습니다

서버가 재시작되면서 검색이 멈췄습니다.
이미 예약된 좌석이 있어 중복 예약을 피하려고 자동 재개는 하지 않았습니다.

🔗 예약 확인: {paymentUrl}

남은 좌석이 필요하시면 /start 로 다시 시작해주세요."""

    # ========== 관리자 메시지 ==========
    ADMIN_AUTH_REQUIRED = "🔐 관리자 인증이 필요합니다.\n관리자 비밀번호를 입력해주세요."
    ADMIN_AUTH_SUCCESS = "✅ 관리자 인증 성공!"
    ADMIN_AUTH_FAILED = "❌ 관리자 인증 실패\n올바른 비밀번호를 입력해주세요."
    ADMIN_AUTH_FAILED_REMAINING = """❌ 관리자 인증 실패

남은 시도 횟수: {remaining}회
초과 시 {lockout_minutes}분간 인증이 차단됩니다."""
    ADMIN_AUTH_LOCKED = """🚫 관리자 인증이 일시적으로 차단되었습니다.

인증 실패 횟수를 초과했습니다.
{remaining_minutes}분 후에 다시 시도해주세요."""
    ADMIN_DISABLED = """🚫 관리자 기능이 비활성화되어 있습니다.

서버에 ADMIN_PASSWORD 환경변수가 설정되지 않았습니다."""

    # ========== Backward Compatibility Methods for MessageTemplates ==========
    # These methods provide compatibility with the old MessageTemplates interface

    @staticmethod
    def welcome_message(skip_login_prompts: bool = False):
        """Welcome message (compatibility method)

        The list of steps has to match what the user is actually asked for,
        so it drops the login step when the server logs in on its own.
        """
        if skip_login_prompts:
            return Messages.WELCOME_PRECONFIGURED
        return Messages.WELCOME

    @staticmethod
    def request_phone_number():
        """Request phone number (compatibility method)"""
        return Messages.REQUEST_PHONE

    @staticmethod
    def request_password():
        """Request password (compatibility method)"""
        return Messages.REQUEST_PASSWORD

    @staticmethod
    def login_success():
        """Login success (compatibility method)"""
        return Messages.LOGIN_SUCCESS

    @staticmethod
    def preconfigured_login_success(username: str):
        """Login success with the account from the environment.

        The account is masked: the operator knows which one it is, and the
        message lands in a chat transcript that outlives the reservation.
        """
        from korail_bot.utils.privacy import mask_phone

        return Messages.LOGIN_SUCCESS_PRECONFIGURED.format(username=mask_phone(username))

    @staticmethod
    def login_failure(username: str):
        """Login failure (compatibility method)"""
        return Messages.LOGIN_FAILED_RETRY.format(username=username)

    @staticmethod
    def request_departure_station():
        """Request departure station after date input (compatibility method)"""
        return Messages.REQUEST_DATE

    @staticmethod
    def request_arrival_station():
        """Request arrival station after departure station input (compatibility method)"""
        return Messages.REQUEST_SRC_STATION

    @staticmethod
    def not_in_allow_list():
        """Not in allow list (compatibility method)"""
        return Messages.ERROR_NOT_SUBSCRIBER

    @staticmethod
    def reservation_started():
        """Reservation started (compatibility method)"""
        return Messages.RESERVATION_STARTED

    @staticmethod
    def reservation_cancelled():
        """Reservation cancelled (compatibility method)"""
        return Messages.CANCELLED

    @staticmethod
    def help_message():
        """Help message (compatibility method)"""
        return Messages.HELP

    @staticmethod
    def payment_reminder(remaining_minutes: int, remaining_seconds: int):
        """Payment reminder (compatibility method)"""
        if remaining_seconds == 0:
            time_text = f"{remaining_minutes}분"
        else:
            time_text = f"{remaining_minutes}분 {remaining_seconds}초"

        return f"""⏰ 결제 리마인더

예약 취소까지 남은 시간: {time_text}

서둘러 결제를 완료해주세요!
💡 결제 완료 후 아무 메시지나 입력하면 알림이 중단됩니다.
"""


class MessageService:
    """메시지 전송 서비스"""

    def __init__(self, session, send_url):
        """
        Args:
            session: requests.session() 객체
            send_url: 텔레그램 API URL
        """
        self.session = session
        self.send_url = send_url

    def send(self, chat_id, message):
        """
        메시지 전송

        Args:
            chat_id: 채팅 ID
            message: 전송할 메시지 (str 또는 템플릿 메서드)
        """
        url = f"{self.send_url}/sendMessage"
        params = {"chat_id": chat_id, "text": message}
        self.session.get(url, params=params)

    def send_to_multiple(self, chat_ids, message):
        """
        여러 사용자에게 메시지 전송

        Args:
            chat_ids: 채팅 ID 리스트
            message: 전송할 메시지
        """
        for chat_id in chat_ids:
            self.send(chat_id, message)
