"""
The Mini App doing the same work as the chat, under the same rules.

The risk in giving a second surface the whole reservation flow is not that it
breaks - it is that it quietly becomes a way round something. The trial limit
is enforced in one place; a screen that started searches by its own route
would be a screen with no trial limit. The duplicate-search guard is what
stops one chat running two processes; a screen that overwrote the session
would be a screen that loses a running search.

So these tests are mostly about the gateway *not* having its own opinion:
that it refuses where the chat refuses, and that when it does start a search
it goes through the same start_booking the confirmation button goes through.
"""

from unittest.mock import Mock

import pytest

from korail_bot.handlers.conversation_handler import BookingOutcome, ConversationHandler
from korail_bot.models import (
    OnboardedAccount,
    Operator,
    RunningReservation,
    TrainSearchParams,
    UserProgress,
    UserSession,
)
from korail_bot.services.mini_app_gateway import MiniAppError, MiniAppGateway
from korail_bot.services.pending_payment_service import PendingPaymentService
from korail_bot.services.reservation_service import ReservationService
from korail_bot.services.scheduled_search_service import ScheduledSearchService
from korail_bot.services.telegram_service import TelegramService
from korail_bot.storage.base import StorageInterface

CHAT_ID = 12345

CONDITIONS = {
    "v": 1,
    "action": "prepare_search",
    "operator": "korail",
    "dep_date": "20261225",
    "src_station": "서울",
    "dst_station": "부산",
    "dep_time": "0900",
    "max_dep_time": "1800",
    "train_type": "1",
    "seat_option": "1",
    "passenger_count": 1,
    "seat_strategy": "1",
}


@pytest.fixture
def storage():
    store = Mock(spec=StorageInterface)
    store.get_user_session.return_value = None
    store.get_running_reservation.return_value = None
    store.get_scheduled_search.return_value = None
    store.get_favourites.return_value = []
    store.get_progress_report_minutes.return_value = 0
    store.is_developer.return_value = False
    store.get_onboarded_account.return_value = OnboardedAccount(
        chat_id=CHAT_ID, korail_id="010-1234-5678", korail_pw="pw"
    )
    return store


@pytest.fixture
def conversation():
    handler = Mock(spec=ConversationHandler)
    handler.MAX_TRAIN_OPTIONS = 30
    handler.uses_server_account.return_value = False
    handler.fetch_train_options.return_value = [
        {"no": "101", "label": "KTX 101", "soldout": True},
    ]
    handler.start_booking.return_value = BookingOutcome(started=True)
    handler._rail_service.return_value = Mock(login=Mock(return_value=True))
    return handler


@pytest.fixture
def pending():
    service = Mock(spec=PendingPaymentService)
    service.pending.return_value = []
    return service


@pytest.fixture
def gateway(storage, conversation, pending):
    return MiniAppGateway(
        storage,
        Mock(spec=TelegramService),
        Mock(spec=ReservationService),
        conversation_handler=conversation,
        pending_payment_service=pending,
        scheduled_search_service=Mock(spec=ScheduledSearchService),
    )


def running_session():
    session = UserSession(chat_id=CHAT_ID, in_progress=True)
    session.last_action = UserProgress.FINDING_TICKET
    return session


class TestNotGoingRoundTheChatsRules:
    """The gateway must refuse exactly where the chat refuses."""

    def test_a_search_already_running_is_not_overwritten(self, gateway, storage):
        """
        Someone with a search going who opens the app and presses start has
        almost certainly forgotten it is going. Replacing it silently loses
        hours of watching.
        """
        storage.get_user_session.return_value = running_session()

        with pytest.raises(MiniAppError) as refusal:
            gateway.start_search(CHAT_ID, {"conditions": CONDITIONS})

        assert refusal.value.status == 409

    def test_starting_goes_through_the_shared_booking_path(self, gateway, conversation):
        """
        Not a copy of it. That method is where the access gate and the trial
        allowance live, and a second implementation would be a second set of
        rules.
        """
        gateway.start_search(CHAT_ID, {"conditions": CONDITIONS})

        conversation.start_booking.assert_called_once()

    def test_an_exhausted_trial_is_reported_rather_than_started(self, gateway, conversation):
        conversation.start_booking.return_value = BookingOutcome(
            started=False, needs_access_request=True, trial_used=3, trial_limit=3
        )

        result = gateway.start_search(CHAT_ID, {"conditions": CONDITIONS})

        assert result["started"] is False
        assert result["needsAccessRequest"] is True
        assert (result["trialUsed"], result["trialLimit"]) == (3, 3)

    def test_conditions_are_validated_by_the_existing_boundary(self, gateway):
        """
        The same validators the chat uses. A station this railway does not
        serve is refused here, not discovered by a search that never matches.
        """
        with pytest.raises(MiniAppError):
            gateway.start_search(CHAT_ID, {"conditions": {**CONDITIONS, "src_station": "없는역"}})

    def test_the_departure_and_arrival_may_not_be_the_same(self, gateway):
        with pytest.raises(MiniAppError):
            gateway.start_search(CHAT_ID, {"conditions": {**CONDITIONS, "dst_station": "서울"}})


