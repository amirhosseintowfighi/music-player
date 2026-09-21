"""Stars and card-to-card through the real webhook, with a recording Bot API session."""

from __future__ import annotations

import time
from typing import Any

import httpx
import pytest
from aiogram.methods import AnswerPreCheckoutQuery, SendMessage, SendPhoto
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Payment, Subscription, User
from app.services import plans, subscriptions
from tests.integration.test_bot import HOOK, USER, RecordingSession, private_message
from tests.integration.test_payments import gateway, make_admin, make_user


@pytest.fixture
def tg(client: httpx.AsyncClient) -> RecordingSession:
    session = RecordingSession()
    client.app.state.bot.session = session  # type: ignore[attr-defined]
    return session


ADMIN_CHAT = -1009999
ADMIN_TG = 9001  # the same tg id as USER: the reviewer presses the button themselves


@pytest.fixture(autouse=True)
async def _reset_providers(session: AsyncSession) -> Any:
    yield
    await session.rollback()
    session.expire_all()
    await session.execute(text("UPDATE payment_providers SET is_enabled = (code = 'stars')"))
    await session.commit()
    plans.clear_caches()


async def feed(client: httpx.AsyncClient, update: dict[str, Any]) -> httpx.Response:
    return await client.post("/tg/webhook", json=update, headers=HOOK)


async def pending_stars_payment(session: AsyncSession, amount: int = 150) -> Payment:
    user = await make_user(session, tg_id=USER["id"])
    payment = Payment(
        user_id=user.id,
        provider="stars",
        plan_code="pro_monthly",
        amount=amount,
        currency="XTR",
        status="pending",
    )
    session.add(payment)
    await session.flush()
    await session.refresh(payment)
    await session.commit()
    return payment


async def test_pre_checkout_is_answered_and_validated(
    client: httpx.AsyncClient, session: AsyncSession, tg: RecordingSession
) -> None:
    payment = await pending_stars_payment(session)
    update = {
        "update_id": 91001,
        "pre_checkout_query": {
            "id": "pcq-1",
            "from": USER,
            "currency": "XTR",
            "total_amount": 150,
            "invoice_payload": str(payment.public_id),
        },
    }
    assert (await feed(client, update)).json() == {"ok": True}
    answer = tg.calls[-1]
    assert isinstance(answer, AnswerPreCheckoutQuery)
    assert answer.ok is True


async def test_pre_checkout_rejects_a_tampered_amount(
    client: httpx.AsyncClient, session: AsyncSession, tg: RecordingSession
) -> None:
    payment = await pending_stars_payment(session)
    update = {
        "update_id": 91002,
        "pre_checkout_query": {
            "id": "pcq-2",
            "from": USER,
            "currency": "XTR",
            "total_amount": 1,  # not what we invoiced
            "invoice_payload": str(payment.public_id),
        },
    }
    await feed(client, update)
    answer = tg.calls[-1]
    assert isinstance(answer, AnswerPreCheckoutQuery)
    assert answer.ok is False


async def test_pre_checkout_rejects_an_unknown_payload(
    client: httpx.AsyncClient, session: AsyncSession, tg: RecordingSession
) -> None:
    await make_user(session, tg_id=USER["id"])
    await session.commit()
    update = {
        "update_id": 91003,
        "pre_checkout_query": {
            "id": "pcq-3",
            "from": USER,
            "currency": "XTR",
            "total_amount": 150,
            "invoice_payload": "11111111-1111-1111-1111-111111111111",
        },
    }
    await feed(client, update)
    assert tg.calls[-1].ok is False  # type: ignore[attr-defined]


