"""Transactional email templates (Phase 22) in Vietnamese, English and Japanese.

Each template has a subject, a few paragraphs and an optional call to action. ``render``
fills them with parameters (HTML-escaped in the HTML part) and returns the subject, a
plain-text body and an HTML body; every email carries both. Times are shown in UTC
(``2026-10-02 14:05 UTC``), amounts in VND formatted for the language.

Parameters never include a password or secret. A link may carry a one-time token: it is
rendered at send time from the encrypted outbox row and erased afterwards
(``app/mailer.py``).
"""
from datetime import datetime, timezone
import html
from typing import Any

BRAND = "ReelForge Studio"
LOCALES = ("vi", "en", "ja")
DEFAULT_LOCALE = "vi"

ROLES = {
    "vi": {"owner": "Chủ sở hữu", "admin": "Quản trị", "editor": "Biên tập", "viewer": "Người xem"},
    "en": {"owner": "Owner", "admin": "Admin", "editor": "Editor", "viewer": "Viewer"},
    "ja": {"owner": "オーナー", "admin": "管理者", "editor": "編集者", "viewer": "閲覧者"},
}
FOOTER = {
    "vi": "Email này được gửi tự động từ {brand}. Nếu bạn không thực hiện thao tác này, hãy bỏ qua email hoặc liên hệ hỗ trợ.",
    "en": "This email was sent automatically by {brand}. If you did not do this, ignore it or contact support.",
    "ja": "このメールは {brand} から自動送信されています。心当たりがない場合は、このメールを無視するか、サポートにお問い合わせください。",
}
GREETING = {"vi": "Xin chào,", "en": "Hello,", "ja": "こんにちは。"}