class TestTrainNumbersFromTheRequest:
    """These become arguments to a search process, so they are not trusted."""

    def test_a_selection_is_carried_through(self, gateway, storage):
        gateway.start_search(CHAT_ID, {"conditions": CONDITIONS, "trains": ["101", "105"]})

        saved = storage.save_user_session.call_args.args[0]
        assert saved.train_info["selectedTrains"] == ["101", "105"]

    def test_something_that_is_not_a_train_number_is_refused(self, gateway):
        with pytest.raises(MiniAppError):
            gateway.start_search(CHAT_ID, {"conditions": CONDITIONS, "trains": ["101; rm -rf /"]})

    def test_more_trains_than_the_list_can_hold_is_refused(self, gateway):
        with pytest.raises(MiniAppError):
            gateway.start_search(
                CHAT_ID, {"conditions": CONDITIONS, "trains": [str(n) for n in range(100)]}
            )

    def test_no_selection_means_the_whole_window(self, gateway, storage):
        gateway.start_search(CHAT_ID, {"conditions": CONDITIONS})

        saved = storage.save_user_session.call_args.args[0]
        assert saved.train_info["selectedTrains"] == []


class TestLoggingIn:
    """A password is verified against the railway and never sent outward."""

    def test_without_a_registration_the_app_is_told_to_register(self, gateway, storage):
        storage.get_onboarded_account.return_value = None

        with pytest.raises(MiniAppError) as refusal:
            gateway.list_trains(CHAT_ID, {"conditions": CONDITIONS})

        assert refusal.value.status == 428

    def test_a_registration_the_railway_rejects_is_dropped(self, gateway, storage, conversation):
        """
        People change their password without telling the bot. Keeping the
        stale one means every later search fails the same way.
        """
        conversation._rail_service.return_value = Mock(login=Mock(return_value=False))

        with pytest.raises(MiniAppError) as refusal:
            gateway.list_trains(CHAT_ID, {"conditions": CONDITIONS})

        assert refusal.value.status == 401
        storage.delete_onboarded_account.assert_called_once_with(CHAT_ID, Operator.KORAIL)

    def test_no_response_ever_carries_a_password(self, gateway):
        """
        The one invariant worth asserting directly: the registered password
        goes into a login and nowhere else.
        """
        import json

        payload = json.dumps(gateway.bootstrap(CHAT_ID), ensure_ascii=False, default=str)

        assert "pw" not in json.loads(payload)["operators"]["korail"]
        assert "010-1234-5678" not in payload


class TestWhatTheAppIsToldOnOpening:
    """One round trip, because the alternative is a spinner."""

    def test_both_railways_are_described(self, gateway):
        state = gateway.bootstrap(CHAT_ID)["operators"]

        assert set(state) == {"korail", "srt"}

    def test_stations_are_served_rather_than_shipped_with_the_page(self, gateway):
        """
        They used to be a constant in the JavaScript, which meant Korail's
        several hundred stations were the seventeen someone typed out.
        """
        srt = gateway.bootstrap(CHAT_ID)["operators"]["srt"]

        assert len(srt["stations"]) > len(srt["majorStations"])

    def test_a_running_search_is_reported(self, gateway, storage):
        storage.get_running_reservation.return_value = RunningReservation(
            chat_id=CHAT_ID,
            process_id=999,
            korail_id="010-1234-5678",
            search_params=TrainSearchParams(
                dep_date="20261225",
                src_locate="서울",
                dst_locate="부산",
                dep_time="090000",
            ),
        )

        running = gateway.bootstrap(CHAT_ID)["running"]

        assert running["srcLocate"] == "서울"
        assert running["depTime"] == "0900"

    def test_nothing_running_reads_as_nothing_running(self, gateway):
        assert gateway.bootstrap(CHAT_ID)["running"] is None


class TestGivingSeatsBack:
    """Cancelling reaches this chat's own reservations, and only those."""

    def test_with_nothing_pending_there_is_nothing_to_cancel(self, gateway):
        with pytest.raises(MiniAppError) as refusal:
            gateway.cancel_pending(CHAT_ID)

        assert refusal.value.status == 404

    def test_the_reservation_numbers_come_from_storage_not_the_request(self, gateway, pending):
        """
        A reservation number is guessable and the railway would happily
        cancel a stranger's, so the request never names one.
        """
        from korail_bot.services.pending_payment_service import PendingReservation

        pending.pending.return_value = [
            PendingReservation(reservation_id="R1", train_info="KTX 101", expires_at=None)
        ]
        pending.cancel.return_value = True

        gateway.cancel_pending(CHAT_ID)

        pending.cancel.assert_called_once_with(CHAT_ID)


class TestNotificationInterval:
    """The same bounds /notify enforces, from the same settings."""

    def test_zero_turns_reports_off(self, gateway, storage):
        assert gateway.set_notify_minutes(CHAT_ID, 0)["notifyMinutes"] == 0
        storage.set_progress_report_minutes.assert_called_once_with(CHAT_ID, 0)

    def test_an_interval_outside_the_allowed_range_is_refused(self, gateway):
        with pytest.raises(MiniAppError):
            gateway.set_notify_minutes(CHAT_ID, 10_000)

    def test_something_that_is_not_a_number_is_refused(self, gateway):
        with pytest.raises(MiniAppError):
            gateway.set_notify_minutes(CHAT_ID, "매시간")