async def test_successful_payment_activates_and_thanks(
    client: httpx.AsyncClient, session: AsyncSession, tg: RecordingSession
) -> None:
    payment = await pending_stars_payment(session)
    payment_id, public_id = payment.id, str(payment.public_id)
    update = private_message(
        successful_payment={
            "currency": "XTR",
            "total_amount": 150,
            "invoice_payload": public_id,
            "telegram_payment_charge_id": "charge-1",
            "provider_payment_charge_id": "p-1",
        }
    )
    await feed(client, update)

    await session.rollback()
    session.expire_all()
    stored = await session.get(Payment, payment_id)
    assert stored is not None
    assert stored.status == "paid"
    assert stored.provider_ref == "charge-1"
    live = (await session.scalars(select(Subscription))).one()
    assert live.status == "active"
    assert any("فعال است" in message for message in tg.texts())
    plans.clear_caches()


async def test_successful_payment_twice_activates_once(
    client: httpx.AsyncClient, session: AsyncSession, tg: RecordingSession
) -> None:
    payment = await pending_stars_payment(session)
    payment_id = payment.id
    public_id = str(payment.public_id)
    body = {
        "currency": "XTR",
        "total_amount": 150,
        "invoice_payload": public_id,
        "telegram_payment_charge_id": "charge-2",
        "provider_payment_charge_id": "p-2",
    }
    await feed(client, private_message(successful_payment=body))
    await feed(client, private_message(successful_payment=body))

    await session.rollback()
    session.expire_all()
    stored = await session.get(Payment, payment_id)
    assert stored is not None
    assert stored.status == "paid"
    assert len((await session.scalars(select(Subscription))).all()) == 1
    plans.clear_caches()


async def test_refunded_payment_cancels_the_subscription(
    client: httpx.AsyncClient, session: AsyncSession, tg: RecordingSession
) -> None:
    payment = await pending_stars_payment(session)
    payment_id = payment.id
    public_id = str(payment.public_id)
    body = {
        "currency": "XTR",
        "total_amount": 150,
        "invoice_payload": public_id,
        "telegram_payment_charge_id": "charge-3",
        "provider_payment_charge_id": "p-3",
    }
    await feed(client, private_message(successful_payment=body))
    await feed(
        client,
        private_message(
            refunded_payment={
                "currency": "XTR",
                "total_amount": 150,
                "invoice_payload": public_id,
                "telegram_payment_charge_id": "charge-3",
            }
        ),
    )
    await session.rollback()
    session.expire_all()
    stored = await session.get(Payment, payment_id)
    assert stored is not None
    assert stored.status == "refunded"
    user = (await session.scalars(select(User).where(User.tg_id == USER["id"]))).one()
    assert user.plan_code == "free"
    plans.clear_caches()


async def test_sub_command_reports_the_plan(
    client: httpx.AsyncClient, session: AsyncSession, tg: RecordingSession
) -> None:
    user = await make_user(session, tg_id=USER["id"])
    await session.commit()
    await feed(
        client,
        private_message(text="/sub", entities=[{"offset": 0, "length": 4, "type": "bot_command"}]),
    )
    assert any("رایگان" in message for message in tg.texts())

    await subscriptions.activate(session, user.id, "pro_monthly", source="admin")
    await session.commit()
    await feed(
        client,
        private_message(text="/sub", entities=[{"offset": 0, "length": 4, "type": "bot_command"}]),
    )
    assert any("پرو ماهانه" in message for message in tg.texts())
    plans.clear_caches()


# ── card-to-card ──────────────────────────────────────────────────────────────


def photo_message(file_id: str = "AgACPhoto1") -> dict[str, Any]:
    return private_message(
        photo=[
            {
                "file_id": file_id,
                "file_unique_id": "u1",
                "width": 800,
                "height": 1000,
                "file_size": 12345,
            }
        ]
    )


async def start_card_payment(session: AsyncSession, settings: Any) -> int:
    await session.execute(text("UPDATE payment_providers SET is_enabled = true"))
    user = await make_user(session, tg_id=USER["id"])
    async with gateway({}) as http:
        checkout = await subscriptions.start_checkout(
            session, http, settings, user, "pro_monthly", "card2card"
        )
    await session.commit()
    return checkout.payment_id


