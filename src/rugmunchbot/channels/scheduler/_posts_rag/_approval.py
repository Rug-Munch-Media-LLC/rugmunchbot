"""Main-channel human-in-the-loop approval gate.

When ``RMI_MAIN_APPROVAL_GATE`` is enabled, main-channel drafts
(@cryptorugmuncher) are NOT posted directly. Instead each draft is sent to
@rmi_admin_bot with Approve / Reject / Edit inline buttons:

* **Approve** → the draft is posted to the main channel (and mirrored to the
  feed when ``mirror: true``, exactly like the direct path).
* **Reject** → the draft is logged and discarded (its dedupe mark is kept so
  it is never re-requested every tick).
* **Edit** → the operator replies to the approval message with corrected text;
  the draft is updated and re-sent for approval.
* **No response within ``RMI_MAIN_APPROVAL_TIMEOUT_S``** → the draft
  AUTO-POSTS (fail-open — the gate shapes rhythm, never blocks content).

The gate is opt-in (default OFF). When disabled, ``post_rag_drafts`` posts
directly exactly as before. When enabled but the admin chat id is unset, the
gate degrades to fail-open (posts directly) rather than dropping content.

State is in-memory by design: a restart clears pending approvals (the drafts
were already marked seen, so they are not re-requested — acceptable for an
opt-in operator-facing gate).
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode

import rugmunchbot.channels.scheduler._posts_rag as _pkg

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import ContextTypes

#: Prefix that marks an approval message so the reply-based Edit flow can
#: recognise a reply to one of our approval requests.
_APPROVAL_MARKER = "\U0001f9fe MAIN POST \u2014 APPROVAL REQUIRED"

#: Callback-data prefixes (dispatched by ``handlers.callback``).
CB_APPROVE = "main_approve_"
CB_REJECT = "main_reject_"
CB_EDIT = "main_edit_"


@dataclass
class _PendingApproval:
    """One in-flight main-channel approval request."""

    approval_id: str
    chat_id: str
    text: str
    item: dict[str, Any]
    dedupe_key: str
    content_type: str | None
    deadline: float  # time.monotonic() when the auto-post fires
    admin_message_id: int | None = None
    editing: bool = False


#: approval_id → pending request. In-memory by design (see module docstring).
_pending: dict[str, _PendingApproval] = {}


def _approval_id(dedupe_key: str) -> str:
    """Short stable id for a draft (callback_data is capped at 64 bytes).

    sha256 (not md5) so the security linter stays quiet; this is a
    non-cryptographic stable id, not a secret hash.
    """
    return hashlib.sha256(dedupe_key.encode()).hexdigest()[:16]


def _approval_markup(approval_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("\u2705 Approve", callback_data=f"{CB_APPROVE}{approval_id}"),
                InlineKeyboardButton("\u274c Reject", callback_data=f"{CB_REJECT}{approval_id}"),
                InlineKeyboardButton("\u270f\ufe0f Edit", callback_data=f"{CB_EDIT}{approval_id}"),
            ]
        ]
    )


def _approval_text(text: str) -> str:
    """Wrap a draft in the approval header so replies can be recognised."""
    return f"{_APPROVAL_MARKER}\n\n{text}"


def _post_main_draft(
    ctx: ContextTypes.DEFAULT_TYPE,
    chat_id: str,
    text: str,
    item: dict[str, Any],
    dedupe_key: str,
    content_type: str | None,
) -> Any:
    """Post one main draft (photo-aware) + mirror to the feed. Returns the
    ``_post_photo_aware`` result (bool). Shared by the direct path and the
    approval gate's approve/auto-post callbacks so both behave identically."""
    from rugmunchbot.support.image_policy import ensure_post_image

    return _pkg._post_photo_aware(
        ctx.bot,
        chat_id,
        ensure_post_image(_pkg._rag_post_with_image(text, item)),
        _pkg._safe_send,
        content_type=content_type,
    )


async def _mirror_if_needed(ctx: ContextTypes.DEFAULT_TYPE, item: dict[str, Any]) -> None:
    """Mirror a posted main draft to feed.rugmunch.io (W30 contract)."""
    if not item.get("mirror"):
        return
    slug = _pkg._mirror_to_feed(item)
    if slug:
        _pkg._note_mirror_success()
        _pkg.logger.info("rag_post_mirrored_to_feed slug=%s", slug)
    else:
        await _pkg._alert_mirror_failure(ctx.bot, str(item.get("story_ref") or "")[:120])