# name: {locale: (subject, [paragraphs], action label or None)}
TEMPLATES: dict[str, dict[str, tuple[str, list[str], str | None]]] = {
    "verify_email": {
        "vi": ("Xác minh email của bạn – {brand}",
               ["Hãy xác minh địa chỉ email để hoàn tất tài khoản {brand}.",
                "Liên kết có hiệu lực trong {hours} giờ."], "Xác minh email"),
        "en": ("Verify your email – {brand}",
               ["Please verify your email address to finish setting up your {brand} account.",
                "The link is valid for {hours} hours."], "Verify email"),
        "ja": ("メールアドレスの確認 – {brand}",
               ["{brand} アカウントの設定を完了するため、メールアドレスを確認してください。",
                "このリンクの有効期限は {hours} 時間です。"], "メールアドレスを確認"),
    },
    "welcome": {
        "vi": ("Chào mừng đến với {brand}",
               ["Tài khoản của bạn đã sẵn sàng.",
                "Bắt đầu bằng cách tạo dự án đầu tiên, chọn một mẫu workflow và tạo video."], "Mở {brand}"),
        "en": ("Welcome to {brand}",
               ["Your account is ready.",
                "Start by creating your first project, choosing a workflow template and generating a video."],
               "Open {brand}"),
        "ja": ("{brand} へようこそ",
               ["アカウントの準備ができました。",
                "最初のプロジェクトを作成し、ワークフローのテンプレートを選んで動画を生成しましょう。"], "{brand} を開く"),
    },
    "account_created": {
        "vi": ("Tài khoản {brand} của bạn",
               ["Quản trị viên đã tạo cho bạn một tài khoản {brand} với email này.",
                "Để đặt mật khẩu của riêng bạn, mở trang đăng nhập và chọn “Quên mật khẩu”."], "Mở {brand}"),
        "en": ("Your {brand} account",
               ["An administrator created a {brand} account for this email address.",
                "To choose your own password, open the sign-in page and use “Forgot password”."], "Open {brand}"),
        "ja": ("{brand} のアカウント",
               ["管理者がこのメールアドレスで {brand} のアカウントを作成しました。",
                "ご自身のパスワードを設定するには、サインイン画面の「パスワードをお忘れですか」を使用してください。"],
               "{brand} を開く"),
    },
    "password_reset": {
        "vi": ("Đặt lại mật khẩu – {brand}",
               ["Có yêu cầu đặt lại mật khẩu cho tài khoản này.",
                "Liên kết có hiệu lực trong {minutes} phút và chỉ dùng được một lần.",
                "Nếu bạn không yêu cầu, hãy bỏ qua email này; mật khẩu của bạn không thay đổi."], "Đặt lại mật khẩu"),
        "en": ("Reset your password – {brand}",
               ["Someone asked to reset the password of this account.",
                "The link is valid for {minutes} minutes and works once.",
                "If it was not you, ignore this email; your password stays the same."], "Reset password"),
        "ja": ("パスワードの再設定 – {brand}",
               ["このアカウントのパスワード再設定がリクエストされました。",
                "リンクの有効期限は {minutes} 分で、一度だけ使用できます。",
                "心当たりがない場合はこのメールを無視してください。パスワードは変更されません。"], "パスワードを再設定"),
    },
    "password_changed": {
        "vi": ("Mật khẩu của bạn đã được thay đổi",
               ["Mật khẩu tài khoản {brand} của bạn đã được thay đổi lúc {time}.",
                "Các phiên đăng nhập khác đã được đăng xuất.",
                "Nếu không phải bạn, hãy dùng “Quên mật khẩu” ngay và liên hệ hỗ trợ."], "Đăng nhập"),
        "en": ("Your password was changed",
               ["The password of your {brand} account was changed at {time}.",
                "Your other sessions were signed out.",
                "If this was not you, use “Forgot password” right away and contact support."], "Sign in"),
        "ja": ("パスワードが変更されました",
               ["{brand} アカウントのパスワードが {time} に変更されました。",
                "他のセッションはサインアウトされました。",
                "心当たりがない場合は、すぐに「パスワードをお忘れですか」から再設定し、サポートにご連絡ください。"],
               "サインイン"),
    },
    "support_reply": {
        "vi": ("Phản hồi mới cho yêu cầu hỗ trợ: {subject}",
               ["Đội hỗ trợ đã trả lời yêu cầu “{subject}”.", "Mở yêu cầu để xem và trả lời."], "Xem phản hồi"),
        "en": ("New reply to your support request: {subject}",
               ["Support replied to “{subject}”.", "Open the request to read and answer it."], "View reply"),
        "ja": ("お問い合わせに返信がありました: {subject}",
               ["「{subject}」にサポートから返信がありました。", "お問い合わせを開いて内容を確認し、返信してください。"],
               "返信を見る"),
    },
    "payment_succeeded": {
        "vi": ("Thanh toán thành công – gói {plan}",
               ["Chúng tôi đã nhận {amount} cho gói {plan} (mã đơn {reference}).",
                "Gói có hiệu lực đến {ends}. Đã cộng {credits} credits vào studio.",
                "Gói không tự động gia hạn; bạn có thể gia hạn bất cứ lúc nào trong Gói & credits."],
               "Mở Gói & credits"),
        "en": ("Payment received – {plan} plan",
               ["We received {amount} for the {plan} plan (order {reference}).",
                "The plan is active until {ends}. {credits} credits were added to your studio.",
                "Plans do not renew automatically; renew any time under Plans & credits."], "Open Plans & credits"),
        "ja": ("お支払いを受け付けました – {plan} プラン",
               ["{plan} プランのお支払い {amount} を受け付けました（注文番号 {reference}）。",
                "プランは {ends} まで有効です。スタジオに {credits} クレジットを追加しました。",
                "プランは自動更新されません。「プランとクレジット」からいつでも更新できます。"],
               "プランとクレジットを開く"),
    },
    "payment_failed": {
        "vi": ("Chưa xác nhận được thanh toán – gói {plan}",
               ["Thanh toán {amount} cho gói {plan} (mã đơn {reference}) chưa được xác nhận.",
                "Lý do: {reason}",
                "Nếu tiền đã bị trừ, hãy liên hệ hỗ trợ kèm biên lai."], "Mở Gói & credits"),
        "en": ("Payment not confirmed – {plan} plan",
               ["The payment of {amount} for the {plan} plan (order {reference}) was not confirmed.",
                "Reason: {reason}",
                "If you were charged, contact support with the receipt."], "Open Plans & credits"),
        "ja": ("お支払いを確認できませんでした – {plan} プラン",
               ["{plan} プランのお支払い {amount}（注文番号 {reference}）を確認できませんでした。",
                "理由: {reason}",
                "引き落とし済みの場合は、控えを添えてサポートにご連絡ください。"], "プランとクレジットを開く"),
    },
    "subscription_activated": {
        "vi": ("Gói {plan} đã được kích hoạt",
               ["Studio “{workspace}” của bạn đang dùng gói {plan}.", "Hiệu lực đến: {ends}."], "Mở {brand}"),
        "en": ("{plan} plan activated",
               ["Your studio “{workspace}” is now on the {plan} plan.", "Active until: {ends}."], "Open {brand}"),
        "ja": ("{plan} プランが有効になりました",
               ["スタジオ「{workspace}」は {plan} プランになりました。", "有効期限: {ends}"], "{brand} を開く"),
    },
    "security_locked": {
        "vi": ("Cảnh báo bảo mật: tạm khoá đăng nhập",
               ["Có nhiều lần đăng nhập sai vào tài khoản {brand} của bạn. Việc đăng nhập từ địa chỉ {ip} đã bị tạm khoá trong 15 phút ({time}).",
                "Nếu đó không phải bạn, hãy đổi mật khẩu và bật xác thực hai lớp."], None),
        "en": ("Security alert: sign-in temporarily blocked",
               ["There were several failed sign-ins to your {brand} account. Sign-in from {ip} is blocked for 15 minutes ({time}).",
                "If this was not you, change your password and turn on two-factor authentication."], None),
        "ja": ("セキュリティ通知: サインインを一時的にブロックしました",
               ["{brand} アカウントへのサインインに複数回失敗しました。{ip} からのサインインを 15 分間ブロックしています（{time}）。",
                "心当たりがない場合は、パスワードを変更し、2 段階認証を有効にしてください。"], None),
    },
    "security_2fa_enabled": {
        "vi": ("Đã bật xác thực hai lớp",
               ["Xác thực hai lớp đã được bật cho tài khoản {brand} của bạn lúc {time}.",
                "Hãy cất giữ mã khôi phục ở nơi an toàn."], None),
        "en": ("Two-factor authentication turned on",
               ["Two-factor authentication was turned on for your {brand} account at {time}.",
                "Keep your recovery codes somewhere safe."], None),
        "ja": ("2 段階認証を有効にしました",
               ["{time} に {brand} アカウントの 2 段階認証が有効になりました。",
                "リカバリーコードは安全な場所に保管してください。"], None),
    },
    "security_2fa_disabled": {
        "vi": ("Đã tắt xác thực hai lớp",
               ["Xác thực hai lớp đã bị tắt cho tài khoản {brand} của bạn lúc {time}.",
                "Nếu không phải bạn, hãy đổi mật khẩu ngay và liên hệ hỗ trợ."], None),
        "en": ("Two-factor authentication turned off",
               ["Two-factor authentication was turned off for your {brand} account at {time}.",
                "If this was not you, change your password now and contact support."], None),
        "ja": ("2 段階認証を無効にしました",
               ["{time} に {brand} アカウントの 2 段階認証が無効になりました。",
                "心当たりがない場合は、すぐにパスワードを変更し、サポートにご連絡ください。"], None),
    },
    "security_recovery_used": {
        "vi": ("Một mã khôi phục vừa được dùng",
               ["Một mã khôi phục đã được dùng để đăng nhập vào tài khoản {brand} của bạn lúc {time} (địa chỉ {ip}).",
                "Còn lại {remaining} mã. Tạo mã mới trong Cài đặt → Bảo mật nếu sắp hết."], None),
        "en": ("A recovery code was used",
               ["A recovery code was used to sign in to your {brand} account at {time} (from {ip}).",
                "{remaining} codes remain. Create new ones in Settings → Security when they run low."], None),
        "ja": ("リカバリーコードが使用されました",
               ["{time} に {brand} アカウントへのサインインでリカバリーコードが使用されました（{ip}）。",
                "残りは {remaining} 個です。少なくなったら「設定 → セキュリティ」で再発行してください。"], None),
    },
    "member_invite": {
        "vi": ("Lời mời tham gia studio {workspace}",
               ["{inviter} mời bạn tham gia studio “{workspace}” trên {brand} với vai trò {role}.",
                "Lời mời có hiệu lực trong {days} ngày."], "Chấp nhận lời mời"),
        "en": ("Invitation to join {workspace}",
               ["{inviter} invited you to the “{workspace}” studio on {brand} as {role}.",
                "The invitation is valid for {days} days."], "Accept invitation"),
        "ja": ("{workspace} への招待",
               ["{inviter} さんが {brand} のスタジオ「{workspace}」に{role}として招待しました。",
                "招待の有効期限は {days} 日です。"], "招待を承認"),
    },
    "test": {
        "vi": ("Email thử nghiệm từ {brand}",
               ["Đây là email thử nghiệm gửi lúc {time}.", "Nếu bạn nhận được email này, cấu hình email đang hoạt động."],
               None),
        "en": ("Test email from {brand}",
               ["This is a test email sent at {time}.", "If you received it, email delivery works."], None),
        "ja": ("{brand} からのテストメール",
               ["これは {time} に送信したテストメールです。", "このメールが届いていれば、メール送信は正常に動作しています。"], None),
    },
}
NAMES = tuple(TEMPLATES)


