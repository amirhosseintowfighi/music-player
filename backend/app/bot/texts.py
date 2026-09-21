"""Bot copy in both UI languages. Keys are stable; clients never see these."""

from __future__ import annotations

from typing import Literal

Lang = Literal["fa", "en"]

TEXTS: dict[str, dict[Lang, str]] = {
    "welcome": {
        "fa": (
            "سلام {name}! 🎧\n"
            "کانال‌های موزیک تلگرامت را یک‌جا گوش بده: پلی‌لیست بساز، لایک کن و بین همه‌شان سرچ کن.\n\n"
            "برای افزودن کانال، یوزرنیم یا لینکش را بفرست، یا یک پست از آن کانال را برایم فوروارد کن."  # noqa: E501
        ),
        "en": (
            "Hi {name}! 🎧\n"
            "Listen to your Telegram music channels in one place: build playlists, like tracks "
            "and search across all of them.\n\n"
            "To add a channel, send its username or link, or forward me a post from it."
        ),
    },
    "open_player": {"fa": "🎵 باز کردن پخش‌کننده", "en": "🎵 Open player"},
    "help": {
        "fa": (
            "راهنما:\n"
            "• افزودن کانال عمومی: @username یا لینک t.me بفرست، یا یک پست را فوروارد کن.\n"
            "• کانال خصوصی خودت: من را ادمین کانال کن؛ از آن به بعد آهنگ‌های جدید اضافه می‌شوند.\n"
            "• /lang تغییر زبان"
        ),
        "en": (
            "Help:\n"
            "• Public channel: send @username or a t.me link, or forward a post.\n"
            "• Your private channel: make me an admin; new tracks are added from then on.\n"
            "• /lang change language"
        ),
    },
    "channel_added": {
        "fa": "✅ کانال «{title}» به کتابخانه‌ات اضافه شد. {status}",
        "en": "✅ «{title}» was added to your library. {status}",
    },
    "channel_exists": {
        "fa": "این کانال از قبل در کتابخانه‌ات هست. {status}",
        "en": "This channel is already in your library. {status}",
    },
    "status_pending": {
        "fa": "ایندکس آهنگ‌ها شروع شد؛ پیشرفت را در پخش‌کننده می‌بینی.",
        "en": "Indexing has started; you can follow the progress in the player.",
    },
    "status_ready": {"fa": "آهنگ‌هایش آماده‌اند.", "en": "Its tracks are ready."},
    "private_channel": {
        "fa": (
            "این کانال خصوصی است و قابل ایندکس خودکار نیست.\n"
            "اگر مال خودت است، من را ادمینش کن تا آهنگ‌های جدیدش اضافه شوند. "
            "(آهنگ‌های قدیمی کانال خصوصی را تلگرام به ربات‌ها نشان نمی‌دهد.)"
        ),
        "en": (
            "This is a private channel and can't be indexed automatically.\n"
            "If it's yours, make me an admin and new tracks will be added. "
            "(Telegram doesn't show a private channel's older posts to bots.)"
        ),
    },
    "not_a_channel": {
        "fa": "یوزرنیم یا لینک کانال را تشخیص ندادم. مثال: @channelname یا t.me/channelname",
        "en": "I couldn't recognise a channel. Example: @channelname or t.me/channelname",
    },
    "limit_channels": {
        "fa": "در پلن رایگان حداکثر {limit} کانال می‌توانی اضافه کنی. با پرو نامحدود می‌شود. ⭐",
        "en": "The free plan allows up to {limit} channels. Pro makes it unlimited. ⭐",
    },
    "blocked_channel": {
        "fa": "این کانال در دسترس نیست.",
        "en": "This channel is not available.",
    },
    "banned": {"fa": "دسترسی شما محدود شده است.", "en": "Your access has been restricted."},
    "admin_connected": {
        "fa": "✅ کانال «{title}» وصل شد. آهنگ‌های جدیدش خودکار اضافه می‌شوند.",
        "en": "✅ «{title}» is connected. New tracks will be added automatically.",
    },
    "choose_lang": {"fa": "زبان را انتخاب کن:", "en": "Choose a language:"},
    "lang_set": {"fa": "زبان روی فارسی تنظیم شد.", "en": "Language set to English."},
    "sub_active": {
        "fa": "اشتراک «{plan}» فعال است تا {until}.",
        "en": "Your «{plan}» subscription is active until {until}.",
    },
    "sub_free": {
        "fa": "الان روی پلن رایگان هستی. از داخل اپ می‌توانی پلن بگیری.",
        "en": "You are on the free plan. You can subscribe from inside the app.",
    },
    "pay_thanks": {
        "fa": "✅ پرداخت انجام شد. اشتراک «{plan}» تا {until} فعال است.",
        "en": "✅ Payment received. «{plan}» is active until {until}.",
    },
    "pay_refunded": {
        "fa": "پرداختت برگشت خورد و اشتراک لغو شد.",
        "en": "Your payment was refunded and the subscription was cancelled.",
    },
    "c2c_howto": {
        "fa": (
            "برای پرداخت کارت‌به‌کارت:\n"
            "۱) مبلغ {amount} تومان را به کارت زیر واریز کن:\n"
            "<code>{card}</code>\n"
            "به نام {holder}{bank}\n"
            "۲) همین‌جا عکس رسید را بفرست (کد پیگیری: {ref}).\n\n"
            "بعد از تأیید، اشتراکت فعال می‌شود."
        ),
        "en": (
            "Card transfer:\n"
            "1) Transfer {amount} IRR to:\n"
            "<code>{card}</code>\n"
            "Holder: {holder}{bank}\n"
            "2) Send the receipt photo here (reference: {ref}).\n\n"
            "Your plan is activated once it is approved."
        ),
    },
    "c2c_no_pending": {
        "fa": "الان پرداخت در انتظاری نداری. اول از داخل اپ پلن را انتخاب کن.",
        "en": "You have no pending payment. Pick a plan inside the app first.",
    },
    "c2c_received": {
        "fa": "\U0001f4f8 رسیدت رسید. تا {hours} ساعت آینده بررسی و نتیجه را اعلام می‌کنیم.",
        "en": "\U0001f4f8 Receipt received. We will review it within {hours} hours.",
    },
    "c2c_rejected": {
        "fa": "\u274c رسید پرداختت تأیید نشد. {note}",
        "en": "\u274c Your receipt was not approved. {note}",
    },
    "c2c_expired": {
        "fa": (
            "\u231b\ufe0f درخواست پرداخت کارت‌به‌کارتت منقضی شد. "
            "اگر واریز کرده‌ای با پشتیبانی تماس بگیر."
        ),
        "en": (
            "\u231b\ufe0f Your card transfer request expired. Contact support if you already paid."
        ),
    },
    "expiry_notice": {
        "fa": "\u23f3 اشتراکت {days} روز دیگر تمام می‌شود. برای تمدید وارد اپ شو.",
        "en": "\u23f3 Your subscription ends in {days} days. Open the app to renew.",
    },
    "expired_notice": {
        "fa": "اشتراکت تمام شد و به پلن رایگان برگشتی. هر وقت خواستی تمدید کن.",
        "en": "Your subscription ended and you are back on the free plan.",
    },
    "notify_new_tracks": {
        "fa": "\U0001f3b5 {count} آهنگ تازه در «{channel}» اضافه شد.",
        "en": "\U0001f3b5 {count} new tracks in «{channel}».",
    },
    "notify_digest": {
        "fa": "این هفته {tracks} آهنگ تازه در {channels} کانالت اضافه شد.",
        "en": "{tracks} new tracks landed in {channels} of your channels this week.",
    },
    "notify_discover": {
        "fa": "\u2728 «کشف هفتگی» تازه‌ات آماده است.",
        "en": "\u2728 Your new Discover Weekly is ready.",
    },
    "error": {
        "fa": "مشکلی پیش آمد، کمی بعد دوباره امتحان کن.",
        "en": "Something went wrong, please try again shortly.",
    },
}


def t(key: str, lang: str, **kwargs: object) -> str:
    table = TEXTS[key]
    text = table["en"] if lang == "en" else table["fa"]
    return text.format(**kwargs) if kwargs else text
