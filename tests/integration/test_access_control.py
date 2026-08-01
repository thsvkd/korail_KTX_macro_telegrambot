"""
Integration tests for who is allowed to make this bot talk to Korail.

A Telegram bot is findable by name and anyone who opens it can talk to it, so
without a gate the operator's server runs searches for strangers - and it is
the operator's IP that Korail sees. The gate is deliberately not a wall: a
few searches for anyone, and one button press for the operator to let someone
in properly.

Run against a real Redis because the counting and the approvals are exactly
the parts that have to survive a restart.
"""

from unittest.mock import Mock, patch

import pytest

from korail_bot.config.settings import Settings, settings
from korail_bot.handlers.command_handler import CommandHandler
from korail_bot.models import ApprovedUser, OnboardedAccount, Operator
from korail_bot.services import (
    AccessLevel,
    AccessService,
    PaymentReminderService,
    ReservationService,
    TelegramService,
)
from korail_bot.storage import RedisStorage
from korail_bot.telegramBot import keyboards
from korail_bot.utils.crypto import identity_hash

CHAT_ID = 90210
OPERATOR_CHAT = 5150
PHONE = "010-1234-5678"
OTHER_PHONE = "010-9999-8888"


@pytest.fixture
def storage():
    storage = RedisStorage()
    storage.redis.flushdb()
    yield storage
    storage.redis.flushdb()


@pytest.fixture
def access(storage):
    return AccessService(storage)


@pytest.fixture
def telegram():
    return Mock(spec=TelegramService)


@pytest.fixture
def commands(storage, telegram, access):
    handler = CommandHandler(
        storage, telegram, Mock(spec=ReservationService), Mock(spec=PaymentReminderService)
    )
    handler.access = access
    return handler


def _texts(telegram):
    return [
        call.args[1] if len(call.args) > 1 else call.kwargs.get("text", "")
        for call in telegram.send_message.call_args_list
    ]


def _trial_limit(limit):
    return patch.object(Settings, "TRIAL_SEARCH_LIMIT", limit)


class TestTheTrialAllowance:
    """Everyone gets a few searches before anyone has to decide about them."""

    def test_a_new_number_is_on_trial(self, access):
        with _trial_limit(3):
            decision = access.evaluate(PHONE)

        assert decision.level == AccessLevel.TRIAL
        assert decision.allowed is True
        assert decision.remaining == 3

    def test_evaluating_does_not_cost_anything(self, access, storage):
        """
        Asking is free. A user who backed out at the summary screen must not
        have been charged for the search they did not run.
        """
        with _trial_limit(3):
            access.evaluate(PHONE)
            access.evaluate(PHONE)

        assert storage.get_trial_count(identity_hash(PHONE)) == 0

    def test_the_allowance_runs_out(self, access):
        with _trial_limit(2):
            for _ in range(2):
                decision = access.evaluate(PHONE)
                access.consume(PHONE, decision)

            after = access.evaluate(PHONE)

        assert after.level == AccessLevel.EXHAUSTED
        assert after.allowed is False

    def test_it_is_counted_per_number_not_per_chat(self, access, storage):
        """
        The reason the count hangs off the Korail number: a new Telegram
        account is free, so per-chat counting is a limit anyone can reset.
        """
        with _trial_limit(1):
            decision = access.evaluate(PHONE)
            access.consume(PHONE, decision)

            # Same person, new Telegram account - same Korail number.
            assert access.evaluate(PHONE).level == AccessLevel.EXHAUSTED
            # A genuinely different person is unaffected.
            assert access.evaluate(OTHER_PHONE).level == AccessLevel.TRIAL

    def test_the_number_itself_is_not_stored(self, access, storage):
        with _trial_limit(3):
            decision = access.evaluate(PHONE)
            access.consume(PHONE, decision)

        keys = list(storage.redis.scan_iter(match="trial:*"))
        assert keys
        assert not any("1234" in str(key) for key in keys)

    def test_a_negative_limit_never_runs_out(self, access):
        with _trial_limit(-1):
            for _ in range(10):
                decision = access.evaluate(PHONE)
                access.consume(PHONE, decision)

            assert access.evaluate(PHONE).allowed is True

    def test_a_zero_limit_requires_approval_from_the_start(self, access):
        with _trial_limit(0):
            assert access.evaluate(PHONE).level == AccessLevel.EXHAUSTED