async def _request_main_approval(
    ctx: ContextTypes.DEFAULT_TYPE,
    chat_id: str,
    text: str,
    item: dict[str, Any],
    dedupe_key: str,
    content_type: str | None,
) -> bool:
    """Send a main draft to @rmi_admin_bot for approval. Returns True when the
    approval request was sent (the draft is now pending). Fail-open: if the
    admin chat id is unset or the send fails, returns False so the caller
    falls back to posting directly (never drop content)."""
    admin_chat_id = _pkg.RMI_ADMIN_CHAT_ID
    if not admin_chat_id:
        _pkg.logger.warning(
            "main_approval:no_admin_chat_id failing_open ref=%s",
            str(item.get("story_ref") or "")[:60],
        )
        return False

    approval_id = _approval_id(dedupe_key)
    pending = _PendingApproval(
        approval_id=approval_id,
        chat_id=chat_id,
        text=text,
        item=item,
        dedupe_key=dedupe_key,
        content_type=content_type,
        deadline=time.monotonic() + _pkg.RMI_MAIN_APPROVAL_TIMEOUT_S,
    )
    try:
        sent = await ctx.bot.send_message(
            chat_id=admin_chat_id,
            text=_approval_text(text),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            reply_markup=_approval_markup(approval_id),
        )
    except Exception as exc:  # fail-open: never block content on a send error
        _pkg.logger.warning(
            "main_approval:send_failed failing_open ref=%s err=%s",
            str(item.get("story_ref") or "")[:60],
            str(exc)[:120],
        )
        return False

    pending.admin_message_id = getattr(sent, "message_id", None)
    _pending[approval_id] = pending
    _pkg.logger.info(
        "main_approval:requested ref=%s approval_id=%s timeout=%ss",
        str(item.get("story_ref") or "")[:60],
        approval_id,
        _pkg.RMI_MAIN_APPROVAL_TIMEOUT_S,
    )

    # Schedule the fail-open auto-post. Guarded for queues that predate PTB
    # run_once (and test fakes) — if there is no job queue we still keep the
    # draft pending; the next tick's dedupe prevents a re-request and the
    # operator can still approve/reject.
    job_queue = getattr(ctx, "job_queue", None)
    if job_queue is not None and hasattr(job_queue, "run_once"):
        try:
            job_queue.run_once(
                _auto_post_main_approval,
                when=_pkg.RMI_MAIN_APPROVAL_TIMEOUT_S,
                data=approval_id,
                name=f"main_approval_{approval_id}",
            )
        except Exception as exc:  # pragma: no cover - defensive
            _pkg.logger.warning("main_approval:schedule_failed err=%s", str(exc)[:120])
    return True