def locale_of(value: str | None) -> str:
    return value if value in LOCALES else DEFAULT_LOCALE


def format_time(value: Any) -> str:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return value
    if isinstance(value, datetime):
        value = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return "—"


def format_vnd(amount: Any, locale: str) -> str:
    try:
        number = int(amount)
    except (TypeError, ValueError):
        return str(amount or "—")
    grouped = f"{number:,}"
    if locale == "vi":
        return grouped.replace(",", ".") + " ₫"
    if locale == "ja":
        return grouped + " VND"
    return grouped + " VND"


def _values(locale: str, params: dict[str, Any]) -> dict[str, str]:
    values = {"brand": BRAND}
    for key, value in params.items():
        if key in ("time", "ends") or key.endswith("_at"):
            values[key] = format_time(value) if value else "—"
        elif key == "amount":
            values[key] = format_vnd(value, locale)
        elif key == "role":
            values[key] = ROLES[locale].get(str(value), str(value))
        else:
            values[key] = "—" if value is None or value == "" else str(value)
    return values


class _Safe(dict):
    def __missing__(self, key):
        return "—"


def render(name: str, locale: str | None, params: dict[str, Any]) -> tuple[str, str, str]:
    """(subject, text, html) of template ``name``; ``params["link"]`` becomes the call to action."""
    locale = locale_of(locale)
    subject, paragraphs, action = TEMPLATES[name][locale]
    values = _Safe(_values(locale, params))
    escaped = _Safe({key: html.escape(value) for key, value in values.items()})
    link = str(params.get("link") or "")
    text_parts = [GREETING[locale], "", *[line.format_map(values) for line in paragraphs]]
    if action and link:
        text_parts += ["", f"{action.format_map(values)}: {link}"]
    text_parts += ["", "—", FOOTER[locale].format_map(values)]
    body = "\n".join(text_parts) + "\n"

    html_paragraphs = "".join(f'<p style="margin:0 0 14px">{line.format_map(escaped)}</p>' for line in paragraphs)
    button = ""
    if action and link:
        safe_link = html.escape(link, quote=True)
        button = (f'<p style="margin:22px 0"><a href="{safe_link}" style="background:#6d5dfc;color:#ffffff;'
                  f'padding:11px 18px;border-radius:8px;text-decoration:none;font-weight:600;display:inline-block">'
                  f'{html.escape(action.format_map(values))}</a></p>'
                  f'<p style="margin:0 0 14px;font-size:12px;color:#666666;word-break:break-all">{safe_link}</p>')
    html_body = (
        f'<!doctype html><html lang="{locale}"><body style="margin:0;padding:24px;background:#f4f4f6;'
        f'font-family:Segoe UI,Helvetica,Arial,sans-serif;color:#1d1d24;font-size:15px;line-height:1.55">'
        f'<div style="max-width:560px;margin:0 auto;background:#ffffff;border-radius:12px;padding:28px">'
        f'<p style="margin:0 0 18px;font-weight:700;font-size:17px">{html.escape(BRAND)}</p>'
        f'<p style="margin:0 0 14px">{html.escape(GREETING[locale])}</p>{html_paragraphs}{button}'
        f'<hr style="border:none;border-top:1px solid #e6e6ea;margin:24px 0 14px">'
        f'<p style="margin:0;font-size:12px;color:#777777">{html.escape(FOOTER[locale].format_map(values))}</p>'
        f'</div></body></html>')
    return subject.format_map(values), body, html_body
