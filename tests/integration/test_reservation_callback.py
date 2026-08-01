"""Integration tests for the loopback reservation callback."""

from unittest.mock import Mock

from flask import Flask
from flask_restful import Api

from korail_bot.api import ReservationCallbackAPI
from korail_bot.config.settings import settings
from korail_bot.models import UserCredentials, UserProgress, UserSession
from korail_bot.services import PaymentReminderService, TelegramService
from korail_bot.storage import RedisStorage


class TestReservationCallback:
    def setup_method(self):
        self.storage = RedisStorage()
        self.telegram = Mock(spec=TelegramService)
        self.payment_reminder = Mock(spec=PaymentReminderService)

        app = Flask(__name__)
        api = Api(app)
        api.add_resource(
            ReservationCallbackAPI,
            "/reservation-callback",
            resource_class_kwargs={
                "storage": self.storage,
                "telegram_service": self.telegram,
                "payment_reminder_service": self.payment_reminder,
            },
        )
        self.client = app.test_client()
        self.token = settings.INTERNAL_CALLBACK_TOKEN

    def teardown_method(self):
        self.storage.redis.flushdb()

    def callback(self, **query):
        return self.client.get(
            "/reservation-callback",
            query_string={"token": self.token, **query},
        )

    def test_success_notifies_user_starts_reminder_and_resets_session(self):
        chat_id = 12345
        session = UserSession(
            chat_id=chat_id,
            in_progress=True,
            last_action=UserProgress.FINDING_TICKET,
            credentials=UserCredentials(korail_id="010-1234-5678", korail_pw="password"),
        )
        self.storage.save_user_session(session)

        response = self.callback(
            chatId=str(chat_id),
            msg="예약 성공!",
            status="0",
            isMulti="0",
            totalSeats="1",
            seatStrategy="consecutive",
        )

        assert response.status_code == 200
        self.telegram.send_message.assert_called_once_with(chat_id, "예약 성공!")
        self.payment_reminder.start_reminders.assert_called_once_with(chat_id)
        assert self.storage.get_user_session(chat_id).in_progress is False

    def test_failure_notifies_user(self):
        response = self.callback(chatId="12345", msg="예약 실패", status="1")

        assert response.status_code == 200
        self.telegram.send_message.assert_called_once_with(12345, "예약 실패")

    def test_partial_reservation_does_not_start_single_reminder(self):
        response = self.callback(
            chatId="12345",
            msg="첫 좌석 예약 완료",
            status="2",
            isMulti="1",
            totalSeats="3",
            seatStrategy="random",
        )

        assert response.status_code == 200
        self.telegram.send_message.assert_called_once_with(12345, "첫 좌석 예약 완료")
        self.payment_reminder.start_reminders.assert_not_called()

    def test_missing_parameters_are_acknowledged_without_sending(self):
        response = self.callback(chatId="12345")

        assert response.status_code == 200
        self.telegram.send_message.assert_not_called()

    def test_missing_token_is_rejected(self):
        response = self.client.get(
            "/reservation-callback",
            query_string={"chatId": "12345", "msg": "위조", "status": "0"},
        )

        assert response.status_code == 403
        self.telegram.send_message.assert_not_called()

    def test_remote_source_is_rejected_even_with_token(self):
        response = self.client.get(
            "/reservation-callback",
            query_string={
                "token": self.token,
                "chatId": "12345",
                "msg": "외부 요청",
                "status": "0",
            },
            environ_base={"REMOTE_ADDR": "203.0.113.7"},
        )

        assert response.status_code == 403
        self.telegram.send_message.assert_not_called()