async def _auto_post_main_approval(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Fail-open auto-post: fire when the operator has not responded in time."""
    approval_id = getattr(ctx.job, "data", None)
    if not approval_id:
        return
    pending = _pending.get(approval_id)
    if pending is None:
        return  # already decided (approved/rejected) — nothing to do
    _pending.pop(approval_id, None)
    _pkg.logger.info(
        "main_approval:auto_post ref=%s approval_id=%s",
        str(pending.item.get("story_ref") or "")[:60],
        approval_id,
    )
    ok = await _post_main_draft(
        ctx,
        pending.chat_id,
        pending.text,
        pending.item,
        pending.dedupe_key,
        pending.content_type,
    )
    if ok:
        await _mirror_if_needed(ctx, pending.item)
    else:
        # Post failed — release the dedupe mark so the next tick retries.
        _pkg._release("rag_main", pending.dedupe_key)


async def _approve_main_approval(
    update: Update, ctx: ContextTypes.DEFAULT_TYPE, approval_id: str
) -> None:
    query = update.callback_query
    pending = _pending.pop(approval_id, None)
    if pending is None:
        if query is not None:
            await query.answer("Already handled (posted or expired).", show_alert=True)
        return
    _pkg.logger.info(
        "main_approval:approved ref=%s approval_id=%s",
        str(pending.item.get("story_ref") or "")[:60],
        approval_id,
    )
    ok = await _post_main_draft(
        ctx,
        pending.chat_id,
        pending.text,
        pending.item,
        pending.dedupe_key,
        pending.content_type,
    )
    if ok:
        await _mirror_if_needed(ctx, pending.item)
    else:
        _pkg._release("rag_main", pending.dedupe_key)
    if query is not None:
        await query.answer("Approved \u2014 posted to main.")
        await _mark_admin_message(ctx, pending, "\u2705 Approved \u2014 posted to main.")


async def _reject_main_approval(
    update: Update, ctx: ContextTypes.DEFAULT_TYPE, approval_id: str
) -> None:
    query = update.callback_query
    pending = _pending.pop(approval_id, None)
    if pending is None:
        if query is not None:
            await query.answer("Already handled (posted or expired).", show_alert=True)
        return
    # Discard: keep the dedupe mark so the draft is never re-requested.
    _pkg.logger.info(
        "main_approval:rejected ref=%s approval_id=%s",
        str(pending.item.get("story_ref") or "")[:60],
        approval_id,
    )
    if query is not None:
        await query.answer("Rejected \u2014 discarded.")
        await _mark_admin_message(ctx, pending, "\u274c Rejected \u2014 discarded.")


async def _edit_main_approval(
    update: Update, ctx: ContextTypes.DEFAULT_TYPE, approval_id: str
) -> None:
    query = update.callback_query
    pending = _pending.get(approval_id)
    if pending is None:
        if query is not None:
            await query.answer("Already handled (posted or expired).", show_alert=True)
        return
    pending.editing = True
    _pkg.logger.info(
        "main_approval:edit_requested ref=%s approval_id=%s",
        str(pending.item.get("story_ref") or "")[:60],
        approval_id,
    )
    if query is not None:
        await query.answer("Reply with the corrected text.")
        await _mark_admin_message(
            ctx,
            pending,
            "\u270f\ufe0f Edit mode \u2014 reply to this message with the corrected text.",
        )


async def _mark_admin_message(
    ctx: ContextTypes.DEFAULT_TYPE, pending: _PendingApproval, note: str
) -> None:
    """Replace the admin approval message with a terminal note (best-effort)."""
    admin_chat_id = _pkg.RMI_ADMIN_CHAT_ID
    if not admin_chat_id or pending.admin_message_id is None:
        return
    try:
        await ctx.bot.edit_message_text(
            chat_id=admin_chat_id,
            message_id=pending.admin_message_id,
            text=f"{note}\n\n{pending.text}",
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except Exception as exc:  # best-effort; never fail the decision on this
        _pkg.logger.debug("main_approval:mark_failed err=%s", str(exc)[:120])


async def _handle_approval_edit_reply(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> bool:
    """Handle a reply to an approval message carrying corrected text.

    Returns True when the message was an approval-edit reply (consumed here);
    False otherwise so the general free-text pipeline can handle it. This is
    the narrow handler registered ahead of ``register_chat_ai`` — it only
    fires for replies to our approval messages, so it never shadows general
    free text.
    """
    message = update.message
    if message is None or not message.text:
        return False
    reply = getattr(message, "reply_to_message", None)
    if reply is None:
        return False
    replied_text = getattr(reply, "text", "") or ""
    if not replied_text.startswith(_APPROVAL_MARKER):
        return False
    # Find the pending approval whose admin message this reply targets.
    target = None
    for pending in _pending.values():
        if pending.admin_message_id == reply.message_id:
            target = pending
            break
    if target is None:
        return False
    new_text = message.text.strip()
    if not new_text:
        return False
    target.text = new_text
    target.editing = False
    _pkg.logger.info(
        "main_approval:edited ref=%s approval_id=%s",
        str(target.item.get("story_ref") or "")[:60],
        target.approval_id,
    )
    # Re-send the approval with the corrected text + fresh buttons.
    admin_chat_id = _pkg.RMI_ADMIN_CHAT_ID
    if admin_chat_id:
        try:
            sent = await ctx.bot.send_message(
                chat_id=admin_chat_id,
                text=_approval_text(new_text),
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
                reply_markup=_approval_markup(target.approval_id),
            )
            target.admin_message_id = getattr(sent, "message_id", None)
        except Exception as exc:  # fail-open: keep the edited draft pending
            _pkg.logger.warning("main_approval:resend_failed err=%s", str(exc)[:120])
    return True


def is_approval_callback(data: str) -> bool:
    """True when a callback-data string belongs to the main approval gate."""
    return data.startswith((CB_APPROVE, CB_REJECT, CB_EDIT))


async def handle_approval_callback(
    update: Update, ctx: ContextTypes.DEFAULT_TYPE, data: str
) -> bool:
    """Dispatch an approval-gate callback. Returns True when consumed."""
    if data.startswith(CB_APPROVE):
        await _approve_main_approval(update, ctx, data[len(CB_APPROVE) :])
        return True
    if data.startswith(CB_REJECT):
        await _reject_main_approval(update, ctx, data[len(CB_REJECT) :])
        return True
    if data.startswith(CB_EDIT):
        await _edit_main_approval(update, ctx, data[len(CB_EDIT) :])
        return True
    return False