class TestBeingApproved:
    """The ways to be allowed without counting."""

    def test_a_preapproved_number_skips_the_trial(self, access):
        with _trial_limit(0), patch.object(Settings, "PREAPPROVED_USERS", [PHONE]):
            decision = access.evaluate(PHONE)

        assert decision.level == AccessLevel.APPROVED
        assert decision.allowed is True

    def test_an_approved_number_skips_the_trial(self, access, storage):
        storage.save_approved_user(
            ApprovedUser(phone_hash=identity_hash(PHONE), masked_phone="010-****-5678")
        )

        with _trial_limit(0):
            assert access.evaluate(PHONE).level == AccessLevel.APPROVED

    def test_a_developer_chat_is_never_limited(self, access):
        with _trial_limit(0):
            assert access.evaluate(PHONE, is_developer=True).level == AccessLevel.DEVELOPER

    def test_approval_stops_the_counter_being_charged(self, access, storage):
        with _trial_limit(3), patch.object(Settings, "PREAPPROVED_USERS", [PHONE]):
            decision = access.evaluate(PHONE)
            access.consume(PHONE, decision)

        assert storage.get_trial_count(identity_hash(PHONE)) == 0


class TestAskingForAccess:
    """What someone does when the trial runs out."""

    def test_a_request_is_recorded(self, access, storage):
        request = access.request_access(CHAT_ID, PHONE)

        assert request is not None
        assert request.masked_phone == "010-****-5678"
        assert storage.get_access_request(identity_hash(PHONE)) is not None

    def test_asking_twice_does_not_queue_twice(self, access):
        access.request_access(CHAT_ID, PHONE)

        assert access.request_access(CHAT_ID, PHONE) is None
        assert len(access.pending_requests()) == 1

    def test_requests_come_back_oldest_first(self, access, storage):
        access.request_access(CHAT_ID, PHONE)
        access.request_access(CHAT_ID + 1, OTHER_PHONE)

        pending = access.pending_requests()
        assert [r.masked_phone for r in pending] == ["010-****-5678", "010-****-8888"]

    def test_approving_lets_them_back_in(self, access, storage):
        access.request_access(CHAT_ID, PHONE)

        granted = access.approve(identity_hash(PHONE), approved_by=OPERATOR_CHAT)

        assert granted is not None
        assert granted.chat_id == CHAT_ID
        with _trial_limit(0):
            assert access.evaluate(PHONE).level == AccessLevel.APPROVED
        # The request is settled, so it stops showing up in the list.
        assert access.pending_requests() == []

    def test_rejecting_drops_the_request_without_a_permanent_no(self, access):
        access.request_access(CHAT_ID, PHONE)

        turned_down = access.reject(identity_hash(PHONE))

        assert turned_down is not None
        assert access.pending_requests() == []
        # Nothing stops them asking again - a friend of a friend may be worth
        # allowing later, and a permanent refusal would need an undo nobody
        # would find.
        assert access.request_access(CHAT_ID, PHONE) is not None

    def test_approving_something_already_gone_says_so(self, access):
        assert access.approve(identity_hash(PHONE), approved_by=OPERATOR_CHAT) is None


class TestRevokingApproval:
    """Letting someone in has to be reversible."""

    def test_it_takes_the_approval_away(self, access, storage):
        access.request_access(CHAT_ID, PHONE)
        access.approve(identity_hash(PHONE), approved_by=OPERATOR_CHAT)

        revoked = access.revoke(identity_hash(PHONE))

        assert revoked is not None
        assert storage.is_approved(identity_hash(PHONE)) is False

    def test_the_trial_is_not_handed_back(self, access, storage):
        """
        Someone approved and then revoked has already used the bot. A fresh
        allowance would make revoking mean less than it says.
        """
        with _trial_limit(1):
            decision = access.evaluate(PHONE)
            access.consume(PHONE, decision)

            access.request_access(CHAT_ID, PHONE)
            access.approve(identity_hash(PHONE), approved_by=OPERATOR_CHAT)
            access.revoke(identity_hash(PHONE))

            assert access.evaluate(PHONE).level == AccessLevel.EXHAUSTED

    def test_revoking_nothing_reports_nothing(self, access):
        assert access.revoke(identity_hash(PHONE)) is None