async def test_receipt_photo_without_a_pending_payment(
    client: httpx.AsyncClient, session: AsyncSession, tg: RecordingSession
) -> None:
    await make_user(session, tg_id=USER["id"])
    await session.commit()
    await feed(client, photo_message())
    assert any("در انتظاری نداری" in message for message in tg.texts())


async def test_receipt_photo_is_stored_and_sent_for_review(
    client: httpx.AsyncClient, session: AsyncSession, tg: RecordingSession, app_state: Any
) -> None:
    payment_id = await start_card_payment(session, app_state.settings)
    await feed(client, photo_message())

    await session.rollback()
    session.expire_all()
    stored = await session.get(Payment, payment_id)
    assert stored is not None
    assert stored.receipt_file_id == "AgACPhoto1"
    assert stored.status == "pending_review"
    sent = [call for call in tg.calls if isinstance(call, SendPhoto)]
    assert len(sent) == 1
    assert sent[0].chat_id == ADMIN_CHAT
    assert sent[0].caption is not None
    assert f"#{payment_id}" in sent[0].caption


async def test_only_an_admin_can_approve(
    client: httpx.AsyncClient, session: AsyncSession, tg: RecordingSession, app_state: Any
) -> None:
    payment_id = await start_card_payment(session, app_state.settings)
    update = {
        "update_id": 92001,
        "callback_query": {
            "id": "cb-1",
            "from": USER,
            "chat_instance": "ci",
            "data": f"pay:ok:{payment_id}",
        },
    }
    await feed(client, update)
    await session.rollback()
    session.expire_all()
    stored = await session.get(Payment, payment_id)
    assert stored is not None
    assert stored.status == "pending_review"  # unchanged


async def test_admin_approval_through_the_button(
    client: httpx.AsyncClient, session: AsyncSession, tg: RecordingSession, app_state: Any
) -> None:
    payment_id = await start_card_payment(session, app_state.settings)
    await make_admin(session)
    await session.execute(text("UPDATE admin_users SET tg_id = :t").bindparams(t=ADMIN_TG))
    await session.commit()

    update = {
        "update_id": 92002,
        "callback_query": {
            "id": "cb-2",
            "from": USER,
            "chat_instance": "ci",
            "data": f"pay:ok:{payment_id}",
            "message": {
                "message_id": 5,
                "date": int(time.time()),
                "chat": {"id": ADMIN_CHAT, "type": "group"},
                "caption": "card-to-card",
            },
        },
    }
    await feed(client, update)

    await session.rollback()
    session.expire_all()
    stored = await session.get(Payment, payment_id)
    assert stored is not None
    assert stored.status == "paid"
    live = (await session.scalars(select(Subscription))).one()
    assert live.status == "active"
    notices = [call for call in tg.calls if isinstance(call, SendMessage)]
    assert any("فعال است" in (call.text or "") for call in notices)
    plans.clear_caches()


async def test_admin_rejection_through_the_button(
    client: httpx.AsyncClient, session: AsyncSession, tg: RecordingSession, app_state: Any
) -> None:
    payment_id = await start_card_payment(session, app_state.settings)
    await make_admin(session)
    await session.execute(text("UPDATE admin_users SET tg_id = :t").bindparams(t=ADMIN_TG))
    await session.commit()

    update = {
        "update_id": 92003,
        "callback_query": {
            "id": "cb-3",
            "from": USER,
            "chat_instance": "ci",
            "data": f"pay:no:{payment_id}",
            "message": {
                "message_id": 6,
                "date": int(time.time()),
                "chat": {"id": ADMIN_CHAT, "type": "group"},
                "caption": "card-to-card",
            },
        },
    }
    await feed(client, update)

    await session.rollback()
    session.expire_all()
    stored = await session.get(Payment, payment_id)
    assert stored is not None
    assert stored.status == "rejected"
    assert (await session.scalars(select(Subscription))).first() is None
