"""
Giving an unpaid seat back.

Until now the bot could take a seat and never let go of one: changing your
mind meant going to the railway's own site, and the bot went on reminding you
to pay for something you had already dealt with.

The two railways are not equally willing. SR's client cancels outright. Korail's
does too, on paper - but it sends the GET with its parameters in the body, which
Korail ignores, so the call blows up without having cancelled anything. The same
values as query parameters are accepted, and that is the whole of the workaround
(docs/payment-automation-poc.md).

What matters more than either is what is claimed afterwards. A seat the bot
failed to release is still booked in the user's name, and "cancelled" must
never be said about one.
"""

from unittest.mock import Mock

from korail2 import KorailError, NoResultsError

from korail_bot.services.korail_service import KorailService
from korail_bot.services.rail_service import RailService
from korail_bot.services.srt_service import SrtService


def korail_reservation(rsv_id="320260731221946"):
    """A stand-in for a korail2 reservation, with the fields a cancel needs."""
    rsv = Mock()
    rsv.rsv_id = rsv_id
    rsv.journey_no = "001"
    rsv.journey_cnt = "1"
    rsv.rsv_chg_no = "00"
    return rsv


class TestKorailCancels:
    """The workaround, and the honesty around it."""

    def make_service(self, reservations=None, response=None, error=None):
        service = KorailService()
        service._logged_in = True
        service._korail_instance = Mock()
        service._korail_instance.reservations.return_value = reservations or []
        service._korail_instance._device = "AD"
        service._korail_instance._version = "240531001"
        service._korail_instance._key = "test-key"
        session = service._korail_instance._session
        if error is not None:
            session.get.side_effect = error
        else:
            session.get.return_value = Mock(json=Mock(return_value=response or {}))
        service._korail_instance._result_check.return_value = True
        return service

    def test_a_confirmed_cancellation_is_reported_as_one(self):
        service = self.make_service([korail_reservation("222")])

        assert service.cancel_reservation("222") is True

    def test_the_parameters_go_in_the_query_string_not_the_body(self):
        """The whole bug: Korail ignores a GET body, so nothing was cancelled."""
        service = self.make_service([korail_reservation("222")])

        service.cancel_reservation("222")

        call = service._korail_instance._session.get.call_args
        assert "data" not in call.kwargs
        assert call.kwargs["params"]["txtPnrNo"] == "222"

    def test_the_journey_numbers_come_off_the_listed_reservation(self):
        """They are not on the id the caller has, which is why it is looked up."""
        service = self.make_service([korail_reservation("222")])

        service.cancel_reservation("222")

        params = service._korail_instance._session.get.call_args.kwargs["params"]
        assert params["txtJrnySqno"] == "001"
        assert params["txtJrnyCnt"] == "1"
        assert params["hidRsvChgNo"] == "00"

    def test_a_reservation_that_is_not_outstanding_is_not_cancelled(self):
        service = self.make_service([korail_reservation("111")])

        assert service.cancel_reservation("222") is False
        service._korail_instance._session.get.assert_not_called()

    def test_an_empty_list_is_nothing_to_cancel(self):
        service = self.make_service(error=None, reservations=[])
        service._korail_instance.reservations.side_effect = NoResultsError()

        assert service.cancel_reservation("222") is False

    def test_a_refusal_is_not_a_cancellation(self):
        """KorailError is how the client reports one, and it must not escape."""
        service = self.make_service([korail_reservation("222")])
        service._korail_instance._result_check.side_effect = KorailError("안됩니다", "P100")

        assert service.cancel_reservation("222") is False

    def test_a_broken_request_is_not_a_cancellation(self):
        service = self.make_service([korail_reservation("222")], error=OSError("no route"))

        assert service.cancel_reservation("222") is False

    def test_not_being_logged_in_cancels_nothing(self):
        service = KorailService()

        assert service.cancel_reservation("222") is False


class TestSrCancels:
    """SR's client takes a reservation number, so there is nothing to work around."""

    def make_service(self, result=True, error=None):
        service = SrtService()
        service._logged_in = True
        service._srt_instance = Mock()
        if error is not None:
            service._srt_instance.cancel.side_effect = error
        else:
            service._srt_instance.cancel.return_value = result
        return service

    def test_a_confirmed_cancellation_is_reported_as_one(self):
        service = self.make_service()

        assert service.cancel_reservation("990001") is True
        service._srt_instance.cancel.assert_called_once_with("990001")

    def test_a_refusal_is_not_a_cancellation(self):
        service = self.make_service(error=RuntimeError("SRT error"))

        assert service.cancel_reservation("990001") is False

    def test_not_being_logged_in_cancels_nothing(self):
        service = SrtService()

        assert service.cancel_reservation("990001") is False


class TestTheBaseAnswerIsNo:
    """An operator whose client cannot cancel says so rather than pretending."""

    def test_the_default_refuses_and_says_nothing_happened(self):
        assert RailService.cancel_reservation(Mock(spec=RailService), "222") is False