class TestTheOperatorsLists:
    """/approve and /users, and the buttons on them."""

    def _pending(self, access, storage):
        storage.save_onboarded_account(
            OnboardedAccount(chat_id=CHAT_ID, korail_id=PHONE, korail_pw="pw")
        )
        return access.request_access(CHAT_ID, PHONE)

    def test_approve_lists_pending_requests(self, commands, access, storage, telegram):
        self._pending(access, storage)

        commands.handle_approve(OPERATOR_CHAT)

        markup = telegram.send_message.call_args.kwargs["reply_markup"]
        answers = [b["callback_data"] for row in markup["inline_keyboard"] for b in row]
        assert any(
            a.startswith(f"{keyboards.STEP_APPROVE}:{keyboards.APPROVE_PICK}") for a in answers
        )

    def test_approve_says_so_when_there_is_nothing(self, commands, telegram):
        commands.handle_approve(OPERATOR_CHAT)

        assert "없습니다" in " ".join(_texts(telegram))

    def test_pressing_approve_grants_and_tells_the_asker(self, commands, access, storage, telegram):
        request = self._pending(access, storage)

        commands.handle_access_callback(
            OPERATOR_CHAT,
            None,
            keyboards.STEP_APPROVE,
            f"{keyboards.APPROVE_YES}{request.phone_hash}",
        )

        assert storage.is_approved(request.phone_hash) is True
        # The person who asked is the whole point of approving.
        assert any(call.args[0] == CHAT_ID for call in telegram.send_message.call_args_list)

    def test_pressing_reject_tells_the_asker_too(self, commands, access, storage, telegram):
        request = self._pending(access, storage)

        commands.handle_access_callback(
            OPERATOR_CHAT,
            None,
            keyboards.STEP_APPROVE,
            f"{keyboards.APPROVE_NO}{request.phone_hash}",
        )

        assert storage.is_approved(request.phone_hash) is False
        assert any(call.args[0] == CHAT_ID for call in telegram.send_message.call_args_list)

    def test_users_lists_approved_people(self, commands, access, storage, telegram):
        request = self._pending(access, storage)
        access.approve(request.phone_hash, approved_by=OPERATOR_CHAT)

        commands.handle_users(OPERATOR_CHAT)

        markup = telegram.send_message.call_args.kwargs["reply_markup"]
        answers = [b["callback_data"] for row in markup["inline_keyboard"] for b in row]
        assert any(a.startswith(f"{keyboards.STEP_USERS}:{keyboards.USERS_PICK}") for a in answers)

    def test_pressing_revoke_withdraws_the_approval(self, commands, access, storage):
        request = self._pending(access, storage)
        access.approve(request.phone_hash, approved_by=OPERATOR_CHAT)

        commands.handle_access_callback(
            OPERATOR_CHAT,
            None,
            keyboards.STEP_USERS,
            f"{keyboards.USERS_REVOKE}{request.phone_hash}",
        )

        assert storage.is_approved(request.phone_hash) is False


class TestWhoMayAdminister:
    """The tools are only as private as the check in front of them."""

    def test_a_developer_chat_may(self, commands, storage):
        storage.set_developer(OPERATOR_CHAT, True)

        assert commands.may_administer(OPERATOR_CHAT) is True

    def test_an_ordinary_chat_may_not(self, commands):
        assert commands.may_administer(CHAT_ID) is False

    def test_turning_developer_mode_off_takes_it_away(self, commands, storage):
        storage.set_developer(OPERATOR_CHAT, True)
        storage.set_developer(OPERATOR_CHAT, False)

        assert commands.may_administer(OPERATOR_CHAT) is False


class TestConcurrencyCeiling:
    """
    Korail sees one IP, not one user. Ten approved people searching at once is
    ten times the request rate from where Korail is standing.
    """

    @pytest.fixture
    def reservation(self, storage, telegram):
        from korail_bot.services.reservation_service import ReservationService as Real

        return Real(storage, telegram)

    def _running(self, storage, count, operator=Operator.KORAIL):
        from korail_bot.models import RunningReservation, TrainSearchParams

        base = 1000 if operator is Operator.KORAIL else 2000
        for index in range(count):
            storage.save_running_reservation(
                RunningReservation(
                    chat_id=base + index,
                    process_id=9000 + base + index,
                    korail_id=f"010-0000-{index:04d}",
                    search_params=TrainSearchParams(
                        dep_date="20991231",
                        src_locate="수서" if operator is Operator.SRT else "서울",
                        dst_locate="부산",
                        dep_time="090000",
                        operator=operator,
                    ),
                    run_id=settings.RUN_ID,
                )
            )

    def test_room_below_the_ceiling(self, reservation, storage):
        self._running(storage, 2)

        with patch.object(Settings, "MAX_CONCURRENT_SEARCHES", 5):
            assert reservation._under_concurrency_limit(CHAT_ID) is True

    def test_refused_at_the_ceiling(self, reservation, storage, telegram):
        self._running(storage, 5)

        with patch.object(Settings, "MAX_CONCURRENT_SEARCHES", 5):
            assert reservation._under_concurrency_limit(CHAT_ID) is False

        assert "검색을 시작할 수 없습니다" in " ".join(_texts(telegram))

    def test_zero_disables_the_ceiling(self, reservation, storage):
        self._running(storage, 20)

        with patch.object(Settings, "MAX_CONCURRENT_SEARCHES", 0):
            assert reservation._under_concurrency_limit(CHAT_ID) is True

    def test_each_railway_has_its_own_allowance(self, reservation, storage, telegram):
        self._running(storage, 5, Operator.KORAIL)
        self._running(storage, 4, Operator.SRT)

        with patch.object(Settings, "MAX_CONCURRENT_SEARCHES", 5):
            assert reservation._under_concurrency_limit(CHAT_ID, Operator.SRT) is True
            self._running(storage, 5, Operator.SRT)
            assert reservation._under_concurrency_limit(CHAT_ID, Operator.SRT) is False

        assert "SRT 검색" in " ".join(_texts(telegram))
